# FARAST AI Gateway — Runflare

This is a lightweight, stateless API gateway for FARAST. It deliberately does **not** try to load large language or speech models into Runflare's free 3 GB RAM / 3 GB disk plan. Model inference is delegated to configurable providers; Runflare hosts authentication, routing, and the stable API contract.

## Routes

- \`GET /health\`: public basic liveness only.
- \`GET /ready\`: public configuration readiness booleans (not a live upstream test).
- \`GET /v1/status\`: authenticated readiness without returning keys.
- \`POST /v1/transcribe\`: Bearer-authenticated multipart \`audio\` + \`locale\`; Groq Whisper Large v3 first, existing Render service as fallback.
- \`POST /v1/synthesize\`: Bearer-authenticated JSON \`text\`, \`language\`, \`voice_id\`; Microsoft Edge TTS first (Persian, English, Arabic), Render TTS as fallback. Returns MP3.
- \`POST /v1/chat/completions\`: OpenAI-compatible chat request; Gemini 3.8 Flash first by default, Groq GPT-OSS 20B fallback. This gives FARAST one multi-purpose API contract for chat, summarization, writing, and agent text generation.

All \`/v1/*\` endpoints require \`Authorization: Bearer FARAST_GATEWAY_TOKEN\`. Keep provider keys and this token in Runflare's server-side environment settings only. Never put them in browser JavaScript or Git.

## Resource and reliability design

- No model weights are stored on the 3 GB disk; this keeps the deployment small and easy to recreate.
- The gateway is stateless. Settings live in environment variables and source lives in GitHub.
- ASR fallback order: Groq Whisper Large v3 -> Render ASR (if URL and token are configured).
- Whisper selection is quality-first: Groq's current documentation recommends `whisper-large-v3` for error-sensitive multilingual transcription (listed at $0.111 per audio hour) and `whisper-large-v3-turbo` for lower-cost throughput (listed at $0.04 per audio hour). Quotas and prices can change; verify the account dashboard. Change `GROQ_ASR_MODEL` to `whisper-large-v3-turbo` if latency/cost is more important than the best accuracy tier.
- Gemini 3.8 Flash is the balanced default, not a guaranteed free model. Google's published introductory price is $0.75/1M input tokens and $3.75/1M output tokens through Dec 31, 2026; standard pricing rises Jan 1, 2027. For the flagship reasoning model, set `GEMINI_MODEL=gemini-4-argon` only if the account has access and the higher cost is acceptable. Verify the live pricing page before enabling production traffic.
- TTS fallback order: Edge TTS -> Render TTS (if URL and token are configured).
- LLM fallback order: Gemini 3.8 Flash (default primary) -> Groq GPT-OSS 20B, or the reverse if \`AI_PRIMARY_PROVIDER=groq\`.
- Groq currently lists GPT-OSS 20B at $0.075 per million input tokens and $0.30 per million output tokens; GPT-OSS 120B is stronger but costs more ($0.15/$0.60 per million). The configured 20B model is the cost-conscious fallback; set `GROQ_CHAT_MODEL=openai/gpt-oss-120b` if higher quality is worth the extra cost. A provider's free quota, rate limit, region access, terms, and availability still apply. The gateway cannot guarantee that an upstream provider is free or reachable.
- Privacy warning: the Gemini API free tier states that submitted content may be used to improve Google's products. Do not route confidential documents through a free-tier provider unless that use is acceptable under the site's privacy policy and the user's selected processing mode. Review current provider terms before production; a paid tier has different data-use terms.
- Runflare's free service is documented as non-production, with no backup guarantee, and is deleted after three months. Do not store user data, database state, or secrets on its disk.

## Deploy

1. In Runflare, create a project and a **Docker** service using the free resource plan. The free plan currently lists 3 GB RAM, 1.5 CPU, 3 GB disk, Tehran cluster, and two deployments. Confirm the limits and terms in your account before relying on them.
2. Deploy the repository folder \`runflare-gateway\` (not the repository root). If using Runflare CLI, install it as described in Runflare's official docs, log in, open a terminal in this folder, and run \`runflare deploy\`. The first deployment asks you to choose the project/service.
3. Set environment variables from \`.env.example\` in Runflare's service settings. Generate a long random \`FARAST_GATEWAY_TOKEN\`; add Groq and Gemini keys you actually have. Add the current Render tokens as fallback if you want the two services linked.
4. Ensure the service listens on the provided \`PORT\` (the Dockerfile does this), then test \`/health\` and authenticated \`/v1/status\`.
5. Before connecting production traffic, test a real Persian audio recording and verify \`/v1/transcribe\` returns the expected text. \`/ready\` only reports configuration presence; it does not guarantee upstream reachability.

## Recreate after the three-month expiry

Runflare deletes the expired free service and its data, so no deployment script can resurrect that deleted instance. The app is intentionally stateless so no model downloads or data migration are needed:

1. Create a new free project/service in Runflare's dashboard and note the new service.
2. Re-enter the environment variables (use the values from your secure password manager; never commit real values).
3. Run the included \`deploy-runflare.ps1\` from this directory. On the first deploy to a newly created service, the Runflare CLI may ask you to select the new target once.
4. Update the Laravel server-side ASR/TTS URLs to the new service URL and set their token to the same \`FARAST_GATEWAY_TOKEN\`. Keep the Render URLs/tokens in the gateway's fallback variables.
5. Run the smoke tests below. Keep this folder and the environment template in GitHub, not on the expiring Runflare disk.

This is designed to make the **code deployment** repeatable with one script after the new service exists. Creating a replacement Runflare project and entering secrets still requires the account owner to do that step; the provider's public CLI documentation does not establish a supported one-click API for recreating a deleted service.

## Local test

\`\`\`sh
python -m pip install -r requirements-dev.txt
pytest -q
uvicorn app:app --host 127.0.0.1 --port 8000
\`\`\`
