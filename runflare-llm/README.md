# FARAST Local LLM — legacy optional component

This folder is retained for compatibility and historical work. It runs a quantized Qwen3 1.7B model using the official `llama.cpp` server image, but **Qwen3 is not the selected model for FARAST** and this service is not part of the current Runflare deployment path. The default deploy script now deploys only `runflare-gateway`, which forwards to the Cloudflare Worker. Do not create or start this LLM service unless the model is explicitly re-evaluated and approved.

## Why this model

- Model: `ggml-org/Qwen3-1.7B-GGUF:Q4_K_M`
- Weight download: about 1.28 GB.
- License: Apache-2.0 on the published model card.
- Resource limits: one inference slot, one CPU thread, 2,048-token context and a 160-token output cap. Qwen3 thinking mode is disabled to avoid slow hidden reasoning. This intentionally favors keeping the service alive over parallel throughput.
- Expected limitation: 1.5 CPU cores are slow for generation, and a 1.7B model can make mistakes on complex instructions, long documents, tool use, or nuanced Persian. It must not be treated as the sole source of truth for billing, permissions, account actions, or other deterministic business logic.

The model weights are downloaded by `llama.cpp` when the container starts and cached in the service filesystem. The weights are not committed to GitHub. If Runflare deletes the service at the three-month expiry, the new service downloads the model again.

## Legacy deployment (not selected)

The instructions below describe the previous optional Qwen deployment. They are retained for reference only and must not be used for the current FARAST architecture.
2. Add `LLAMA_API_KEY` as a long random secret in the service environment. Keep it private.
3. Deploy. The first start downloads the roughly 1.28 GB model and may take several minutes. Wait for `GET /health` to return HTTP 200; while weights are loading it can return 503.
4. Copy the HTTPS service URL. In the gateway service, set:
   - `FARAST_LOCAL_LLM_URL=https://YOUR-LLM-SERVICE-DOMAIN`
   - `FARAST_LOCAL_LLM_API_KEY` to the exact same secret as `LLAMA_API_KEY`
   - `FARAST_LOCAL_LLM_MODEL=Qwen3-1.7B-Q4_K_M`
   - `AI_PRIMARY_PROVIDER=local`
5. The gateway calls the local model first. If it fails and cloud keys are configured, it tries the configured Gemini/Groq fallbacks. Those fallback requests leave Runflare and are subject to provider access, quotas, pricing, and privacy terms.

The server requires a Bearer API key for inference. Its health endpoint is public and does not reveal the key.

## Resource and privacy notes

- Do not increase the context or parallel slots on the free plan without measuring memory; both can cause out-of-memory restarts.
- This service is not suitable for high concurrent traffic or low-latency voice streaming. Use it for short text responses; live speech recognition is handled by the browser-local Whisper path, and live audio output still requires separate streaming/TTS work.
- The browser-local Whisper weights are a separate download on each user's device; this service does not serve or execute those speech weights.
- Runflare's free service is time-limited and non-production. Keep source, deployment scripts, and secrets in their appropriate locations; never put keys in GitHub.