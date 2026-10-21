# FARAST AI Gateway — Runflare

FARAST's browser talks only to the authenticated Laravel application. Laravel calls this gateway server-to-server; no model weights, tokenizer, Whisper package, or AI runtime are downloaded to visitors' devices.

## Production inference design

- `runflare-gateway`: authenticated API router, request limits, provider selection and fallback.
- Cloudflare Workers AI: preferred server-side text and speech-recognition provider when configured. General requests use `@cf/qwen/qwen3-30b-a3b-fp8`; explicit `farast-heavy` requests use `@cf/meta/llama-3.3-70b-instruct-fp8-fast`; audio transcription uses `@cf/openai/whisper-large-v3-turbo`.
- `runflare-llm`: self-hosted Qwen3 1.7B Q4_K_M on Runflare as the fallback for text generation if Cloudflare is unavailable or its free daily allocation is exhausted.
- Render ASR/TTS: optional fallback only; the URLs are intentionally blank by default because the repository does not prove that a Render service is deployed. Render's free web services spin down after 15 minutes without inbound traffic and can take about a minute to wake; the repository's recommended persistent speech deployment uses a paid 2 CPU / 8 GB RAM plan. Do not configure Render unless you have verified the endpoint and accepted its current cost.
- Persian speech synthesis: server-side Edge TTS first, Render TTS fallback. Edge TTS is a community client to an online speech endpoint, not a guaranteed production API.

**Free quota, stated honestly:** Cloudflare Workers AI includes 10,000 Neurons per day at no charge. The allocation is shared across models and resets daily; once it is exhausted, inference requests fail until reset unless the account has a paid plan. See [Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) and [model limits](https://developers.cloudflare.com/workers-ai/platform/limits/). This can provide materially stronger model quality than a 1.7B CPU model for modest traffic, but it is not unlimited free production capacity.

**Why Colab is not the production inference server:** Google says free managed Colab runtimes may be terminated when used to bypass the notebook UI for web-based content generation and prohibits distributed workers without a positive compute-unit balance. Therefore no keep-alive loop or fake uptime promise is implemented. Colab can still be used by the developer for evaluation and experimentation, but the public site must not depend on a free Colab notebook staying alive. See the [official Colab FAQ](https://research.google.com/colaboratory/faq.html).

## Routes

- `GET /health`: public gateway liveness only.
- `GET /ready`: configuration readiness booleans; it does not test live upstream inference.
- `GET /v1/status`: authenticated configuration status without secrets.
- `POST /v1/transcribe`: Bearer-authenticated multipart `audio` + `locale`; Cloudflare Whisper first, Render ASR fallback.
- `POST /v1/synthesize`: Bearer-authenticated JSON `text`, `language`, `voice_id`; server-side Edge TTS first, Render fallback. Returns MP3.
- `POST /v1/chat/completions`: OpenAI-compatible chat; Cloudflare Qwen3-30B-A3B first, Cloudflare Llama 3.3 70B for explicit `farast-heavy` requests, Runflare Qwen 1.7B fallback. SSE streaming is forwarded from the selected upstream where supported; if upstream streaming fails, the gateway retries using a complete response and wraps it in SSE.

All `/v1/*` endpoints require `Authorization: Bearer FARAST_GATEWAY_TOKEN`. Keep account IDs and provider keys in server-side environment settings only. Never put them in browser JavaScript or Git. The Laravel-to-gateway token must be distinct from the Cloudflare API token.

## Resource design

- Runflare's published free hosting currently lists 3 GB RAM, 1.5 CPU, 3 GB disk, two deployments, and a three-month term. Confirm availability and terms in the account dashboard before relying on them. This is a constrained hobby/test allocation, not a production SLA.
- The Qwen3-1.7B Q4_K_M model file is about 1.28 GB. The model service uses one CPU thread, one concurrent slot, a 2,048-token context and bounded output to reduce memory pressure.
- The Runflare model service downloads weights on the server at startup. These weights are never delivered to visitors. If the service is deleted, the replacement downloads the model again.
- The Render free tier spins down after 15 minutes idle and may restart at any time; use it only as a fallback, not as the only service users depend on.
- The gateway reports which upstream answered using `X-Farast-AI-Provider`. If Cloudflare reaches its daily limit and the local model answers instead, the provider header identifies the lower-capacity fallback. This is not presented as equivalent model quality.
- This gateway does not store conversations or audio itself. The Laravel app remains responsible for user authorization, quotas, audit records and persistence.

## First deployment

1. Create the **gateway** Docker service in Runflare with build/deploy root `runflare-gateway`. The gateway is the first service to deploy because Cloudflare Workers AI is the primary inference provider.
2. In the gateway service environment, set `FARAST_GATEWAY_TOKEN` to a long random secret, plus `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. The Cloudflare token must have Workers AI Read/Write permissions. Do not commit these secrets.
3. Deploy the gateway. Verify `/health` returns HTTP 200, `/ready` reports configured readiness, and authenticated `/v1/status` shows Cloudflare configured. Then send a real authenticated `/v1/chat/completions` request and transcribe a real Persian recording through `/v1/transcribe`.
4. Optionally create a second Docker service for the local fallback with build root `runflare-llm`. Add `LLAMA_API_KEY` as a long random secret and deploy. The first start downloads roughly 1.28 GB from Hugging Face and may fail if outbound access or free resources are unavailable.
5. Once the local model `GET /health` returns HTTP 200, set in the gateway environment:
   - `FARAST_LOCAL_LLM_URL=https://YOUR-LLM-SERVICE-DOMAIN`
   - `FARAST_LOCAL_LLM_API_KEY` to the exact same secret as `LLAMA_API_KEY`
   - `FARAST_LOCAL_LLM_MODEL=Qwen3-1.7B-Q4_K_M`
6. In Laravel's server environment set `FARAST_AI_GATEWAY_URL` to the gateway's HTTPS base URL and `FARAST_AI_GATEWAY_TOKEN` to the exact same secret as `FARAST_GATEWAY_TOKEN`. Clear Laravel's cached configuration, then test `/editor/voice/status`, `/assistant/voice/status`, `/assistant/transcribe`, `/assistant/speak`, and `/assistant/chat` against the live deployment.
7. Configure Render ASR/TTS only if you have deployed and tested that service and explicitly accepted its cost. The sample environment intentionally leaves those URLs blank.

The repository root includes `deploy-runflare-stack.ps1` to deploy the gateway first and the optional local model fallback second. Pass `-SkipLocalFallback` to deploy only the gateway. Account creation, dashboard service setup, Cloudflare token creation and secret entry still require the account owner; this repository change cannot perform those account-side actions.
## Local tests

```sh
python -m pip install -r requirements-dev.txt
pytest -q
uvicorn app:app --host 127.0.0.1 --port 8000
```

These tests validate API contracts, routing and fallback behavior. They do not prove live Persian model quality, Cloudflare account configuration, Runflare latency/memory, Render reachability, or full live voice conversation.
