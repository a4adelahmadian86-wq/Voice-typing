from __future__ import annotations

import asyncio
import os
import secrets
from typing import Optional

import edge_tts
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import Response
from pydantic import BaseModel, Field

APP_NAME = "FARAST Free Voice Gateway"
API_TOKEN = os.getenv("FARAST_VOICE_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3").strip()
EDGE_TTS_VOICE = os.getenv("FARAST_EDGE_TTS_VOICE", "fa-IR-FaridNeural").strip()
VOICE_ID = os.getenv("FARAST_VOICE_ID", "farast-fa-male-v1")
MAX_AUDIO_BYTES = max(1024, int(os.getenv("FARAST_MAX_AUDIO_BYTES", str(15 * 1024 * 1024))))
MAX_TEXT_CHARS = max(1, int(os.getenv("FARAST_MAX_TEXT_CHARS", "1200")))
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

app = FastAPI(title=APP_NAME, version="0.2.0")


bearer_scheme = HTTPBearer(auto_error=False)


def authorize(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="Voice gateway is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, API_TOKEN):
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


def looks_like_mp3(audio: bytes) -> bool:
    # Edge TTS can fail after yielding non-audio/empty frames; don't return a
    # non-empty response that browsers cannot actually play.
    if len(audio) < 1024:
        return False
    if audio.startswith(b"ID3"):
        return True
    limit = min(len(audio) - 1, 4096)
    return any(audio[i] == 0xFF and (audio[i + 1] & 0xE0) == 0xE0 for i in range(limit))


async def synthesize_mp3(text: str) -> bytes:
    last_error: Optional[Exception] = None
    # One short retry handles transient websocket/endpoint failures without
    # adding long waits to voice conversations. The configured voice stays fixed.
    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text=text, voice=EDGE_TTS_VOICE)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
            audio = b"".join(chunks)
            if not looks_like_mp3(audio):
                raise RuntimeError("TTS provider returned an invalid or empty MP3 payload")
            return audio
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.35)
    raise RuntimeError("Edge TTS failed after one retry") from last_error


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


@app.websocket("/v1/stream")
async def live_stream(websocket):
    """Authenticated live PCM stream; audio is transcribed in short disjoint WAV chunks."""
    import hashlib, hmac, io, json, wave
    from fastapi import WebSocketDisconnect
    await websocket.accept()
    try:
        start = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        token = str(start.get("token", "")) if isinstance(start, dict) else ""
        locale = str(start.get("locale", "fa-IR")) if isinstance(start, dict) else ""
        app_url = os.getenv("FARAST_APP_URL", "").strip().rstrip("/")
        gateway_secret = os.getenv("FARAST_VOICE_GATEWAY_SECRET", "").strip()
        if not token or not app_url or not gateway_secret or not GROQ_API_KEY or locale not in {"fa-IR", "en-US", "ar-SA"}:
            await websocket.send_json({"type": "error", "message": "live_stream_not_configured"})
            await websocket.close(code=1008)
            return
        signature = hmac.new(gateway_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            verified = await client.post(app_url + "/editor/voice/stream-config", json={"token": token}, headers={"X-Farast-Voice-Gateway": signature, "Accept": "application/json"})
        if not verified.is_success or not verified.json().get("gateway") or verified.json().get("locale") != locale:
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return
        await websocket.send_json({"type": "ready", "sample_rate": 16000, "format": "pcm_s16le"})
        buf = bytearray()
        chunk_size = 16000 * 2 * max(2, min(5, int(os.getenv("FARAST_LIVE_CHUNK_SECONDS", "3"))))
        language = {"fa-IR": "fa", "en-US": "en", "ar-SA": "ar"}[locale]
        async def flush(raw):
            if len(raw) < 16000:
                return
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(raw)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
                response = await client.post(GROQ_TRANSCRIPTIONS_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, data={"model": GROQ_ASR_MODEL, "language": language, "response_format": "json", "temperature": "0"}, files={"file": ("live.wav", wav_buf.getvalue(), "audio/wav")})
            if response.is_success:
                text = str(response.json().get("text", "")).strip()
                if text:
                    await websocket.send_json({"type": "final", "text": text})
            else:
                await websocket.send_json({"type": "provider_error", "status": response.status_code})
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("text") is not None:
                try: command = json.loads(message["text"])
                except (TypeError, ValueError): continue
                if command.get("type") == "stop":
                    await flush(bytes(buf)); break
            elif message.get("bytes"):
                buf.extend(message["bytes"][:len(message["bytes"]) - len(message["bytes"]) % 2])
                while len(buf) >= chunk_size:
                    chunk = bytes(buf[:chunk_size]); del buf[:chunk_size]
                    await flush(chunk)
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        print(f"LIVE_STREAM_FAILED: {type(exc).__name__}", flush=True)
        try:
            await websocket.send_json({"type": "error", "message": "live_stream_failed"})
            await websocket.close(code=1011)
        except Exception:
            pass
