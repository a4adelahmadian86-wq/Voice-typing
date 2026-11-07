# FARAST Voice — free-tier API gateway (experimental)

This branch replaces the large local Whisper and Coqui models with a lightweight gateway:
- Speech recognition: Groq's hosted Whisper API (whisper-large-v3 by default for accuracy; switch to turbo only if latency/quota trade-offs are needed).
- Persian speech output: Microsoft's Edge online TTS through the community edge-tts client, using the fixed fa-IR-FaridNeural voice. A single short retry is attempted and MP3 bytes are validated before they are returned.
- Assistant chat: an authenticated OpenAI-compatible `POST /v1/chat/completions` proxy to Groq, with both JSON and SSE streaming responses. The upstream model is pinned by the server-side `GROQ_CHAT_MODEL`; browser-supplied model names are ignored.
- FastAPI protects the gateway with a separate bearer token. Laravel must keep both provider and gateway credentials server-side.

## Important limitations — read before deployment

- **This is a cost-minimizing experiment, not a guarantee of free unlimited service.** Groq's free tier has rate/audio limits and may change; check the account's current limits and billing dashboard.
- edge-tts accesses an online Microsoft Edge speech endpoint through a community client, not a documented guaranteed free production API. It can be blocked, throttled, or changed by the provider. Test Persian output from the actual hosting region and the site's network before relying on it.
- Render Free web services can spin down after inactivity. This avoids the prior 512 MB out-of-memory crash because no local ML weights are loaded, but **it does not provide guaranteed always-on uptime**. A cold start and third-party outage remain possible. Do not describe a 14-minute ping as a guarantee.
- Free hosting/provider availability from Iranian networks is not guaranteed. Confirm connectivity to both Groq and the speech endpoint from the actual deployed host.
- No API key belongs in JavaScript, Git, or logs.

## Deploy on Render Free

1. Open the repository in Render and create a Web Service from branch free-groq-edge-tts.
2. Select the Python runtime (not Docker). The Blueprint file describes a Free instance.
3. Add GROQ_API_KEY as a secret environment variable. Create a long random FARAST_VOICE_TOKEN; Render can generate it. The Blueprint defaults to `whisper-large-v3` for better transcription accuracy; change `GROQ_ASR_MODEL` to `whisper-large-v3-turbo` only if you prefer lower latency and accept the accuracy trade-off.
4. Deploy and verify /health, then /ready.
5. Verify Persian audio using POST /v1/synthesize and a short Persian recording using POST /v1/transcribe before changing the live Laravel settings.

## Conversational WebSocket speech stream

- `wss://YOUR-SERVICE.onrender.com/v1/stream` accepts a JSON start frame (`type=start`, short-lived signed session `token`, and `locale`) followed by continuous binary mono PCM16 little-endian audio at 16 kHz.
- Render verifies the short-lived token locally using the existing shared `FARAST_VOICE_TOKEN`. Laravel signs the token; Render does not call back to Laravel. This is required because Laravel may be running on localhost and does not need a public URL.
- Render performs server-side voice activity detection (20 ms frames, pre-roll, configurable silence endpoint, and a maximum utterance duration). After approximately 600 ms of detected silence it finalizes that utterance, trims long leading/trailing silence, queues ASR work without blocking incoming microphone frames, and emits `speech_started`, `processing`, `final`, `empty`, or `provider_error` events. A client may send `{"type":"end_utterance"}` to finalize the current phrase while keeping the authenticated WebSocket session open. The browser automatically submits each `final` transcript to Laravel; users do not press stop/send for every turn.
- Laravel remains the conversation/session owner and streams chat responses from Render. It requests Persian TTS from Render and plays response chunks while keeping the microphone stream open. The browser can interrupt playback when it detects the user beginning a new utterance (barge-in).
- This is continuous audio transport with automatic server-side turn detection, not token-by-token streaming speech recognition: Groq Whisper transcribes each completed utterance. It therefore waits for the endpointing pause plus one ASR request before the chat response begins.
- Optional Render tuning: `FARAST_VAD_END_SILENCE_MS` (default 1200), `FARAST_VAD_PREROLL_MS` (default 500), and `FARAST_VAD_MAX_UTTERANCE_MS` (default 20000).

## API contract

- GET /health: basic public health; never returns secrets.
- GET /ready: verifies required configuration is present (not a live provider check).
- GET /v1/status: bearer-token-protected configuration status.
- POST /v1/transcribe: multipart fields audio, locale; returns {"text":"..."}. Supported locales: fa-IR, en-US, ar-SA.
- POST /v1/synthesize: JSON {"text":"سلام","language":"fa-IR","voice_id":"farast-fa-male-v1"}; returns MPEG audio.

## Laravel environment

Set these on the Laravel server only:

    FARAST_VOICE_ASR_URL=https://YOUR-SERVICE.onrender.com/v1/transcribe
    FARAST_VOICE_ASR_TOKEN=the-gateway-token
    FARAST_VOICE_TTS_URL=https://YOUR-SERVICE.onrender.com/v1/synthesize
    FARAST_VOICE_TTS_TOKEN=the-gateway-token
    FARAST_VOICE_TTS_VOICE_ID=farast-fa-male-v1
    FARAST_VOICE_LOCAL_SPEECH_ENABLED=false
    FARAST_VOICE_REMOTE_BATCH_ENABLED=true
    FARAST_AI_GATEWAY_URL=https://YOUR-SERVICE.onrender.com

Set `FARAST_VOICE_ASR_TOKEN` and `FARAST_VOICE_TTS_TOKEN` to the same Render `FARAST_VOICE_TOKEN`. The chat endpoint and live WebSocket use that same shared token; no separate AI token is required. Set `VOICE_STREAM_URL=wss://YOUR-SERVICE.onrender.com` (no path suffix). Set `FARAST_VOICE_LOCAL_SPEECH_ENABLED=false`; this keeps the legacy Qwen/Sherpa/Piper stack out of the online path. The browser uses the Render WebSocket for continuous audio and server-side endpointing, while Laravel stays local and makes outbound requests to Render. Clear Laravel's config cache after changing environment values.

## Verification boundary

Dependency-free unit tests cover PCM framing, adaptive VAD thresholds, and safe quiet-speech normalization. The browser continuously sends PCM so server-side VAD can retain pre-roll and silence context. Real microphone behavior, false-positive/false-negative VAD in different rooms, Groq latency/rate limits, Persian Edge TTS playback, barge-in timing, and the full local-Laravel-to-Render round trip still require a real browser session. Do not call the end-to-end experience production-verified until those checks pass.
