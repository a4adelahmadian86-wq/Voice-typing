# FARAST Voice — free-tier API gateway (experimental)

This branch replaces the large local Whisper and Coqui models with a lightweight gateway:
- Speech recognition: Groq's hosted Whisper API (whisper-large-v3 by default for accuracy; switch to turbo only if latency/quota trade-offs are needed).
- Persian speech output: Microsoft's Edge online TTS through the community edge-tts client, using the fixed fa-IR-FaridNeural voice. A single short retry is attempted and MP3 bytes are validated before they are returned.
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

## Live WebSocket speech stream (experimental)

- `wss://YOUR-SERVICE.onrender.com/v1/stream` accepts a JSON start frame (`type=start`, short-lived Laravel `token`, and `locale`) followed by binary mono PCM16 little-endian audio at 16 kHz. It transcribes disjoint short WAV chunks through the configured Groq Whisper model and sends `ready`, `final`, `provider_error`, or `error` JSON events.
- Set `FARAST_APP_URL` to the canonical HTTPS origin of the Laravel site and set `FARAST_VOICE_GATEWAY_SECRET` to the same random secret configured as `services.voice_stream.gateway_secret` in Laravel. These must be secrets in the hosting dashboard, not committed values. The gateway validates each session token against Laravel's signed `/editor/voice/stream-config` endpoint before processing audio.
- Set Laravel `services.voice_stream.url` to the service origin (no `/v1/stream` suffix) and configure `services.voice_stream.gateway_secret` with the same secret. The browser app appends `/v1/stream` automatically.
- The stream currently uses chunked Whisper requests, not a native token-by-token streaming ASR model. It is an experimental live transport, and real-device latency, pause detection, and end-to-end voice turns must be validated before describing it as production-ready.

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

Both ASR and TTS currently use the same gateway token. Clear Laravel's config cache after changing environment values if the hosting platform requires it.

## What remains unverified

The code contract can be tested without provider credentials, but live Groq transcription, Edge TTS Persian audio, Iranian network reachability, Render deployment, and Laravel end-to-end playback have not yet been verified. Do not merge to main until those checks pass.
