from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

APP_NAME = "FARAST AI Gateway"
API_TOKEN = os.getenv("FARAST_GATEWAY_TOKEN", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_LLM_MODEL = os.getenv("FARAST_CLOUDFLARE_LLM_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8").strip()
CLOUDFLARE_HEAVY_MODEL = os.getenv("FARAST_CLOUDFLARE_HEAVY_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast").strip()
CLOUDFLARE_ASR_MODEL = os.getenv("FARAST_CLOUDFLARE_ASR_MODEL", "@cf/openai/whisper-large-v3-turbo").strip()
LOCAL_LLM_URL = os.getenv("FARAST_LOCAL_LLM_URL", "").strip().rstrip("/")
LOCAL_LLM_API_KEY = os.getenv("FARAST_LOCAL_LLM_API_KEY", "").strip()
LOCAL_LLM_MODEL = os.getenv("FARAST_LOCAL_LLM_MODEL", "Qwen3-1.7B-Q4_K_M").strip()
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "fa-IR-FaridNeural").strip()
EDGE_TTS_EN_VOICE = os.getenv("EDGE_TTS_EN_VOICE", "en-US-AriaNeural").strip()
EDGE_TTS_AR_VOICE = os.getenv("EDGE_TTS_AR_VOICE", "ar-SA-HamedNeural").strip()
FARAST_VOICE_ID = os.getenv("FARAST_VOICE_ID", "farast-fa-male-v1").strip()
RENDER_ASR_URL = os.getenv("FARAST_RENDER_ASR_URL", "").strip()
RENDER_ASR_TOKEN = os.getenv("FARAST_RENDER_ASR_TOKEN", "").strip()
RENDER_TTS_URL = os.getenv("FARAST_RENDER_TTS_URL", "").strip()
RENDER_TTS_TOKEN = os.getenv("FARAST_RENDER_TTS_TOKEN", "").strip()
RENDER_TTS_VOICE_ID = os.getenv("FARAST_RENDER_TTS_VOICE_ID", FARAST_VOICE_ID).strip()
MAX_AUDIO_BYTES = max(1024, int(os.getenv("FARAST_MAX_AUDIO_BYTES", str(12 * 1024 * 1024))))
MAX_TEXT_CHARS = max(1, int(os.getenv("FARAST_MAX_TEXT_CHARS", "1200")))
UPSTREAM_TIMEOUT = max(5.0, float(os.getenv("FARAST_UPSTREAM_TIMEOUT", "90")))

app = FastAPI(title=APP_NAME, version="1.0.0")
LOCALE_TO_LANGUAGE = {"fa-IR": "fa", "en-US": "en", "ar-SA": "ar"}


def authorize(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="AI gateway is not configured")
    expected = f"Bearer {API_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "fa-IR"
    voice_id: str = FARAST_VOICE_ID


@app.get("/health")
def health() -> dict:
    return {"service": "farast-ai-gateway", "status": "ok"}


@app.get("/ready")
def ready() -> dict:
    cloudflare_configured = bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)
    asr_configured = cloudflare_configured or bool(RENDER_ASR_URL and RENDER_ASR_TOKEN)
    llm_configured = cloudflare_configured or bool(LOCAL_LLM_URL and LOCAL_LLM_API_KEY)
    return {
        "status": "ready" if asr_configured and llm_configured else "degraded",
        "asr_configured": asr_configured,
        "tts_configured": True,
        "llm_configured": llm_configured,
        "note": "Configuration readiness only; upstream inference is checked by a real request.",
    }


@app.get("/v1/status", dependencies=[Depends(authorize)])
def status() -> dict:
    cloudflare_configured = bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)
    return {
        "asr_configured": cloudflare_configured or bool(RENDER_ASR_URL and RENDER_ASR_TOKEN),
        "tts_configured": True,
        "llm_configured": cloudflare_configured or bool(LOCAL_LLM_URL and LOCAL_LLM_API_KEY),
        "cloudflare_configured": cloudflare_configured,
        "cloudflare_llm_model": CLOUDFLARE_LLM_MODEL if cloudflare_configured else None,
        "cloudflare_heavy_model": CLOUDFLARE_HEAVY_MODEL if cloudflare_configured else None,
        "local_llm_configured": bool(LOCAL_LLM_URL and LOCAL_LLM_API_KEY),
        "asr_model": CLOUDFLARE_ASR_MODEL if cloudflare_configured else ("render-fallback" if RENDER_ASR_URL and RENDER_ASR_TOKEN else None),
        "tts_engine": "edge-tts-with-render-fallback",
    }


async def _cloudflare_transcribe(filename: str, data: bytes, mime: str, locale: str) -> str:
    if not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN):
        raise RuntimeError("Cloudflare Workers AI is not configured")
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_ASR_MODEL}"
    payload = {
        "audio": base64.b64encode(data).decode("ascii"),
        "task": "transcribe",
        "language": LOCALE_TO_LANGUAGE[locale],
    }
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"},
            json=payload,
        )
    if not response.is_success:
        raise RuntimeError(f"Cloudflare ASR returned HTTP {response.status_code}")
    result = response.json().get("result", {})
    transcript = str(result.get("text", response.json().get("text", ""))).strip()
    if not transcript:
        raise RuntimeError("Cloudflare ASR returned empty text")
    return transcript


async def _render_transcribe(filename: str, data: bytes, mime: str, locale: str) -> str:
    if not RENDER_ASR_URL or not RENDER_ASR_TOKEN:
        raise RuntimeError("Render ASR fallback is not configured")
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            RENDER_ASR_URL,
            headers={"Authorization": f"Bearer {RENDER_ASR_TOKEN}", "Accept": "application/json"},
            data={"locale": locale},
            files={"audio": (filename, data, mime)},
        )
    if not response.is_success:
        raise RuntimeError(f"Render ASR returned HTTP {response.status_code}")
    text = str(response.json().get("text", "")).strip()
    if not text:
        raise RuntimeError("Render ASR returned empty text")
    return text


@app.post("/v1/transcribe", dependencies=[Depends(authorize)])
async def transcribe(audio: UploadFile = File(...), locale: str = Form("fa-IR")) -> dict:
    if locale not in LOCALE_TO_LANGUAGE:
        raise HTTPException(status_code=400, detail="Unsupported locale")
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds configured size limit")
    filename = (audio.filename or "recording.webm").replace("/", "_").replace(chr(92), "_")[:128]
    mime = audio.content_type or "application/octet-stream"
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        try:
            transcript = await _cloudflare_transcribe(filename, data, mime, locale)
            return {"text": transcript, "engine": "cloudflare-workers-ai", "model": CLOUDFLARE_ASR_MODEL}
        except Exception:
            pass
    if RENDER_ASR_URL and RENDER_ASR_TOKEN:
        try:
            transcript = await _render_transcribe(filename, data, mime, locale)
            return {"text": transcript, "engine": "render-asr-fallback", "model": "configured-render-asr"}
        except Exception:
            pass
    raise HTTPException(status_code=503, detail="Speech recognition is temporarily unavailable")


async def _edge_synthesize(text: str, language: str) -> bytes:
    import edge_tts

    voices = {"fa-IR": EDGE_TTS_VOICE, "en-US": EDGE_TTS_EN_VOICE, "ar-SA": EDGE_TTS_AR_VOICE}
    communicate = edge_tts.Communicate(text, voice=voices[language])
    parts: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            parts.append(chunk["data"])
    audio = b"".join(parts)
    if not audio:
        raise RuntimeError("Edge TTS returned no audio")
    return audio


async def _render_synthesize(text: str, language: str) -> bytes:
    if not RENDER_TTS_URL or not RENDER_TTS_TOKEN:
        raise RuntimeError("Render TTS fallback is not configured")
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            RENDER_TTS_URL,
            headers={"Authorization": f"Bearer {RENDER_TTS_TOKEN}", "Accept": "audio/mpeg"},
            json={"text": text, "language": language, "voice_id": RENDER_TTS_VOICE_ID},
        )
    if not response.is_success or not response.content:
        raise RuntimeError(f"Render TTS returned HTTP {response.status_code}")
    return response.content


@app.post("/v1/synthesize", dependencies=[Depends(authorize)])
async def synthesize(request: SynthesizeRequest) -> Response:
    if request.language not in LOCALE_TO_LANGUAGE:
        raise HTTPException(status_code=400, detail="Unsupported TTS language")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")
    try:
        audio = await _edge_synthesize(text, request.language)
        engine = "edge-tts"
    except Exception:
        try:
            audio = await _render_synthesize(text, request.language)
            engine = "render-fallback"
        except Exception:
            raise HTTPException(status_code=503, detail="Speech synthesis is temporarily unavailable")
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Farast-Voice-Id": FARAST_VOICE_ID, "X-Farast-Voice-Engine": engine},
    )


def _prepare_local_payload(payload: dict, model: str) -> dict:
    body = dict(payload)
    body["model"] = model
    # Keep output bounded on Runflare's 1.5-CPU free plan.
    requested_tokens = body.get("max_tokens", body.get("max_completion_tokens", 160))
    try:
        body["max_tokens"] = min(160, max(1, int(requested_tokens)))
    except (TypeError, ValueError):
        body["max_tokens"] = 160
    body.pop("max_completion_tokens", None)
    body.pop("top_k", None)
    # Keep prompt/context bounded for the 2,048-token, 3 GB RAM deployment.
    source_messages = body.get("messages")
    if isinstance(source_messages, list):
        copied = [dict(item) for item in source_messages if isinstance(item, dict)]
        system_message = next((item for item in copied if item.get("role") == "system"), None)
        recent = [item for item in copied if item.get("role") != "system"][-4:]
        body["messages"] = ([system_message] if system_message else []) + recent
        for index, item in enumerate(body["messages"]):
            content = item.get("content")
            if isinstance(content, str):
                if item.get("role") == "system":
                    limit = 1000
                elif index == len(body["messages"]) - 1 and item.get("role") == "user":
                    limit = 900
                else:
                    limit = 400
                item["content"] = content[:limit]
        if body["messages"] and body["messages"][-1].get("role") == "user":
            last_content = str(body["messages"][-1].get("content", ""))
            if not last_content.endswith("/no_think"):
                body["messages"][-1]["content"] = last_content + "\n/no_think"
    return body


async def _chat_provider_request(provider: str, model: str, payload: dict) -> tuple[int, bytes, str]:
    if provider == "cloudflare":
        if not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN):
            raise RuntimeError("Cloudflare Workers AI is not configured")
        key = CLOUDFLARE_API_TOKEN
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions"
    else:
        if not (LOCAL_LLM_URL and LOCAL_LLM_API_KEY):
            raise RuntimeError("Local LLM is not configured")
        key = LOCAL_LLM_API_KEY
        url = LOCAL_LLM_URL
        if not url.endswith("/v1/chat/completions"):
            url += "/v1/chat/completions"
    body = _prepare_local_payload(payload, model) if provider == "local" else dict(payload)
    body["model"] = model
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
    return response.status_code, response.content, response.headers.get("content-type", "application/json")


def _chat_providers(requested_model: str) -> list[tuple[str, str]]:
    heavy_requested = requested_model in {"farast-heavy", "colab-heavy"}
    providers: list[tuple[str, str]] = []
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        model = CLOUDFLARE_HEAVY_MODEL if heavy_requested else CLOUDFLARE_LLM_MODEL
        providers.append(("cloudflare", model))
    if LOCAL_LLM_URL and LOCAL_LLM_API_KEY:
        providers.append(("local", LOCAL_LLM_MODEL))
    return providers


async def _stream_chat_completions(payload: dict) -> StreamingResponse:
    requested_model = str(payload.get("model") or "").strip()
    providers = _chat_providers(requested_model)
    for provider, model in providers:
        client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
        upstream = None
        try:
            if provider == "cloudflare":
                url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions"
                key = CLOUDFLARE_API_TOKEN
                body = dict(payload)
            else:
                url = LOCAL_LLM_URL if LOCAL_LLM_URL.endswith("/v1/chat/completions") else LOCAL_LLM_URL + "/v1/chat/completions"
                key = LOCAL_LLM_API_KEY
                body = _prepare_local_payload(payload, model)
            body["model"] = model
            body["stream"] = True
            request = client.build_request(
                "POST", url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                json=body,
            )
            upstream = await client.send(request, stream=True)
            if upstream.is_success:
                async def relay(response=upstream, http_client=client):
                    try:
                        async for chunk in response.aiter_raw():
                            if chunk:
                                yield chunk
                    finally:
                        await response.aclose()
                        await http_client.aclose()
                return StreamingResponse(
                    relay(), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Farast-AI-Provider": provider},
                )
            await upstream.aread()
        except Exception:
            pass
        finally:
            if upstream is None or not upstream.is_success:
                if upstream is not None:
                    try:
                        await upstream.aclose()
                    except Exception:
                        pass
                await client.aclose()

    fallback_payload = dict(payload)
    fallback_payload["stream"] = False
    for provider, model in providers:
        try:
            code, raw, _ = await _chat_provider_request(provider, model, fallback_payload)
            if not 200 <= code < 300:
                continue
            data = json.loads(raw.decode("utf-8"))
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not isinstance(answer, str) or not answer.strip():
                continue
            async def one_shot_sse(text: str):
                for offset in range(0, len(text), 24):
                    frame = {"choices": [{"index": 0, "delta": {"content": text[offset:offset + 24]}}]}
                    yield ("data: " + json.dumps(frame, ensure_ascii=False) + "\\n\\n").encode("utf-8")
                yield b"data: [DONE]\\n\\n"
            return StreamingResponse(
                one_shot_sse(answer), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Farast-AI-Provider": provider},
            )
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI text streaming is temporarily unavailable")


@app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
async def chat_completions(payload: dict) -> Response:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=422, detail="messages must be a non-empty array")
    if payload.get("stream") is True:
        return await _stream_chat_completions(payload)

    requested_model = str(payload.get("model") or "").strip()
    providers = _chat_providers(requested_model)
    last_status = 503
    for provider, model in providers:
        try:
            code, body, content_type = await _chat_provider_request(provider, model, payload)
            if 200 <= code < 300:
                return Response(
                    content=body,
                    status_code=code,
                    media_type=content_type.split(";", 1)[0],
                    headers={"Cache-Control": "no-store", "X-Farast-AI-Provider": provider},
                )
            last_status = code
        except Exception:
            continue
    raise HTTPException(status_code=503 if last_status < 500 else last_status, detail="AI text generation is temporarily unavailable")
