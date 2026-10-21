# FARAST Local LLM — Runflare fallback

This is an **optional server-side fallback** for the authenticated `runflare-gateway`. Cloudflare Workers AI is the preferred high-quality inference route; this small Qwen model is used only if the hosted model is unavailable or the free daily allocation has been exhausted.

## Model and constraints

- Model: `ggml-org/Qwen3-1.7B-GGUF:Q4_K_M`
- Weight download: about 1.28 GB.
- License: Apache-2.0 on the published model card.
- Resource limits: one inference slot, one CPU thread, 2,048-token context and a 160-token output cap. Qwen3 thinking mode is disabled to avoid slow hidden reasoning.
- Expected limitation: 1.5 CPU cores are slow for generation, and a 1.7B model can make mistakes on complex instructions, long documents, tool use, or nuanced Persian. It must not be treated as the sole source of truth for billing, permissions, account actions, or other deterministic business logic.

The model weights are downloaded by `llama.cpp` on the server at startup and cached in the service filesystem. They are never delivered to website visitors. If Runflare deletes the service at the three-month expiry, the replacement downloads the model again.

## Deploy

1. In Runflare, create a Docker service for this fallback with build root `runflare-llm`. This service is optional if only Cloudflare Workers AI is to be used.
2. Add `LLAMA_API_KEY` as a long random secret in the service environment.
3. Deploy. The first start downloads roughly 1.28 GB from Hugging Face and may take several minutes. Wait for `GET /health` to return HTTP 200.
4. In the gateway service environment, set:
   - `FARAST_LOCAL_LLM_URL=https://YOUR-LLM-SERVICE-DOMAIN`
   - `FARAST_LOCAL_LLM_API_KEY` to the exact same secret as `LLAMA_API_KEY`
   - `FARAST_LOCAL_LLM_MODEL=Qwen3-1.7B-Q4_K_M`
5. In the gateway environment also set `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, and `FARAST_GATEWAY_TOKEN`. Cloudflare is the preferred provider; this local service is only the fallback.

## Resource and privacy notes

- Do not increase the context or parallel slots on the free plan without measuring memory; both can cause out-of-memory restarts.
- If this service cannot download weights or is sleeping/unavailable, Cloudflare remains the primary route. If Cloudflare's daily allocation is also exhausted, text generation returns a service error instead of pretending the low-quality fallback worked.
- This service is not suitable for high concurrent traffic or low-latency voice streaming. Speech recognition runs through the server-side gateway, not the browser.
- Runflare's free service is time-limited and non-production. Keep source, deployment scripts, and secrets in their appropriate locations; never put keys in GitHub.
