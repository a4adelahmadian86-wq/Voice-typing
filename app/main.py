from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import wave
from collections import deque
from typing import Literal, Optional

import edge_tts
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.audio import estimate_noise_floor, normalize_speech_pcm, pcm_frame_rms, speech_threshold

APP_NAME = "FARAST Free Voice Gateway"
API_TOKEN = os.getenv("FARAST_VOICE_TOKEN", "").strip()
AI_API_TOKEN = API_TOKEN
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b").strip()
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3").strip()
EDGE_TTS_VOICE = os.getenv("FARAST_EDGE_TTS_VOICE", "fa-IR-FaridNeural").strip()
VOICE_ID = os.getenv("FARAST_VOICE_ID", "farast-fa-male-v1")
MAX_AUDIO_BYTES = max(1024, int(os.getenv("FARAST_MAX_AUDIO_BYTES", str(15 * 1024 * 1024))))
MAX_TEXT_CHARS = max(1, int(os.getenv("FARAST_MAX_TEXT_CHARS", "1200")))
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
PERSIAN_ASR_PROMPT = (
    "این گفتار فارسی ایران و معمولاً محاوره‌ای است. واژه‌ها و جمله‌ها را دقیقاً همان‌طور که شنیده می‌شوند بنویس؛ "
    "متن را ترجمه یا خلاصه نکن. نام‌ها و اصطلاحات زمینه‌ای ممکن است شامل فراست، دستیار صوتی، تایپ، ویرایشگر، "
    "گفت‌وگوی زنده و میکروفون باشند."
)

app = FastAPI(title=APP_NAME, version="0.2.0")


@app.middleware("http")
async def allow_public_health_probe(request, call_next):
    response = await call_next(request)
    if request.url.path == "/health":
        # The browser uses this public, secret-free endpoint to wake a sleeping
        # Render Free instance before attempting the WebSocket upgrade.
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "no-store"
    return response


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


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatCompletionRequest(BaseModel):
    # The public model name is intentionally ignored; the server pins its upstream model.
    model: str = Field(default="farast-general", max_length=100)
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=2048)
    max_completion_tokens: Optional[int] = Field(default=None, ge=1, le=2048)
    stream: bool = False


def authorize_ai(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> None:
    if not AI_API_TOKEN:
        raise HTTPException(status_code=503, detail="AI gateway is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer" or not secrets.compare_digest(credentials.credentials, AI_API_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


@app.get("/health")
def health() -> dict:
    return {
        "service": "farast-voice",
        "status": "ok",
        "asr_configured": bool(GROQ_API_KEY),
        "tts_configured": bool(EDGE_TTS_VOICE),
        "chat_configured": bool(GROQ_API_KEY and GROQ_CHAT_MODEL and AI_API_TOKEN),
        "voice_id": VOICE_ID,
        "mode": "groq-asr-chat-edge-tts",
    }


@app.get("/ready")
def ready() -> dict:
    if not API_TOKEN or not GROQ_API_KEY or not EDGE_TTS_VOICE or not AI_API_TOKEN:
        raise HTTPException(status_code=503, detail="Missing required voice and AI gateway configuration")
    return {"status": "ready", "asr_ready": True, "tts_ready": True, "chat_ready": True, "voice_id": VOICE_ID}


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


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, _auth: None = Depends(authorize_ai)):
    """Authenticated OpenAI-compatible chat proxy; prompts and provider keys stay server-side."""
    if not GROQ_API_KEY or not GROQ_CHAT_MODEL:
        raise HTTPException(status_code=503, detail="Chat provider is not configured")

    messages = [item.model_dump() for item in request.messages]
    if sum(len(item["content"]) for item in messages) > 30000:
        raise HTTPException(status_code=413, detail="Chat request is too large")

    max_output = request.max_completion_tokens or request.max_tokens or 700
    payload = {
        "model": GROQ_CHAT_MODEL,
        "messages": messages,
        "temperature": request.temperature,
        "max_completion_tokens": max_output,
        "stream": request.stream,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Accept": "text/event-stream" if request.stream else "application/json"}

    if request.stream:
        client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))
        try:
            upstream_request = client.build_request("POST", GROQ_CHAT_URL, headers=headers, json=payload)
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            print(f"GROQ_CHAT_CONNECTION_FAILED: {type(exc).__name__}", flush=True)
            raise HTTPException(status_code=502, detail="Chat provider is unavailable") from exc

        if not upstream.is_success:
            status_code = upstream.status_code
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status_code=429 if status_code == 429 else 502, detail="Chat provider rejected the request")

        async def relay_events():
            try:
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            relay_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            upstream = await client.post(GROQ_CHAT_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        print(f"GROQ_CHAT_CONNECTION_FAILED: {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="Chat provider is unavailable") from exc

    if not upstream.is_success:
        raise HTTPException(status_code=429 if upstream.status_code == 429 else 502, detail="Chat provider rejected the request")
    try:
        return upstream.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Chat provider returned an invalid response") from exc


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
                    **({"prompt": PERSIAN_ASR_PROMPT} if locale == "fa-IR" else {}),
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
    retry_delays = (0.5, 1.25)
    for attempt in range(3):
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
            if attempt < len(retry_delays):
                await asyncio.sleep(retry_delays[attempt])
    error_type = type(last_error).__name__ if last_error is not None else "UnknownError"
    raise RuntimeError(f"Edge TTS failed after 3 attempts ({error_type})") from last_error


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


def verify_live_session_token(token: str, locale: str) -> Optional[dict]:
    """Verify a short-lived Laravel-issued session token without calling back to Laravel."""
    if not API_TOKEN or not token or "." not in token:
        return None
    encoded, signature = token.split(".", 1)
    expected = hmac.new(API_TOKEN.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    now = int(time.time())
    if (
        not isinstance(payload, dict)
        or int(payload.get("uid", 0) or 0) < 1
        or payload.get("locale") != locale
        or locale not in {"fa-IR", "en-US", "ar-SA"}
        or int(payload.get("exp", 0) or 0) < now
        or int(payload.get("iat", now + 1) or 0) > now + 30
        or not payload.get("nonce")
    ):
        return None
    return payload


async def transcribe_live_utterance(pcm: bytes, locale: str) -> str:
    """Transcribe one endpointed 16 kHz mono PCM utterance using server-side Groq Whisper."""
    if not GROQ_API_KEY:
        raise RuntimeError("asr_not_configured")
    language = {"fa-IR": "fa", "en-US": "en", "ar-SA": "ar"}[locale]
    normalized_pcm, gain, speech_rms = normalize_speech_pcm(pcm)
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16000)
        audio_file.writeframes(normalized_pcm)

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0)) as client:
            response = await client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data={
                    "model": GROQ_ASR_MODEL,
                    "language": language,
                    "response_format": "json",
                    "temperature": "0",
                    **({"prompt": PERSIAN_ASR_PROMPT} if locale == "fa-IR" else {}),
                },
                files={"file": ("farast-utterance.wav", wav_buffer.getvalue(), "audio/wav")},
            )
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        print(f"LIVE_ASR_CONNECTION_FAILED: model={GROQ_ASR_MODEL} elapsed_ms={elapsed_ms} error={type(exc).__name__}", flush=True)
        raise RuntimeError("asr_provider_unavailable") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if response.status_code == 429:
        print(f"LIVE_ASR_RATE_LIMITED: model={GROQ_ASR_MODEL} elapsed_ms={elapsed_ms}", flush=True)
        raise RuntimeError("asr_rate_limited")
    if not response.is_success:
        print(f"LIVE_ASR_FAILED: model={GROQ_ASR_MODEL} status={response.status_code} elapsed_ms={elapsed_ms}", flush=True)
        raise RuntimeError("asr_provider_failed")

    transcript = str(response.json().get("text", "")).strip()
    duration_ms = int(len(pcm) * 1000 / 32000)
    print(
        f"LIVE_ASR_OK: model={GROQ_ASR_MODEL} locale={locale} duration_ms={duration_ms} "
        f"latency_ms={elapsed_ms} speech_rms={speech_rms:.4f} gain={gain:.2f} chars={len(transcript)}",
        flush=True,
    )
    return transcript


@app.websocket("/v1/stream")
async def live_stream(websocket: WebSocket):
    """
    Full-duplex conversational input.
    Browser sends 16 kHz mono PCM frames continuously. Render performs server-side
    voice activity detection and submits only complete utterances after end-of-speech.
    """
    await websocket.accept()
    try:
        start = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        token = str(start.get("token", "")) if isinstance(start, dict) else ""
        locale = str(start.get("locale", "fa-IR")) if isinstance(start, dict) else ""
        session = verify_live_session_token(token, locale)
        print(
            f"LIVE_STREAM_START_RECEIVED: message_type={start.get('type', 'invalid') if isinstance(start, dict) else 'invalid'} "
            f"token_present={bool(token)} locale={locale} token_valid={bool(session)} asr_configured={bool(GROQ_API_KEY)}",
            flush=True,
        )
        if not session or not GROQ_API_KEY or (isinstance(start, dict) and start.get("type") != "start"):
            print("LIVE_STREAM_AUTH_REJECTED: invalid_start_or_missing_provider_config", flush=True)
            await websocket.send_json({"type": "error", "code": "unauthorized_or_not_configured"})
            await websocket.close(code=1008)
            return

        await websocket.send_json({
            "type": "ready",
            "sample_rate": 16000,
            "format": "pcm_s16le",
            "endpointing": "server-vad",
            "provider": "Groq Whisper",
            "model": GROQ_ASR_MODEL,
        })

        frame_bytes = 640  # 20 ms, mono, signed 16-bit, 16 kHz.
        end_silence_frames = max(20, min(75, int(os.getenv("FARAST_VAD_END_SILENCE_MS", "1000")) // 20))
        preroll_frames = max(10, min(50, int(os.getenv("FARAST_VAD_PREROLL_MS", "500")) // 20))
        max_utterance_frames = max(150, min(1500, int(os.getenv("FARAST_VAD_MAX_UTTERANCE_MS", "20000")) // 20))
        min_utterance_frames = 12
        noise_floor = 35.0
        calibration_levels = []
        calibration_frame_count = 10  # 200 ms; keep these frames in pre-roll.
        pre_roll = deque(maxlen=preroll_frames)
        candidate = []
        utterance = []
        speech_active = False
        silence_frames = 0
        utterance_id = 0
        pending = bytearray()

        async def finish_utterance(reason: str):
            nonlocal utterance, speech_active, silence_frames, candidate, utterance_id
            frames = utterance
            utterance = []
            speech_active = False
            silence_frames = 0
            candidate = []
            if len(frames) < min_utterance_frames:
                return
            utterance_id += 1
            raw_pcm = b"".join(frames)
            duration_ms = int(len(frames) * 20)
            await websocket.send_json({"type": "processing", "turn_id": utterance_id, "duration_ms": duration_ms})
            try:
                transcript = await transcribe_live_utterance(raw_pcm, locale)
            except Exception as exc:
                code = str(exc) if str(exc) in {"asr_rate_limited", "asr_not_configured"} else "asr_provider_failed"
                await websocket.send_json({"type": "provider_error", "code": code, "turn_id": utterance_id})
                return
            if transcript:
                await websocket.send_json({
                    "type": "final",
                    "text": transcript,
                    "turn_id": utterance_id,
                    "duration_ms": duration_ms,
                    "endpoint_reason": reason,
                })
            else:
                await websocket.send_json({"type": "empty", "turn_id": utterance_id})

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("text") is not None:
                try:
                    command = json.loads(message["text"])
                except (TypeError, ValueError):
                    continue
                if command.get("type") == "stop":
                    if speech_active:
                        await finish_utterance("session_stop")
                    break
                continue

            data = message.get("bytes")
            if not data:
                continue
            if len(data) > 24000:
                await websocket.send_json({"type": "error", "code": "audio_frame_too_large"})
                continue
            pending.extend(data)
            usable = len(pending) - (len(pending) % frame_bytes)
            if usable <= 0:
                continue
            batch = bytes(pending[:usable])
            del pending[:usable]

            for offset in range(0, len(batch), frame_bytes):
                frame = batch[offset:offset + frame_bytes]
                rms = pcm_frame_rms(frame)

                if not speech_active and len(calibration_levels) < calibration_frame_count:
                    calibration_levels.append(rms)
                    pre_roll.append(frame)
                    if len(calibration_levels) == calibration_frame_count:
                        noise_floor = estimate_noise_floor(calibration_levels)
                        calibration_threshold = speech_threshold(noise_floor)
                        # The user can begin speaking as soon as the microphone opens.
                        # Re-check the retained calibration frames so the first 200 ms
                        # of speech is not mistaken for ambient noise and discarded.
                        speech_pair = next(
                            (
                                index
                                for index in range(len(calibration_levels) - 1)
                                if calibration_levels[index] >= calibration_threshold
                                and calibration_levels[index + 1] >= calibration_threshold
                            ),
                            None,
                        )
                        if speech_pair is not None:
                            speech_active = True
                            utterance = list(pre_roll)
                            pre_roll.clear()
                            last_active = max(
                                index
                                for index, level in enumerate(calibration_levels)
                                if level >= calibration_threshold
                            )
                            silence_frames = len(calibration_levels) - last_active - 1
                            await websocket.send_json({"type": "speech_started"})
                    continue

                threshold = speech_threshold(noise_floor)

                if not speech_active:
                    if rms < threshold:
                        noise_floor = max(25.0, min(250.0, noise_floor * 0.98 + rms * 0.02))
                        if candidate:
                            pre_roll.extend(candidate)
                            candidate = []
                        pre_roll.append(frame)
                        continue

                    candidate.append(frame)
                    if len(candidate) < 2:
                        continue

                    speech_active = True
                    utterance = list(pre_roll) + candidate
                    pre_roll.clear()
                    candidate = []
                    silence_frames = 0
                    await websocket.send_json({"type": "speech_started"})
                    continue

                utterance.append(frame)
                if rms >= threshold:
                    silence_frames = 0
                else:
                    silence_frames += 1

                if silence_frames >= end_silence_frames:
                    await finish_utterance("silence")
                elif len(utterance) >= max_utterance_frames:
                    await finish_utterance("maximum_duration")

    except WebSocketDisconnect:
        return
    except asyncio.TimeoutError:
        print("LIVE_STREAM_START_TIMEOUT: no_start_frame_received_within_10s", flush=True)
        try:
            await websocket.close(code=1008, reason="session_start_timeout")
        except Exception:
            pass
    except Exception as exc:
        print(f"LIVE_STREAM_FAILED: {type(exc).__name__}: {exc}", flush=True)
        try:
            await websocket.send_json({"type": "error", "code": "live_stream_failed"})
            await websocket.close(code=1011)
        except Exception:
            pass
