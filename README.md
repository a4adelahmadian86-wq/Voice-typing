# FARAST Voice — self-hosted Persian speech service

This repository is a dedicated Python service for FARAST voice input/output. It is separate from the Laravel website and does **not** call a hosted speech-inference API. Laravel remains responsible for user authentication, permissions, AI intent handling, document commands, transactions, history, and rendering.

## Honest availability and cost constraints

- Render's Free web service sleeps after 15 minutes without inbound traffic, has 512 MB RAM / 0.1 CPU, and does not provide persistent disks. It cannot reliably run these local speech models or meet the requirement to stay active.
- `render.yaml` intentionally defines an always-on paid service and persistent disk. **Creating it can incur charges. Review the current price in Render before approving any deployment.** No service has been created by committing these files.
- A paid service avoids free-tier idle sleep, but no cloud host can promise literally zero restarts. Persistent model storage and health checks make restarts recoverable.
- This repository cannot guarantee Render is reachable from every Iranian ISP. You have already confirmed its website opens; the deployed service and model-download hosts must also be reachable from the running container.
- No keep-alive pinger or sleep-policy workaround is used.

## What is included

- FastAPI API for ASR and TTS.
- Bearer-token authentication for inference endpoints.
- Fixed Persian voice ID `farast-fa-male-v1`, with no browser or alternate-voice fallback.
- CPU-first faster-whisper ASR.
- Docker image and Render Blueprint.
- Health/readiness endpoints and contract tests.

## Deploy

1. Connect this repository to Render: https://dashboard.render.com/
2. Choose **New → Blueprint** (not Web Service), and select this repository's `main` branch.
3. Render will read `render.yaml`. Review the paid plan and disk cost shown in the UI. Do not proceed if you do not accept those charges.
4. Create a long random secret for `FARAST_VOICE_TOKEN` in Render's environment-variable prompt. Never commit a real token.
5. Confirm the service has a persistent disk mounted at `/models`.
6. Deploy. The first boot downloads model files and may take a while. Watch Logs. Model downloads require outbound access to Hugging Face; inference itself runs on this service.
7. Test `https://YOUR-SERVICE.onrender.com/health`. For actual readiness, `/ready` must return HTTP 200 with both `asr_ready` and `tts_ready` true. A failed readiness check means the models did not load; do not connect Laravel yet.
8. In Render's service settings, confirm HTTPS is enabled and the disk remains attached.

## Endpoints

- `GET /health`: public basic status; no secrets.
- `GET /ready`: provider health check; returns 503 until both models load.
- `GET /v1/status`: bearer-token-protected readiness.
- `POST /v1/transcribe`: bearer-token-protected multipart upload fields `audio`, `locale` (default `fa-IR`); response `{"text":"..." }`.
- `POST /v1/synthesize`: bearer-token-protected JSON `{"text":"سلام","language":"fa-IR","voice_id":"farast-fa-male-v1"}`; response is WAV audio.

## Laravel connection

Set the following in the Laravel host's server-side environment panel (never in JavaScript or committed files):

```dotenv
FARAST_VOICE_ASR_URL=https://YOUR-SERVICE.onrender.com/v1/transcribe
FARAST_VOICE_ASR_TOKEN=the-same-random-token
FARAST_VOICE_TTS_URL=https://YOUR-SERVICE.onrender.com/v1/synthesize
FARAST_VOICE_TTS_TOKEN=the-same-random-token
FARAST_VOICE_TTS_VOICE_ID=farast-fa-male-v1
```

Clear Laravel's config cache if the host supports it. The current Laravel integration must be checked against this service contract and tested before production.

## Model and voice notes

- ASR defaults to faster-whisper `small`, CPU/int8. Accuracy and latency for Persian must be tested with real recordings.
- TTS uses the single-speaker Persian model [Kamtera/persian-tts-male-vits](https://huggingface.co/Kamtera/persian-tts-male-vits). The pinned checkpoint filename is `best_model_91323.pth`; model files are downloaded once into persistent `/models` on startup. Verify repository filenames, model loading compatibility, and license terms for your use before production.
- `farast-fa-male-v1` is a stable configuration identifier, not proof of a particular acoustic identity until the model has successfully loaded and been evaluated. Keep the same checkpoint, config, and inference parameters after acceptance testing. Never silently switch voices.
- This handles speech recognition and speech synthesis only. It does not replace the Laravel AI/intent engine. If that engine still uses a blocked third-party LLM API, the whole assistant is not yet independent of external APIs.

## Local tests

```sh
python -m pip install -r requirements-dev.txt
pytest -q
```

These are API-contract tests, not model-quality or live-deployment tests. Model weights are intentionally not stored in Git.
