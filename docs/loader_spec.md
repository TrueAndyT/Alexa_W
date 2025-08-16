# loader\_service.py — Improved Specification (System Architecture)

**Version:** 1.3 (final)

> **What’s new in 1.3:** A coordinated **post-boot greeting**: the **Loader** starts and warms **all** services; only **after** every service is ready does **KWD** trigger **TTS** to play a greeting. When the greeting finishes, KWD immediately enters wake-word listening so you can say the key word and start transcription without delay or extra clicks.

---

## 0) Purpose & Scope

`LoaderService` is the single orchestration point that:

1. starts services **one-by-one** in a defined order,
2. continuously **monitors VRAM** and logs it,
3. **restarts** services on failure per policy,
4. **logs** all lifecycle events via `logger_service`,
5. **gracefully stops** everything on shutdown/signals,
6. ensures a **clean start** (ports/PIDs/sockets cleared),
7. applies **startup time optimizations** (pre-warm, mmapping, adaptive timeouts),
8. **signals “SYSTEM\_READY”** so **KWD** can **invoke TTS greeting only after full warm-up**, then KWD enters **immediate wake-word listening**.

Target services: `KWD`, `STT`, `LLM`, `TTS`.
Console tags: `MAIN | LOADER | KWD | STT | LLM | TTS`.

---

## 1) Architecture Overview

### 1.1 Service Adapters (uniform contract)

* `prepare_async() -> None` (optional pre-warm; **no** public port bind)
* `start() -> None` (spawn + bind ports)
* `is_ready() -> bool` (cheap; returns **bool**)
* `is_healthy() -> bool` (default → `is_ready`)
* `stop(grace_sec: int) -> None`
* Metadata: `name`, `cmd`, `env`, `workdir`, `bind_port() -> int|None`, `pid() -> int|None`

### 1.2 Loader State Machine

`INIT → PRECHECK → STARTING(service_i) → RUNNING_ALL → SYSTEM_READY → (DEGRADED|STOPPING) → STOPPED`

* **SYSTEM\_READY** (new in 1.3): entered only when **all services have passed readiness and warm-ups**. Loader emits a **one-shot event** to KWD: `on_system_ready()`.

---

## 2) Start/Stop Order (sequential gating)

* **Start:** `["kwd", "stt", "llm", "tts"]`
* **Stop:**  `["kwd", "tts", "llm", "stt"]`
* Next service starts **only after** previous returns `ready=True`.

---

## 3) Configuration Schema (YAML)

```yaml
loader:
  gpu_index: 0
  vram_poll_interval_sec: 2
  vram_log_deltas_only: true

  startup_sequence: ["kwd", "stt", "llm", "tts"]
  shutdown_sequence: ["kwd", "tts", "llm", "stt"]

  timeouts:
    kwd_ready_sec: 20
    stt_ready_sec: 45
    llm_ready_sec: 90
    tts_ready_sec: 25
  autotune_timeouts: true
  health_check_interval_sec: 5
  readiness_poll_ms: 250

  restart_policy:
    type: "on-failure"
    max_restarts: 3
    window_sec: 600
    backoff: { initial_sec: 2, factor: 2.0, max_sec: 30 }
  fail_fast_on_repeated_failures: false

  port_hygiene:
    enabled: true
    ports: { kwd: 5001, stt: 5002, llm: 5003, tts: 5004 }
    unix_sockets: []
    pid_files: []
    kill_orphans: true

  graceful_shutdown:
    per_service_grace_sec: 5
    kill_after_sec: 10

  # Load-time optimizations
  prewarm_next_service: true
  io_prewarm:
    enable_memmap: true
    pre_touch_tokenizers: true
  cuda:
    module_loading: "LAZY"  # sets CUDA_MODULE_LOADING
    torch_alloc_conf: "expandable_segments:True,max_split_size_mb:64"

  # Post-boot greeting (v1.3)
  post_boot_greeting:
    enabled: true
    text: "Hi Master. Ready to serve."
    # Controls KWD/TTS interactions around greeting
    kwd_pause_during_tts: true
    kwd_resume_after_tts_ms: 150   # safety guard before re-enable VAD

services:
  kwd:
    cmd: ["python", "-m", "services.kwd_service"]
    readiness: { type: "http", url: "http://127.0.0.1:5001/ready", expect_code: 200 }
    prepare_async: true
    threads: 2

  stt:
    cmd: ["python", "-m", "services.stt_service"]
    readiness: { type: "tcp", host: "127.0.0.1", port: 5002 }
    backend: "faster-whisper"
    compute_type: "auto"       # or float16 / int8_float16
    mmapped: true
    warmup_encoder_ms: 300

  llm:
    cmd: ["python", "-m", "services.llm_service"]
    readiness: { type: "grpc", target: "127.0.0.1:5003" }
    server: "ollama"
    mmapped: true
    kv_cache_tokens: 1024
    warmup_token: 1

  tts:
    cmd: ["python", "-m", "services.tts_service"]
    readiness: { type: "http", url: "http://127.0.0.1:5004/healthz", expect_code: 200 }
    warmup: true
    default_voice: "en_us_1"
```

---

## 4) Port & Process Hygiene (clean start)

* Unlink configured Unix sockets.
* Kill port owners via `psutil` (TERM → wait → KILL) with precise PID targeting.
* Remove stale PID files.
* Log all actions.

---

## 5) VRAM Monitoring

* Prefer `pynvml`; fallback to `nvidia-smi`.
* Poll every `vram_poll_interval_sec`; during boot, snapshot **only on stage transitions**.
* Log absolute and delta usage.

---

## 6) Health, Readiness, Restart

* **Readiness gating:** poll `is_ready()` until per-service timeout; on timeout → stop, backoff restart per policy.
* **Liveness:** every `health_check_interval_sec`; 3 consecutive fails → controlled restart.
* **Policy:** `on-failure` with capped exponential backoff; exceeding limits → mark **DEGRADED** (others continue).

---

## 7) Logging (console + files)

Follow your human-readable single-line style. Examples around greeting:

```
LOADER   INFO = All services running (warm-ups complete); entering SYSTEM_READY
KWD      INFO = Received SYSTEM_READY; initiating post-boot greeting
TTS      INFO = Speaking greeting: "Hi Master. Ready to serve."
TTS      INFO = Greeting finished (0.9s)
KWD      INFO = Wake-word detection enabled (resume after 150 ms guard)
```

(Plus standard start/stop/restart/VRAM lines as in v1.2.)

---

## 8) Graceful Stop

* On SIGINT/SIGTERM: ordered shutdown, TERM → wait → KILL, ports cleaned, threads stopped, final VRAM snapshot, `Shutdown complete`.

---

## 9) Startup-Time Optimizations (unchanged logic, summarized)

* Overlapped **prepare\_async** for next service (no ports).
* **mmapped** models (LLM/STT) + tokenizer pre-touch.
* CUDA allocator and module loading tuned once.
* **Warm-ups**: STT encoder \~300ms silent audio; LLM 1 token; TTS 0.5s dummy synth.
* **Adaptive timeouts** via boot history p95.

---

## 10) Post-Boot Greeting Orchestration (v1.3 core)

### 10.1 Definition

* **Goal:** You can **immediately** say the wake word after greeting; first STT turn should start smoothly.

### 10.2 Sequence (success path)

1. Loader completes sequential start + warm-ups for **KWD → STT → LLM → TTS**.
2. Loader enters **SYSTEM\_READY** and calls `kwd.on_system_ready(greeting_cfg)`.
3. `KWD` (if `greeting.enabled`):

   * (If `kwd_pause_during_tts=true`) temporarily **disable** wake-word/VAD to avoid self-trigger.
   * Call **TTS**: `speak(text = greeting_cfg.text, tag="boot_greeting")` (non-streaming is fine; streaming OK if TTS supports end-event).
4. `TTS` plays greeting and emits **on\_tts\_finished(tag="boot\_greeting")**.
5. `KWD` receives finish event, waits `kwd_resume_after_tts_ms` (default 150ms) to avoid tail-audio bleed, then **enables wake-word listening** and logs **READY FOR WAKE WORD**.
6. System idle state = **wake-word detection active**; user can speak the wake word **immediately**.

### 10.3 Failure/edge rules

* If TTS greeting **fails** (timeout/error), KWD **still** enables wake-word listening immediately; log a WARNING.
* If greeting **disabled**, KWD enables wake-word listening as soon as `SYSTEM_READY` arrives.
* Loader **never** triggers greeting before `SYSTEM_READY`.
* **No greeting retry loops**—avoid blocking user; only one shot per boot.

### 10.4 Timing contracts

* **Greeting duration budget:** ≤ 1.5s by default (text kept short).
* **Re-enable guard:** `kwd_resume_after_tts_ms` ensures no false trigger from TTS tail.
* **Audio routing:** if echo cancellation exists, keep it **enabled** during greeting to reduce room echo.

---

## 11) Metrics (prove improvements)

* Per-service: `prepare_async_ms`, `start_ms`, `time_to_ready_ms`, `warmup_ms`.
* Global: `first_cuda_context_ms`, `total_boot_ms`.
* Greeting: `greeting_speak_ms`, `kwd_resume_after_ms`.
* VRAM snapshots after each stage.

Sample:

```
LOADER   INFO = SYSTEM_READY: total_boot=15.1s (p95 last 5: 16.8s)
TTS      INFO = Greeting finished in 0.94s
KWD      INFO = Resume wake-word detection after 152ms
```

---

## 12) Public API (class sketch)

```python
class LoaderService:
    def __init__(self, cfg: LoaderConfig, logger: AlexaLogger): ...
    def start_all(self) -> bool: ...        # enters SYSTEM_READY on success
    def stop_all(self) -> None: ...
    def restart_service(self, name: str) -> bool: ...
    def status(self) -> dict: ...
    def vram_snapshot(self) -> dict: ...

class KWDServiceAdapter(ServiceAdapter):
    def on_system_ready(self, greeting_cfg: dict) -> None: ...
    # internally: pause_if_configured → tts.speak → on_tts_finished → enable_wake_detection()

class TTSServiceAdapter(ServiceAdapter):
    def speak(self, text: str, tag: str|None=None) -> None: ...
```

---

## 13) Error Handling Matrix (unchanged + greeting rules)

| Event                            | Detection            | Action                                                  |
| -------------------------------- | -------------------- | ------------------------------------------------------- |
| Greeting TTS fails               | timeout/error        | Log WARN; **enable KWD listening immediately**          |
| Greeting plays too long          | duration > budget    | Log WARN; enable KWD listening on finish; do not retry  |
| TTS finished but KWD not resumed | missing finish event | Force resume after `max(greeting_timeout, 2s)` watchdog |

---

## 14) Acceptance Criteria (mapped to v1.3 changes)

* Loader **never** issues greeting until **all services are ready & warmed**; **SYSTEM\_READY** is the gate.
* KWD **initiates greeting** via TTS **exactly once** per boot, then **automatically resumes** wake-word listening within \~150 ms after TTS finishes.
* User can say the wake word **immediately after greeting** and transcription starts smoothly.
* If greeting fails or is disabled, KWD **still** ends in wake-word listening.
* All steps are **logged** in the agreed console format with timings.

---

## 15) Minimal Pseudocode (boot tail)

```text
# After TTS ready & warm-ups
state = SYSTEM_READY
log("All services running (warm-ups complete); entering SYSTEM_READY")

if cfg.post_boot_greeting.enabled:
  kwd.on_system_ready(cfg.post_boot_greeting)
else:
  kwd.enable_wake_detection()

# Inside KWD
on_system_ready(cfg):
  if cfg.kwd_pause_during_tts: self.pause_wake_detection()
  tts.speak(text=cfg.text, tag="boot_greeting")

on_tts_finished(tag="boot_greeting"):
  sleep(cfg.kwd_resume_after_tts_ms / 1000.0)
  self.enable_wake_detection()
  log("Wake-word detection enabled; ready for key word")
```

---

### End of Document — v1.3