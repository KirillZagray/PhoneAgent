# Voximplant audio bridge — design

Date: 2026-09-09
Status: approved for planning

## Context

PhoneAgent can already dial out (`Orchestrator.handle_callback` → `VoximplantTelephonyProvider.make_call`), but the call connects and immediately fails: `send_audio`/`send_text` raise `NotImplementedError`, `_listen()` is a permanent stub, and `Orchestrator.handle_callback` refuses to place a real call at all when `supports_realtime_audio` is `False` (which it is, for every non-mock provider today). This spec covers making one real call actually work end to end for a single salon, before any multi-tenancy work. Multi-tenancy ("constructor"), LLM→TTS sentence-level streaming, Twilio/Asterisk bridges, and automated VoxEngine scenario deployment are explicitly out of scope — see "Out of scope" below.

Decisions locked in during brainstorming (see conversation, not repeated here): keep the existing provider-agnostic STT/LLM/TTS pipeline instead of Voximplant's built-in SpeechKit; build in two phases (raw echo first, then the real pipeline); end-of-utterance detection via streaming STT partial/final events (VoiceStudio), not custom VAD; the bridge is a new route inside the existing FastAPI app/process, not a separate service; test deployment target is an external server (already has a domain + HTTPS) the user controls from a separate session.

## Protocol (verified against current Voximplant docs, 2026-09-09)

This is **not** raw binary WebSocket traffic. It's JSON-over-WebSocket, symmetric in both directions:

- Scenario calls `VoxEngine.createWebSocket(url, {headers})` — an *outgoing* connection from Voximplant to us. `wss://` requires a domain (no bare IP). `headers` lets the scenario send an `X-Webhook-Secret` header we can check on connect, reusing the existing webhook-auth pattern (`api/security.py`).
- `call.sendMediaTo(ws, {encoding, customParameters})` streams caller audio to us; `ws.sendMediaTo(call, {encoding})` streams our audio back into the call. Independent calls, either direction, `stopMediaTo` to stop.
- `WebSocketAudioEncoding` options: `PCM16_8KHZ` (**default**), `PCM16_16KHZ`, `ULAW`, `ALAW`, `OPUS`. We use the default, `PCM16_8KHZ` — it is exactly `settings.sample_rate` and the PCM s16le mono contract already established for `BaseTTSProvider` in the previous audit pass. No re-encoding needed on our side for the Voximplant leg specifically.
- Wire messages (JSON text frames), same shape both directions, recommended chunk duration ~20ms:

```jsonc
// once, at stream start
{"event":"start","sequenceNumber":0,"start":{"mediaFormat":{"encoding":"audio/x-mulaw","sampleRate":8000,"channels":1},"customParameters":{"call_id":"..."}}}

// repeated, per audio chunk
{"event":"media","sequenceNumber":2,"media":{"chunk":1,"timestamp":5,"payload":"<base64 PCM bytes>"}}

// once, at stream end
{"event":"stop","sequenceNumber":777,"stop":{"mediaInfo":{"bytesSent":21100,"duration":124}}}
```

- `customParameters` set on `sendMediaTo` at the VoxEngine side surface in the `start` message's `start.customParameters` on our side — this is how we correlate the WS connection to our `call_id` (already generated in `Orchestrator.handle_callback` and passed into the scenario today via `StartCall`'s `script_custom_data`).
- A WS connection is *not* torn down automatically when the call ends — we must act on an explicit `stop` event (or our own `call_timeout_seconds` backstop), not rely on socket close alone.

## Architecture

```
Caller (PSTN)
   │
Voximplant platform ── StartCall (existing: VoximplantTelephonyProvider.make_call)
   │
VoxEngine scenario (voxengine/phoneagent_scenario.js)
   │  wss://<PUBLIC_WEBHOOK_BASE_URL>/ws/voxengine/{call_id}
   │  (headers: X-Webhook-Secret)
   ▼
PhoneAgent FastAPI process — same app, same deploy
   src/phoneagent/api/media_ws.py  (new WS route)
        │
        ▼  (Phase B only)
   per-call bridge session
        ├─ inbound PCM ──▶ STT live stream (VoiceStudio WS) ──▶ on "final" ──▶ Orchestrator._run_turn()
        └─ Orchestrator._say() output ──▶ TTS PCM ──▶ chunked into "media" events ──▶ back over the same WS
```

## Phase A — echo (prove the bridge works, nothing else)

Goal: dial the test number, say something, hear it echoed back. Isolates "does the WS bridge actually carry audio" from "does STT/LLM/TTS work" — two independently debuggable failure classes instead of one tangled one.

**`voxengine/phoneagent_scenario.js`** (new, committed as source of truth; deployed by pasting into the Voximplant scenario editor — no deploy automation in this phase):
- On call start, read `call_id` from custom data (already threaded through `StartCall`'s `script_custom_data`, see `VoximplantTelephonyProvider.make_call`).
- `VoxEngine.createWebSocket(`wss://${webhookBase}/ws/voxengine/${callId}`, {headers: [{name: "X-Webhook-Secret", value: webhookSecret}]})`.
- On the socket's `open` event: `call.sendMediaTo(ws)` and `ws.sendMediaTo(call)` (default `PCM16_8KHZ` both ways).
- On call end: `stopMediaTo` both directions, `ws.close()`.

**`src/phoneagent/api/media_ws.py`** (new):
- `@app.websocket("/ws/voxengine/{call_id}")`.
- Auth: check `X-Webhook-Secret` header against `settings.webhook_secret` at `accept()` time (reuses `require_webhook_secret`'s comparison logic from `api/security.py`, adapted for the WS handshake — FastAPI dependency injection works on WS routes the same way).
- Parse each JSON text frame; on `"event": "media"`, immediately re-emit the same `media` JSON shape (same `payload`, bumped `sequenceNumber`) back down the socket. On `"event": "stop"`, close cleanly.
- No Orchestrator involvement in this phase.

**Test:** a `pytest` WS client (via `fastapi.testclient.TestClient.websocket_connect`) sends a `start`, a few `media` frames with known base64 payloads, a `stop`, and asserts the payloads come back unchanged. This covers the route without touching Voximplant, STT, or TTS at all. The only thing that needs an actual phone call is validating the *scenario* and the network path — a separate manual check, not a pytest.

## Phase B — real pipeline

**STT streaming interface** (`providers/stt/base.py`):
- Add `supports_streaming: bool = False` on `BaseSTTProvider`, mirroring `BaseTelephonyProvider.supports_realtime_audio`.
- Add an abstract-ish live-session method, e.g. `open_live_session(*, language, sample_rate) -> LiveSTTSession`, where `LiveSTTSession` exposes `async def push_audio(pcm: bytes) -> None` and `async def events() -> AsyncIterator[STTEvent]` yielding `partial`/`final` text events. Only `VoiceStudioSTTProvider` implements this for now (against its documented `WS /v1/audio/transcriptions/stream`); all other STT providers keep `supports_streaming = False` and stay usable only through `/call/text`.

**`media_ws.py`, Phase B behavior:**
- On `start`: look up the call's `Orchestrator` (from `app.state`) and `ConversationState` (from `state_store`, already created in `handle_callback`); open a `LiveSTTSession` if `stt.supports_streaming`, else fail the call the same way `handle_callback` already refuses providers without realtime support (fail *before* answering is better, but the STT capability isn't known until this point in the current call flow — see "Open question" below).
- On each `media` frame: base64-decode, `push_audio()` into the STT session.
- On STT `final` event: this is `user_text` → `await orchestrator._run_turn(state, user_text)` (already exists, already handles tool-calling correctly per the previous audit pass — no changes needed there).
- Response text → TTS `synthesize_stream()` (already produces PCM s16le mono at `sample_rate`, per the TTS audio-contract work already done) → slice into ~20ms chunks → emit as `media` JSON frames back down the WS.
- On `stop` / WS close / `call_timeout_seconds` elapsed: close the STT live session, persist final state, let the existing `_process_call` cleanup path hang up.

**`Orchestrator` changes:** `_listen()` stops being a stub-that-returns-empty; the turn-taking loop in `_dialog_loop` becomes event-driven (driven by the bridge pushing text in, not polling). The tool-calling turn logic (`_run_turn`, `_execute_tool`) is reused as-is.

**`VoximplantTelephonyProvider`:** `send_audio`/`send_text` get real implementations (or become no-ops, since audio now flows entirely through the WS bridge rather than through `Orchestrator._say()` calling `telephony.send_audio()` — needs a small decision at plan time about whether `_say()` keeps calling `telephony.send_audio()` at all for Voximplant, or whether the bridge session bypasses that call path entirely). `supports_realtime_audio` flips to `True` only when the configured `stt_provider` also has `supports_streaming = True` — otherwise `handle_callback` keeps refusing, same fail-fast principle as today.

## Error handling

- STT live session drops mid-call → one reconnect attempt; on second failure, say "к сожалению, у меня технические проблемы, до свидания" and end the call gracefully rather than hanging silently.
- WS from VoxEngine drops without a `stop` event → treated the same as a `stop`: finalize state, let `call_timeout_seconds` / cleanup path in `_process_call` do its job (already exists).
- Malformed/unexpected JSON frame → log and drop the frame, don't crash the whole session (a stray `customEvent` or unknown `event` value is plausible per the docs).

## Testing

- Phase A: WS route round-trip test (no external services).
- Phase B: STT live-session interface tested against a fake WS server standing in for VoiceStudio (respx doesn't cover WS — use a local `websockets` test server or a hand-rolled fake); `Orchestrator._run_turn` event-driven path tested with a fake `LiveSTTSession` that emits scripted partial/final events, reusing the `ScriptedOrchestrator`-style pattern already in `tests/test_audit2.py`.
- Real phone call: manual verification only, against the user's external server + a rented Voximplant test number. Not part of CI.

## Open questions to resolve during planning (not blocking the design)

1. Exact VoxEngine JS syntax for reading `script_custom_data` and wiring `customParameters` through to the `start` message — the docs above confirm the mechanism exists but the plan should pull the precise API reference pages (`VoxEngine.customData()` or equivalent) before writing `phoneagent_scenario.js`.
2. Whether `handle_callback`'s realtime-support check should also gate on `stt.supports_streaming` at call-start time (cleaner, fails before dialing) versus `media_ws.py` discovering the mismatch after the WS connects (current sketch above) — leaning toward gating at `handle_callback` since that matches the existing fail-fast pattern and avoids a connected-then-immediately-hung-up call.
3. Whether `Orchestrator._say()` stays the single audio-output path (with `BaseTelephonyProvider.send_audio` reimplemented for Voximplant to push into the live bridge session) or whether the bridge writes directly to the WS, bypassing `_say()`/`send_audio` — the former keeps one code path for mock and real telephony; the latter is more direct. Decide at plan time.

## Out of scope

- Multi-tenancy / self-service "constructor" (separate spec, separate brainstorm).
- LLM→TTS sentence-level streaming for lower latency — still whole-utterance synthesis for now.
- Twilio / Asterisk real audio bridges (this spec is Voximplant-specific; the `supports_streaming`/`supports_realtime_audio` groundwork is reusable later).
- Automated VoxEngine scenario deployment via the Voximplant Management API — manual paste into their scenario editor for now.
- Telegram audio debug channel — user's separate idea, not part of this design.
