# IMC v1.2 — No Controller. STT‑Led Dialog Loop.

## LoaderService (5002) — unchanged intent

* **Only**: start, stop, restart on failure, phased‑parallel startup, one‑time warm‑up (“Hi, Master!”).
* **No** dialog/timer/bridging logic.

## Who does what (final)

* **KWD (5003):** Detects wake → (1) speak randomized confirmation via TTS, (2) starts STT, (3) disables itself for dialog (stops), relying on STT to re‑enable it.
* **STT (5004):** Owns the **entire dialog loop**:

  * Capture + **Whisper VAD** finalize (\~2s silence) → emit **one** `user_text`.
  * On each `user_text`: call **LLM/Complete** (server‑stream), **pipe chunks to TTS/SpeakStream** immediately.
  * Subscribe to **TTS/PlaybackEvents** to know when speech finished → start **4s follow‑up timer** and listen.
  * If speech begins <4s → keep dialog (turn++), repeat.
  * If silence ≥4s → **end dialog**: stop itself; **re‑enable KWD** (`Kwd.Start()`).
* **LLM (5005):** Streams chunks; no dialog logic.
* **TTS (5006):** Speaks unary/stream; emits PlaybackEvents; no dialog logic.
* **Logger (5001):** Logs; issues dialog ids; no dialog logic.

## Minimal RPCs you already have (no new services)

* `KwdService`: `Configure/Start/Stop/Events`
* `SttService`: `Configure/Start/Stop/Results` (+ STT **also** acts as a gRPC **client** to LLM/TTS/KWD)
* `LlmService`: `Configure/Complete (stream)`
* `TtsService`: `Configure/Speak/SpeakStream (client‑stream)/PlaybackEvents (server‑stream)`
* `LoggerService`: `NewDialog/WriteDialog/WriteApp`
* All implement `grpc.health.v1.Health`.

## Tiny proto clarifications (keep names; just usage rules)

* **No new methods.** Behavior shift only:

  * KWD, after emitting internal detection, **actively calls**:

    * `Tts.Speak(TtsText{text=random(yes_phrases)})`
    * `Stt.Start(DialogRef)` (and **then** `Kwd.Stop()` to disarm during dialog)
  * STT, on finalize:

    * `Llm.Complete(UserQuery)` (consume stream)
    * `Tts.SpeakStream(stream LlmChunk)` (pipe through)
    * Subscribe `Tts.PlaybackEvents(DialogRef)` to get `finished` → start 4s follow‑up timer
    * On dialog end: `Stt.Stop(DialogRef)`; `Kwd.Start()` to re‑arm wake word

## Data flow (final, no controller)

```
Idle:
  KWD(ON) listening

Wake:
  KWD --detect(≥0.6)--> 
      TTS.Speak(random yes_phrase)
      STT.Start(dialog_id=Logger.NewDialog()) 
      KWD.Stop()

Turn:
  STT --final(user_text)--> LLM.Complete(stream) --chunks--> TTS.SpeakStream
  TTS --> PlaybackEvents(finished) --> STT

Follow‑up:
  STT starts 4s timer + listens:
    if speech<4s -> next STT final -> (loop Turn)
    if silence≥4s -> STT.Stop(); KWD.Start()  (back to Idle)
```

## Logging responsibilities (unchanged names; moved to the right place)

* **KWD:** `wake_detected`, `kwd_started`, `kwd_stopped`, `cooldown_suppressed`
* **STT:** `stt_started`, `stt_final_text`, `stt_stopped`, `dialog_started`, `dialog_turn`, `dialog_followup_start`, `dialog_ended`
* **LLM:** `llm_stream_start`, `llm_stream_end`, `llm_error`
* **TTS:** `tts_stream_start`, `tts_chunk`, `tts_finished`, `tts_underrun`, `tts_error`
* **Logger:** `dialog_file_created`, `dialog_write`
* **Loader:** phases + restarts only

## Acceptance criteria (tight)

1. **Idle→Wake:** Wake (≥0.6) triggers **one** randomized phrase; KWD stops; STT starts; `dialog_*` file created.
2. **Turn:** STT final after \~2s silence → LLM streams → TTS begins audio ≤150 ms after first chunk; `ASSISTANT:` line logged at `eot`.
3. **Follow‑up:** On `tts_finished`, STT opens 4s window:

   * speech <4s → next turn (same dialog\_id, turn+1)
   * silence ≥4s → STT stop; KWD start; `dialog_ended` logged.
4. **Crash handling:** If any service dies, **loader** restarts it. STT reacts:

   * LLM error → TTS says “Sorry, I had a problem.”; 4s window remains.
   * TTS error → “Audio error.”; 4s window remains.
   * STT error → “Sorry, I didn’t catch that.”; keep window (or end if unrecoverable).
5. **No controller process exists.** Only the six services + loader.

## Atomic tasks (only the deltas from your task list)

* **KWD:** implement internal action chain on detection: `TTS.Speak` → `Logger.NewDialog` → `STT.Start` → `KWD.Stop`. (Verifiable with grpcurl + logs.)
* **STT:** implement:

  * LLM stream bridge to TTS,
  * subscribe to `TTS.PlaybackEvents`,
  * 4s follow‑up timer + speech‑start detection,
  * end‑of‑dialog: `STT.Stop` + `KWD.Start`. (Verifiable by timing logs and dialog file contents.)
* **Loader:** ensure no dialog events in loader logs; only phases/restarts/warmup. (Grepping `app.log` proves it.)

This satisfies your original design: **KWD→STT→LLM→TTS→STT (timer)** with **loader doing only lifecycle**.
