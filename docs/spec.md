Got it — here’s the reworked architect’s pack with **phased‑parallel startup**.

# System Architecture (phased‑parallel)

* **Processes (localhost, separate):** `main`, `loader`, `kwd`, `stt`, `llm`, `tts`, `logger`.
* **Ports:** 5001 logger, 5002 loader, 5003 kwd, 5004 stt, 5005 llm, 5006 tts.
* **IPC:** gRPC (domain APIs) + `grpc.health.v1.Health` per service.
* **Security:** 127.0.0.1 only; no external calls.
* **Resources:** VRAM guardrail `min_vram_mb=8000` at boot and between phases.

## Startup Mode: Phased‑Parallel (loader‑controlled)

**Phase 1 (parallel):** start **TTS** and **LLM** together
→ wait until both `SERVING` and VRAM check passes.
**Phase 2 (single):** start **STT** (claims mic, AEC3 setup).
**Phase 3 (single):** start **KWD** (armed only when STT is ready).
**Warm‑up:** when Phase 3 done, TTS says **“Hi, Master!”**, system enters **Idle**.

**Loader config (additions)**

* `startup_mode=phased_parallel`
* `parallel_phase_timeout_ms=8000`
* `audio_device_probe=strict`  (verify output before STT)
* `gpu_mem_min_mb=8000` (same as system guardrail; rechecked per phase)

**Rationale**

* Cuts boot time vs sequential, but avoids early false wakes, audio device contention, and VRAM spikes from loading three models at once.

---

# Data Flow (Level‑1, unchanged at runtime)

```
[Mic] --> (kwd) --WAKE{confidence}--> (loader) -> (logger: app)
           |
(loader) --TTS.say(yes_phrase)------> (tts) --> [Speakers]
(loader) --stt.Start(dialog_id)-----> (stt)
(loader) --logger.NewDialog---------> (logger) -> dialog_id

(stt) --USER{final text}-----------> (llm) --stream{chunks}--> (tts) --> [Speakers]
  \-------------------------------> (logger: dialog USER)
(llm) --final_response-------------> (logger: dialog ASSISTANT)
(tts) --PlaybackEvents-------------> (loader)

(loader) --4s follow-up timer------+
   | speech<4s -> loop STT (same dialog)
   | silence≥4s -> end dialog → enable KWD
   +-> (logger: app/dialog events)
```

---

# Runtime Sequences (with phases)

## 1) Startup & Warm‑Up (phased‑parallel)

1. `main` launches `loader`.
2. **Phase 1 (parallel):** loader starts **tts+llm** → waits Health `SERVING` for both (≤ `parallel_phase_timeout_ms`), rechecks VRAM ≥ 8000 MB.
3. **Phase 2:** start **stt** → Health `SERVING` (mic + WebRTC AEC3).
4. **Phase 3:** start **kwd** → Health `SERVING`.
5. `loader` triggers **warm‑up greeting** via TTS → system **Idle** (KWD on).

Failure rules:

* If any phase misses timeout or Health `NOT_SERVING`, loader logs FATAL and aborts; no KWD armed.

## 2) Wake → Turn → Follow‑Up (same logic)

* KWD detects (≥0.6; 1s cooldown) → loader randomizes phrase from `yes_phrases`, calls `tts.Speak(...)`, starts `stt`.
* STT finalizes (\~2s silence) → `user_text` → LLM streams → TTS streams/plays.
* On TTS finish: 4s follow‑up timer; speech continues dialog; silence ends → KWD re‑enabled.

## 3) Failure Handling (phase‑aware)

* If **tts** or **llm** fails during Phase 1: rollback the pair, retry Phase 1 with backoff.
* If **stt** fails Phase 2: stop stt, keep tts/llm up, retry Phase 2.
* If **kwd** fails Phase 3: don’t arm wake; retry Phase 3.
* Mid‑dialog faults: canned error prompts; 4s window preserved; loader restart with capped backoff (3/min escalation).

---

# Modules List (by process)

## Common (shared)

* `config_loader` (INI + validation + phase settings)
* `health_client` (Check/Watch helpers)
* `logging_client` (RPC to logger)
* `ids` (dialog/turn IDs)
* `audio_io` (device probe; resampling; output readiness check)
* `gpu_monitor` (VRAM sampling for guardrail)
* `retry_backoff` (1s/3s/5s)
* `proto stubs` (all services + Health)

## main

* `bootstrap` (env checks, spawn loader)
* `signal_handler` (graceful stop)
* `fatal_guard` (last‑resort logger)

## loader

* `phase_controller` (**phased‑parallel orchestration**)
* `proc_supervisor` (spawn/monitor child PIDs)
* `health_watcher` (aggregate phase gating; VRAM rechecks)
* `dialog_manager` (Idle/Dialog, 4s timer, turn counter)
* `event_bus` (subscribe KWD/STS/LLM/TTS streams)
* `warmup` (trigger greeting post Phase 3)
* `admin_api` (Start/Stop/GetPids; Health)

## kwd

* `wake_engine` (openWakeWord, threshold 0.6, cooldown 1s)
* `events_api` (server‑streaming `Events()`)
* `health_provider` (SERVING only after mic path non‑conflicting)
* `logging_adapter`

## stt

* `capture_pipeline` (WebRTC AEC3; device claim)
* `vad_finalize` (Whisper VAD \~2s)
* `whisper_cuda` (small.en)
* `results_api` (server‑streaming)
* `control_api` (Start/Stop)
* `logging_adapter`

## llm

* `ollama_client` (stream bridge)
* `modelfile_loader` (SYSTEM """...""")
* `completion_api` (server‑streaming chunks)
* `logging_adapter`

## tts

* `kokoro_client` (CUDA, af\_heart)
* `stream_queue` (low‑latency buffer; underrun guards)
* `speak_api` (unary + client‑streaming)
* `playback_events` (server‑streaming)
* `logging_adapter`

## logger

* `app_log_writer` (reset on start; rotation)
* `dialog_log_manager` (`NewDialog`, `WriteDialog`)
* `rpc_sink` (WriteApp/WriteDialog)
* `health_provider`

---

# Config Keys (additions/highlights)

* `[loader]`

  * `startup_mode=phased_parallel`
  * `parallel_phase_timeout_ms=8000`
  * `restart_backoff_ms=1000,3000,5000`
  * `health_interval_ms=2000`
* `[system]`

  * `min_vram_mb=8000`
* `[stt]`

  * `aec_enabled=true`
  * `aec_backend=webrtc_aec3`
* `[kwd]`

  * `confidence_threshold=0.6`
  * `cooldown_ms=1000`
  * `yes_phrases=Yes?;Yes, Master?;Sup?;Yo`

---

# Risks & Mitigations (specific to phased‑parallel)

* **GPU contention during Phase 1:** Stagger inside phase if VRAM dips (start TTS, then LLM after TTS `SERVING` but still count as Phase 1).
* **Audio device race:** `audio_device_probe=strict` ensures output ready before STT claims mic.
* **Phase deadlock:** `parallel_phase_timeout_ms` + rollback/retry with capped backoff.

This keeps your deterministic readiness while trimming boot time. If you want, I’ll align the **acceptance criteria** block to explicitly reference phases for verification.

