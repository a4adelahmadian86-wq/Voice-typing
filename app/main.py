from __future__ import annotations

import asyncio
import os
import secrets
from typing import Optional

import edge_tts
import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

APP_NAME = "FARAST Free Voice Gateway"
API_TOKEN = os.getenv("FARAST_VOICE_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3-turbo").strip()
EDGE_TTS_VOICE = os.getenv("FARAST_EDGE_TTS_VOICE", "fa-IR-FaridNeural").strip()
VOICE_ID = os.getenv("FARAST_VOICE_ID", "farast-fa-male-v1")
MAX_AUDIO_BYTES = max(1024, int(os.getenv("FARAST_MAX_AUDIO_BYTES", str(15 * 1024 * 1024))))
MAX_TEXT_CHARS = max(1, int(os.getenv("FARAST_MAX_TEXT_CHARS", "1200")))
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

app = FastAPI(title=APP_NAME, version="0.2.0")


def authorize(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="Voice gateway is not configured")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "fa-IR"
    voice_id: str = VOICE_ID


@app.get("/health")
def health() -> dict:
    return {
        "service": "farast-voice",
        "status": "ok",
        "asr_configured": bool(GROQ_API_KEY),
        "tts_configured": bool(EDGE_TTS_VOICE),
        "voice_id": VOICE_ID,
        "mode": "groq-asr-edge-tts",
    }


@app.get("/ready")
def ready() -> dict:
    if not API_TOKEN or not GROQ_API_KEY or not EDGE_TTS_VOICE:
        raise HTTPException(status_code=503, detail="Missing required voice gateway configuration")
    return {"status": "ready", "asr_ready": True, "tts_ready": True, "voice_id": VOICE_ID}


@app.get("/v1/status", dependencies=[Depends(authorize)])
def status() -> dict:
    return {
        "asr_ready": bool(GROQ_API_KEY),
        "tts_ready": bool(EDGE_TTS_VOICE),
        "voice_id": VOICE_ID,
        "asr_provider": "groq",
        "tts_provider": "microsoft-edge-tts",
        "asr_model": GROQ_ASR_MODEL,
        "tts_voice": EDGE_TTS_VOICE,
    }


@app.post("/v1/transcribe", dependencies=[Depends(authorize)])
async def transcribe(audio: UploadFile = File(...), locale: str = Form("fa-IR")) -> dict:
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="Groq API key is not configured")
    if locale not in {"fa-IR", "en-US", "ar-SA"}:
        raise HTTPException(status_code=400, detail="Unsupported locale")

    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds configured size limit")

    suffix = (audio.filename or "recording.webm").rsplit(".", 1)[-1].lower()
    allowed = {"webm", "wav", "mp3", "m4a", "mp4", "ogg", "opus", "mpeg", "flac"}
    if suffix not in allowed:
        suffix = "webm"
    language = {"fa-IR": "fa", "en-US": "en", "ar-SA": "ar"}[locale]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
            response = await client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data={
                    "model": GROQ_ASR_MODEL,
                    "language": language,
                    "response_format": "json",
                    "temperature": "0",
                },
                files={"file": (f"recording.{suffix}", data, audio.content_type or "application/octet-stream")},
            )
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="Groq usage limit reached; try again later")
        if response.status_code in {401, 403}:
            raise HTTPException(status_code=503, detail="Groq rejected the configured API key or access")
        if not response.is_success:
            print(f"GROQ_ASR_FAILED: status={response.status_code} body={response.text[:300]}", flush=True)
            raise HTTPException(status_code=502, detail="Groq speech recognition failed")
        transcript = str(response.json().get("text", "")).strip()
        if not transcript:
            raise HTTPException(status_code=422, detail="No speech was detected")
        return {"text": transcript}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        print(f"GROQ_ASR_CONNECTION_FAILED: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="Could not connect to Groq speech recognition") from exc


async def synthesize_mp3(text: str) -> bytes:
    communicate = edge_tts.Communicate(text=text, voice=EDGE_TTS_VOICE)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            chunks.append(chunk["data"])
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("TTS provider returned empty audio")
    return audio


@app.post("/v1/synthesize", dependencies=[Depends(authorize)])
async def synthesize(request: SynthesizeRequest) -> Response:
    if request.language != "fa-IR" or request.voice_id != VOICE_ID:
        raise HTTPException(status_code=400, detail="Only the configured fixed Persian voice is supported")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")
    try:
        audio = await asyncio.wait_for(synthesize_mp3(text), timeout=60)
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Farast-Voice-Id": VOICE_ID,
                "X-Farast-TTS-Provider": "microsoft-edge-tts",
            },
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Persian speech synthesis timed out") from exc
    except Exception as exc:
        print(f"EDGE_TTS_FAILED: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="Persian speech synthesis provider is unavailable") from exc
