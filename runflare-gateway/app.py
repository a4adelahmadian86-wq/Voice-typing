from __future__ import annotations

import os
import secrets
from typing import Optional

import edge_tts
import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

APP_NAME = "FARAST AI Gateway"
API_TOKEN = os.getenv("FARAST_GATEWAY_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3").strip()
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
AI_PRIMARY_PROVIDER = os.getenv("AI_PRIMARY_PROVIDER", "gemini").strip().lower()
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "fa-IR-FaridNeural").strip()
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
    asr_ready = bool(GROQ_API_KEY or (RENDER_ASR_URL and RENDER_ASR_TOKEN))
    tts_ready = True
    llm_ready = bool(GEMINI_API_KEY or GROQ_API_KEY)
    return {
        "status": "ready" if asr_ready and tts_ready and llm_ready else "degraded",
        "asr_ready": asr_ready,
        "tts_ready": tts_ready,
        "llm_ready": llm_ready,
    }


@app.get("/v1/status", dependencies=[Depends(authorize)])
def status() -> dict:
    return {
        "asr_ready": bool(GROQ_API_KEY or (RENDER_ASR_URL and RENDER_ASR_TOKEN)),
        "tts_ready": True,
        "llm_ready": bool(GEMINI_API_KEY or GROQ_API_KEY),
        "primary_llm": AI_PRIMARY_PROVIDER,
        "asr_model": GROQ_ASR_MODEL if GROQ_API_KEY else ("render-fallback" if RENDER_ASR_URL else None),
        "tts_engine": "edge-tts-with-render-fallback",
    }


async def _groq_transcribe(filename: str, data: bytes, mime: str, language: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("Groq ASR is not configured")
    payload = {"model": GROQ_ASR_MODEL, "language": language, "temperature": "0", "response_format": "json"}
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            data=payload,
            files={"file": (filename, data, mime)},
        )
    if not response.is_success:
        raise RuntimeError(f"Groq ASR returned HTTP {response.status_code}")
    text = str(response.json().get("text", "")).strip()
    if not text:
        raise RuntimeError("Groq ASR returned empty text")
    return text


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
    filename = (audio.filename or "recording.webm").replace("/", "_").replace("\\", "_")[:128]
    mime = audio.content_type or "application/octet-stream"

    if GROQ_API_KEY:
        try:
            text = await _groq_transcribe(filename, data, mime, LOCALE_TO_LANGUAGE[locale])
            return {"text": text, "engine": "groq-whisper", "model": GROQ_ASR_MODEL}
        except Exception:
            pass

    if RENDER_ASR_URL and RENDER_ASR_TOKEN:
        try:
            text = await _render_transcribe(filename, data, mime, locale)
            return {"text": text, "engine": "render-fallback", "model": "configured-render-asr"}
        except Exception:
            pass

    raise HTTPException(status_code=503, detail="Speech recognition is temporarily unavailable")


async def _edge_synthesize(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice=EDGE_TTS_VOICE)
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
    if request.language != "fa-IR":
        raise HTTPException(status_code=400, detail="Only Persian TTS is configured")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")
    try:
        audio = await _edge_synthesize(text)
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


async def _chat_provider_request(provider: str, model: str, payload: dict) -> tuple[int, bytes, str]:
    if provider == "gemini":
        key = GEMINI_API_KEY
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    else:
        key = GROQ_API_KEY
        url = "https://api.groq.com/openai/v1/chat/completions"
    if not key:
        raise RuntimeError(f"{provider} is not configured")
    body = dict(payload)
    body["model"] = model
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
    return response.status_code, response.content, response.headers.get("content-type", "application/json")


@app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
async def chat_completions(payload: dict) -> Response:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=422, detail="messages must be a non-empty array")

    requested_model = str(payload.get("model") or "").strip()
    primary = "groq" if AI_PRIMARY_PROVIDER == "groq" else "gemini"
    order = [primary, "groq" if primary == "gemini" else "gemini"]
    last_status = 503
    for provider in order:
        if provider == "gemini":
            model = requested_model if requested_model.startswith("gemini-") else GEMINI_MODEL
            if not GEMINI_API_KEY:
                continue
        else:
            model = GROQ_CHAT_MODEL
            if not GROQ_API_KEY:
                continue
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
