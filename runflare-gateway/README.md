# FARAST AI Gateway — Runflare

FARAST uses two small Runflare Docker services:

- `runflare-llm`: self-hosted Qwen3 1.7B Q4_K_M through the official `llama.cpp` server image. This is the local primary text model, with deliberately limited context/output and one inference slot.
- `runflare-gateway`: authenticated API router for speech recognition, TTS, and OpenAI-compatible chat. It routes ordinary text requests to local Qwen and can route explicit farast-heavy/colab-heavy requests to an optional Colab OpenAI-compatible tunnel. Groq and Gemini are disabled by product policy. Speech uses the configured Render ASR fallback; TTS uses Edge TTS then Render.

The local Qwen model is a modest general-text engine, not ChatGPT-class. It may be slow on 1.5 CPU cores and can be wrong on complex Persian instructions. Do not use it as the authority for account permissions, billing, transactions, or other deterministic application rules. High-quality cloud fallback is optional and may incur provider quotas/costs or send content outside Runflare.

## Routes

- `GET /health`: public gateway liveness only.
- `GET /ready`: configuration readiness booleans; it does not test live upstream health.
- `GET /v1/status`: authenticated readiness without returning keys.
- `POST /v1/transcribe`: Bearer-authenticated multipart `audio` + `locale`; Configured Render ASR (Groq is disabled).
- `POST /v1/synthesize`: Bearer-authenticated JSON `text`, `language`, `voice_id`; Microsoft Edge TTS first (Persian, English, Arabic), Render TTS fallback. Returns MP3.
- `POST /v1/chat/completions`: OpenAI-compatible chat request; local Qwen by default; requests with model farast-heavy or colab-heavy use the configured Colab tunnel first and fall back to local Qwen if the Colab request fails. With `stream: true`, the local Qwen route returns real SSE token chunks; if local streaming is unavailable, a configured cloud provider is wrapped in SSE after its full response arrives.

All `/v1/*` endpoints require `Authorization: Bearer FARAST_GATEWAY_TOKEN`. Keep provider keys and gateway/model-service tokens in server-side environment settings only. Never put them in browser JavaScript or Git.

## Resource design

- Qwen3-1.7B Q4_K_M weights are about 1.28 GB. The model service uses one CPU thread, one concurrent slot, a 2,048-token context and at most 160 output tokens to reduce memory pressure.
- Runflare's free plan currently lists 3 GB RAM, 1.5 CPU, 3 GB disk, Tehran cluster, two deployments, and three-month expiry. Verify the terms in the dashboard before relying on them. This is a constrained hobby/test setup, not guaranteed production hosting.
- The LLM service downloads model weights at startup and caches them in the service filesystem. The weights are not stored in GitHub. If the service is deleted after three months, the replacement downloads the model again.
- FARAST AI model weights are never downloaded to visitor browsers. Whisper/ASR inference must run on Render or an explicitly configured server-side runtime.
- Local ASR currently produces progressive text by short recording segments; it is not yet true token-by-token audio streaming. The live voice conversation path still needs a dedicated end-to-end streaming implementation (local/streaming ASR, streamed LLM tokens, incremental TTS, interruption handling).
- ASR route: Render ASR if URL/token are configured. Groq and Gemini are disabled by product policy.
- TTS fallback order: Edge TTS -> Render TTS if URL/token are configured. Edge TTS is a community client to an online speech endpoint, not a guaranteed production API.
- LLM route: local Qwen for ordinary requests; an explicit farast-heavy or colab-heavy model alias selects the configured Colab tunnel, with local Qwen as fallback. Colab is temporary and may disconnect; this gateway only checks its availability on demand and does not attempt to bypass Colab session limits.
- Runflare's free service has no backup/durability guarantee and is deleted after three months. Do not store user data, database state, or secrets on its disk.

## First deployment

1. Create two Docker services in Runflare, both using the free plan: one with build/deploy root `runflare-llm`, another with root `runflare-gateway`. The free plan allows two deployments according to the current public page; confirm this in the account.
2. In the model service, set `LLAMA_API_KEY` to a long random secret. Deploy the `runflare-llm` folder. Wait until the model service `GET /health` returns HTTP 200; the first start downloads around 1.28 GB and can take several minutes.
3. Copy the model service HTTPS URL. In the gateway service environment, set `FARAST_LOCAL_LLM_URL` to that URL, `FARAST_LOCAL_LLM_API_KEY` to the same value as `LLAMA_API_KEY`, `FARAST_LOCAL_LLM_MODEL=Qwen3-1.7B-Q4_K_M`, and `AI_PRIMARY_PROVIDER=local`.
4. Add `FARAST_GATEWAY_TOKEN` (long random secret). Configure the Render ASR/TTS URL and token variables as needed. Do not configure Groq or Gemini; those providers are disabled. To use a heavy Colab model, configure `FARAST_COLAB_LLM_URL`, `FARAST_COLAB_LLM_API_KEY`, and `FARAST_COLAB_LLM_MODEL` after you start and expose the notebook API.
5. Deploy the gateway folder and verify `/health`, `/ready`, and authenticated `/v1/status`. Then test a short Persian chat prompt and a real Persian audio recording. Do not connect production traffic until those tests pass.
6. The repository root includes `deploy-runflare-stack.ps1`. After the two services exist and their environment variables are set, run it from Windows to deploy both folders in sequence. The script resets Runflare's cached deploy root before each folder and asks you to select the model service, then the gateway service.

## Recreate after the three-month expiry

Runflare deletes expired services and their data, so no script can resurrect a deleted service or recreate it without account-owner action. To keep reactivation simple:

1. Create two replacement Docker services in Runflare and note their URLs.
2. Re-enter the saved secrets in the new model and gateway service settings (do not paste secrets into chat or commit them).
3. Update `FARAST_LOCAL_LLM_URL` in the gateway settings to the new model-service URL.
4. Run `deploy-runflare-stack.ps1`. It resets the cached deploy root between services; select the replacement model service and then the replacement gateway service. The new model service will download Qwen again; no model file needs to be copied manually.
5. Update Laravel's server-side `FARAST_AI_GATEWAY_URL` and `FARAST_AI_GATEWAY_TOKEN` if the gateway URL/token changed; update voice ASR/TTS URLs if needed.

The script makes code deployment repeatable; the Runflare dashboard service creation and secure environment entry still require the account owner.

## Local tests

```sh
python -m pip install -r requirements-dev.txt
pytest -q
uvicorn app:app --host 127.0.0.1 --port 8000
```

These tests validate API contracts and fallback ordering. They do not prove live Persian model quality, Runflare memory/latency, Render reachability, or end-to-end live voice conversation.