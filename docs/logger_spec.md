Locked. Updating the logger spec to your **human-readable console format**. No code—just exact, testable rules.

# Logger Service — Console Output (v1.1)

## Console line template (beautiful & aligned)

```
{SERVICE:<10}{LEVEL:<6}= {MESSAGE}
```

* `SERVICE` ∈ `MAIN | LOADER | KWD | STT | LLM | TTS | LOGGER`
* `LEVEL` ∈ `INFO | ERROR | WARNING | DEBUG`
* Padding:

  * `SERVICE` column width = **10** (left-aligned)
  * `LEVEL` column width = **6** (left-aligned)
* Separator: single `"= "` before the message.
* **No timestamp** on console (timestamps remain in files).
  *(Optional toggles below if you later want time or colors.)*

### Examples (exact spacing)

```
MAIN      INFO  = Starting Loader service
LOADER    INFO  = Starting KWD service
LOADER    INFO  = KWD service loaded (PID=1234, port=5003)
KWD       INFO  = Waiting for wake word
KWD       INFO  = Wake word detected (confidence 0.82)
STT       INFO  = User: what's the weather
LLM       INFO  = Assistant: It's sunny and 26°C.
```

## What gets echoed to console

Console is your live “storyline”. We echo **key events**; everything else remains in files.

**Echoed (YES):**

* Service lifecycle: `service_start`, `service_stop`, `service_error`
* Phase milestones from loader: `phase1_start/ready`, `phase2_start/ready`, `phase3_start/ready`, `warmup_done`
* KWD highlights: `kwd_started`, `wake_detected`, `kwd_stopped`
* STT highlights: `stt_started`, `stt_final_text` (shown as `User: <text>`), `stt_stopped`
* LLM highlights: `llm_stream_start` (optional), `llm_stream_end` (shown as `Assistant: <final reply>`)
* TTS highlights: `tts_stream_start`, `tts_finished`, `tts_error`
* Memory guardrail violations: `vram_warning`, `vram_error`, `vram_guardrail`

**Not echoed (NO):** verbose/debug noise like `tts_chunk`, `llm_chunk`, `health_poll`, generic `DEBUG`. These still go to `app.log`.

> Policy knob: you can expand/limit echoed events via config (below).

## Mapping from AppLogEntry → Console line

* `svc` → `SERVICE` (validated & coerced to allowed set; else `LOGGER`)
* `level` → `LEVEL` (unknown → coerced to `INFO`)
* `event`/`details` → `MESSAGE`:

  * `service_start` → `"Starting {target} service"` (if target in details; else use details as-is)
  * `service_stop` → `"Stopping {target} service"`
  * `service_error` → `"Service error: {details}"`
  * `phase*_start/ready` → `"Phase N start/ready"`
  * `wake_detected` → `"Wake word detected (confidence {%%})"`
  * `stt_final_text` → `"User: {text}"`
  * `llm_stream_end` → `"Assistant: {reply}"`
  * `tts_stream_start` → `"Speaking…"`
  * `tts_finished` → `"Playback finished"`
  * `vram_*` → human string from details, preserving numbers (e.g., `"VRAM low: used=7900 free=292 guardrail=8000"`)

If no mapping fits, print `details` verbatim.

## Config (new/updated keys in `[logger]`)

```
[logger]
console_echo = key_events        # key_events | all | none
console_show_time = false        # if true: prefix "dd-mm-yy hh:mm:ss  " before SERVICE
console_colors = false           # if true: ANSI colors (see below)
```

### Optional time prefix (if `console_show_time=true`)

```
{DD-MM-YY} {HH:MM:SS}  {SERVICE:<10}{LEVEL:<6}= {MESSAGE}
```

Example:

```
16-08-25 12:03:11  LOADER    INFO  = Phase 1 ready
```

### Optional ANSI colors (if `console_colors=true`)

* `INFO` = default
* `WARNING` = yellow
* `ERROR` = red (bold)
* `DEBUG` = dim
* `SERVICE` names can be cyan (optional).
  *(Color only affects console; files remain plain.)*

## File logging (unchanged)

* `app.log` — structured key=value, all events, truncated on start
* `memory.log` — VRAM events duplicated here, truncated on start
* `dialog_*.log` — `USER:` / `ASSISTANT:` only; new per dialog; retain 5 days

## Acceptance tests (console)

1. **Lifecycle echo**
   Send `WriteApp{svc=LOADER, level=INFO, event=service_start, details="KWD"}`
   → Console: `LOADER    INFO  = Starting KWD service`
2. **PID/port message**
   Send `WriteApp{svc=LOADER, level=INFO, event=phase3_ready, details="KWD service loaded (PID=1234, port=5003)"}`
   → Console: `LOADER    INFO  = KWD service loaded (PID=1234, port=5003)`
3. **Wake & dialog highlights**

   * `WriteApp{svc=KWD, level=INFO, event=wake_detected, details="confidence=0.82"}`
     → `KWD       INFO  = Wake word detected (confidence 0.82)`
   * `WriteDialog{role=USER, text="what's the weather"}` should also be mirrored by STT via `WriteApp{svc=STT, event=stt_final_text, details="what's the weather"}`
     → `STT       INFO  = User: what's the weather`
   * `WriteApp{svc=LLM, level=INFO, event=llm_stream_end, details="It's sunny and 26°C."}`
     → `LLM       INFO  = Assistant: It's sunny and 26°C.`
4. **Noisy events suppressed**
   `WriteApp{svc=TTS, level=DEBUG, event=tts_chunk, details="idx=12"}`
   → **No console output** (still in `app.log`).
5. **VRAM warnings visible**
   `WriteApp{svc=LOADER, level=ERROR, event=vram_guardrail, details="used=7900 free=292 guardrail=8000"}`
   → `LOADER    ERROR = VRAM low: used=7900 free=292 guardrail=8000`