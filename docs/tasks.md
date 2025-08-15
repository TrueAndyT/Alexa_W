Here’s a **step‑by‑step, atomic, verifiable task list** to implement the phased‑parallel architecture. No code; each step has a clear check.

---

# 0) Foundation & Tooling

0.1 Create repo skeleton and folders

* `services/{loader,kwd,stt,llm,tts,logger}`, `config`, `logs`, `models`
  **Verify:** Folders exist; `git status` clean.

0.2 Add `config/config.txt` (ports, VRAM≥8000, phrases, timeouts) and `config/Modelfile`
**Verify:** Open file; values match decisions (ports 5001–5006, min\_vram\_mb=8000, yes\_phrases list).

0.3 Install prerequisites (CUDA, nvidia-smi, grpcurl, PortAudio/OS audio deps, Ollama, Kokoro, Whisper weights, WebRTC AEC3 lib)
**Verify:**

* `nvidia-smi` shows GPU and **8,192 MB** VRAM.
* `grpcurl -version` prints version.
* `ollama --version` prints version (and `ollama ps` runs).

0.4 Reserve ports 5001–5006 on localhost only
**Verify:** `lsof -i :5001-5006` returns empty before services start.

---

# 1) Common gRPC & Health Contracts

1.1 Define **proto v1** for all services (+ import `grpc.health.v1`)
**Verify:** `protoc` generates stubs; builds succeed; method names match spec (Speak, Complete, Results, Events, etc.).

1.2 Implement **Health/Check/Watch** in each service stub (temporary hardcoded `NOT_SERVING`)
**Verify:**

* Start each service; `grpcurl -d '{}' -plaintext 127.0.0.1:PORT grpc.health.v1.Health/Check` returns `status:"NOT_SERVING"`.

---

# 2) LoggerService (5001)

2.1 Implement `WriteApp`, `NewDialog`, `WriteDialog` (creates `logs/app.log`, `logs/dialog_*.log`)
**Verify:**

* `grpcurl` `WriteApp` → line appended to `logs/app.log`.
* `NewDialog` returns `dialog_id` and file path exists.
* `WriteDialog` appends `USER:`/`ASSISTANT:` lines.

2.2 Health transitions to `SERVING` when log files open OK
**Verify:** Health/Check returns `SERVING`.

**Independent test:** Only logger running; you can create a dialog and write entries.

---

# 3) TTSService (5006)

3.1 Implement unary `Speak(Text)` calling Kokoro (voice `af_heart`, CUDA)
**Verify:**

* Start TTS; Health=SERVING.
* `grpcurl` `TtsService/Speak` with “Test 1 2 3” → audio plays on speakers; app log records playback.

3.2 Implement server‑stream `PlaybackEvents(DialogRef)` emitting `started|chunk_played|finished`
**Verify:** Subscribe with client; you receive events in order on a Speak call.

3.3 Implement **client‑stream** `SpeakStream(stream LlmChunk)` with chunk queue & underrun guards
**Verify:**

* Use a small internal test client or temporary unary wrapper to simulate chunks; PlaybackEvents show continuous playback; no underruns logged.
* **Pass** if first audio ≤150 ms from first chunk (check timestamps in app log).

**Independent test:** TTS alone: Speak works and events stream.

---

# 4) LLMService (5005)

4.1 Bridge to Ollama **streaming** (Llama‑3.1‑8B‑Q4; reads `config/Modelfile`)
**Verify:**

* Health=SERVING.
* `grpcurl` `LlmService/Complete` with text “hello” returns **server‑streamed** chunks ending with an `eot` flag.

4.2 Log full final response to dialog log via LoggerService
**Verify:** After a Complete call with dialog\_id, `dialog_*.log` ends with `ASSISTANT: <full text>`.

4.3 First‑token latency (warm) ≤ 800 ms
**Verify:** Compare app log timestamps: request received → first chunk emitted.

**Independent test:** LLM alone: stream to console; measure latency.

---

# 5) STTService (5004)

5.1 Audio pipeline with **WebRTC AEC3**, device probe, Whisper `small.en` (CUDA)
**Verify:** Health=SERVING only when mic is opened and AEC3 active (report in app log).

5.2 Built‑in **VAD finalize** at \~2s silence → emit `SttResult{final:true, text}`
**Verify:**

* Start `Results(dialog_id)` stream, then talk; after you stop, within \~2s a single final result arrives.
* App log shows VAD timings.

5.3 Error prompt hook (for later integration): on internal error, emit status to loader and be ready to trigger TTS canned phrase
**Verify:** Force an error (e.g., invalid device id in config) → Health NOT\_SERVING and clear error log.

**Independent test:** STT alone: print results; ensure **one** final line per utterance.

---

# 6) KWDService (5003)

6.1 openWakeWord with `models/alexa_v0.1.onnx`, threshold **0.6**, **1s cooldown**
**Verify:** Health=SERVING when mic path is configured and model loaded.

6.2 Server‑stream `Events()` emitting `WAKE_DETECTED{confidence, ts, dialog_id? optional}`
**Verify:** Speak wake word; exactly one event within 1s; repeated utterances within cooldown yield no duplicate.

**Independent test:** KWD alone: connect Events and capture confidences.

---

# 7) LoaderService (5002) — Phased‑Parallel Orchestrator

7.1 Process supervisor: spawn child processes; map PIDs
**Verify:** `GetPids` returns PIDs; `ps` shows processes alive.

7.2 **Phase 1 (parallel):** start **TTS + LLM**, wait for **both** Health=SERVING within `parallel_phase_timeout_ms` (8s), recheck VRAM ≥ 8000
**Verify:** App log shows phase start/finish; Health watcher confirms both SERVING; `nvidia-smi` memory change visible.

7.3 **Phase 2:** start **STT**, wait SERVING
**Verify:** App log marks phase; Health shows SERVING.

7.4 **Phase 3:** start **KWD**, wait SERVING
**Verify:** App log marks phase; KWD SERVING.

7.5 Warm‑up greeting: call `TTS.Speak(dialog.warmup_greeting)` once
**Verify:** You hear “Hi, Master!”; app log has `warmup_done`.

7.6 Health Watch & restarts with backoff (1s/3s/5s; max 3/min/service)
**Verify:** Kill LLM PID; loader restarts it with backoff; logs show sequence and successful SERVING.

7.7 Admin RPCs: `StartService`, `StopService`, `GetPids`
**Verify:** Stop and restart STT via RPC; Health reflects transitions.

**Independent test:** Run **only** loader + health mocks (or real services) and validate phase gates & restarts.

---

# 8) Cross‑Service Wiring (Dialog Loop)

8.1 Wake handling: on KWD event → random phrase from `yes_phrases` → call `TTS.Speak` → `STT.Start(dialog_id)`; disable KWD during dialog
**Verify:** Say wake word; hear a **random** phrase; KWD Health may remain SERVING but emits no events until dialog ends (or KWD disabled flag set). App log shows `dialog_started`, `kwd_disabled`.

8.2 STT→LLM→TTS streaming bridge:

* On `user_text`, call `LlmService/Complete` and pipe chunks to `TtsService/SpeakStream`
  **Verify:** First audio ≤150 ms after first LLM chunk; PlaybackEvents continuous; no underruns in app log.

8.3 Dialog follow‑up timer (4s): start on **TTS finished**; if speech in <4s → continue same dialog; if silence ≥4s → end dialog: `stt.Stop`; re‑enable KWD
**Verify:**

* Speak a follow‑up within 4s → new turn (turn++), same dialog\_id.
* Stay silent → dialog ends; new wake is required next time.

8.4 Error prompts:

* STT error → TTS “Sorry, I didn’t catch that.”
* LLM error → TTS “Sorry, I had a problem.”
* TTS error → “Audio error.”
  **Verify:** Induce each error (kill service mid‑turn); confirm canned prompt plays and 4s window remains; logs record root cause and recovery.

---

# 9) Logging & Auditing

9.1 `app.log` resets on each run; structured lines with svc/event/ts
**Verify:** Delete/rotate on boot; new file created; entries for phases, restarts, timers, errors.

9.2 `dialog_{timestamp}.log` per dialog; only `USER:`/`ASSISTANT:` lines in order
**Verify:** Trigger a full dialog with 2 turns; file has 2× `USER:` + 2× `ASSISTANT:` lines, in order.

9.3 Metrics (optional v1): counters for detections/turns/errors in app log
**Verify:** Counts increase as expected.

---

# 10) Security & Config Validation

10.1 Bind **127.0.0.1** only; refuse external interfaces
**Verify:** From another host, ports are unreachable; `ss -ltnp | grep 127.0.0.1:500` shows loopback.

10.2 Config validation at service start; explicit error on invalid keys/values
**Verify:** Put an invalid key in `[stt]`; service logs error and stays NOT\_SERVING.

10.3 VRAM guardrail enforced at loader boot and between phases
**Verify:** Temporarily reduce guardrail to an impossible value (e.g., 64000) → loader logs FATAL and aborts Phase 1.

---

# 11) Independent Service Test Matrix (quick checklist)

* **Logger:** WriteApp/NewDialog/WriteDialog succeed; files exist.
* **TTS:** Health SERVING; Speak plays audio; PlaybackEvents stream.
* **LLM:** Health SERVING; Complete streams chunks with `eot`.
* **STT:** Health SERVING; VAD finalizes \~2s; Result emitted once.
* **KWD:** Health SERVING; single wake event ≥0.6; 1s cooldown works.
* **Loader:** Phased‑parallel sequence in logs; restarts on crash; warm‑up greeting played.

---

# 12) End‑to‑End Acceptance Runs

12.1 **Cold boot E2E:** From `main`, reach Idle; hear greeting
**Verify:** Phases in app log; greeting spoken once.

12.2 **Dialog happy path:** wake → user asks → streamed reply → follow‑up <4s → second turn → silence ≥4s → KWD re‑enabled
**Verify:** No double wake; dialog log lines present; timings meet targets (200 ms wake→phrase; ≤800 ms first token; ≤150 ms first audio).

12.3 **Failure recovery:** kill TTS mid‑stream; canned prompt plays after restart; 4s window honored
**Verify:** Logs show restart backoff and recovery; dialog ends cleanly if silence.

---

# 13) Performance Tuning (measure & adjust)

13.1 Measure latencies and buffer levels from logs (wake→phrase, first token, first audio)
**Verify:** All within targets; if not, raise low‑watermark or chunk sizes accordingly.

13.2 Check GPU/CPU usage during Phase 1 model loads and dialog
**Verify:** No OOM; VRAM usage stable; adjust stagger inside Phase 1 if needed.

---

# 14) Documentation & Ops

14.1 README: runbook, per‑service test commands, troubleshooting (ports, devices, VRAM)
**Verify:** Another machine can follow doc to reproduce all green checks.

14.2 Log retention & rotation settings validated (size and keep count)
**Verify:** Generate logs to force rotation; older files trimmed as configured.

---

If you want, I can turn this into **issue tickets** (one per atomic task) with checkboxes and exact grpcurl invocations for each verification.

