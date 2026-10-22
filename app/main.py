```python
from __future__ import annotations

import io
import os
import secrets
import tempfile
from pathlib import Path
from threading import Lock
from typing import Optional

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field


APP_NAME = "FARAST Self-hosted Voice"

API_TOKEN = os.getenv("FARAST_VOICE_TOKEN", "").strip()

ASR_MODEL_NAME = os.getenv("FARAST_ASR_MODEL", "small")
ASR_DEVICE = os.getenv("FARAST_ASR_DEVICE", "cpu")
ASR_COMPUTE_TYPE = os.getenv("FARAST_ASR_COMPUTE_TYPE", "int8")
ASR_CPU_THREADS = max(
    1, int(os.getenv("FARAST_ASR_CPU_THREADS", "2"))
)

TTS_MODEL_PATH = os.getenv(
    "FARAST_TTS_MODEL_PATH",
    "/models/persian-tts-male-vits/best_model_91323.pth",
)
TTS_CONFIG_PATH = os.getenv(
    "FARAST_TTS_CONFIG_PATH",
    "/models/persian-tts-male-vits/config.json",
)
TTS_AUTO_DOWNLOAD = os.getenv(
    "FARAST_TTS_AUTO_DOWNLOAD", "true"
).lower() in {"1", "true", "yes"}
TTS_MODEL_REPO = os.getenv(
    "FARAST_TTS_MODEL_REPO", "Kamtera/persian-tts-male-vits"
)

VOICE_ID = os.getenv("FARAST_VOICE_ID", "farast-fa-male-v1")

MAX_AUDIO_BYTES = max(
    1024,
    int(os.getenv("FARAST_MAX_AUDIO_BYTES", str(15 * 1024 * 1024))),
)
MAX_TEXT_CHARS = max(
    1, int(os.getenv("FARAST_MAX_TEXT_CHARS", "1200"))
)

app = FastAPI(title=APP_NAME, version="0.1.0")

model_lock = Lock()

asr_model = None
tts_model = None
asr_error: Optional[str] = None
tts_error: Optional[str] = None


def authorize(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Validate the Bearer token using UTF-8 byte comparison."""

    if not API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice service is not configured: "
                "FARAST_VOICE_TOKEN is missing"
            ),
        )

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expected = f"Bearer {API_TOKEN}"

    try:
        provided_bytes = authorization.encode("utf-8")
        expected_bytes = expected.encode("utf-8")

        valid = secrets.compare_digest(
            provided_bytes,
            expected_bytes,
        )
    except (TypeError, UnicodeError):
        valid = False

    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "fa-IR"
    voice_id: str = VOICE_ID


@app.on_event("startup")
def load_models() -> None:
    global asr_model, tts_model, asr_error, tts_error

    # Initialize speech recognition.
    try:
        from faster_whisper import WhisperModel

        asr_model = WhisperModel(
            ASR_MODEL_NAME,
            device=ASR_DEVICE,
            compute_type=ASR_COMPUTE_TYPE,
            cpu_threads=ASR_CPU_THREADS,
            download_root=os.getenv(
                "FARAST_ASR_MODEL_CACHE",
                "/models/faster-whisper",
            ),
        )
        asr_error = None

    except Exception as exc:
        asr_model = None
        asr_error = f"{type(exc).__name__}: {exc}"
        print(f"ASR_INIT_FAILED: {asr_error}", flush=True)

    # Initialize the fixed Persian TTS model.
    try:
        model_path = Path(TTS_MODEL_PATH)
        config_path = Path(TTS_CONFIG_PATH)

        if TTS_AUTO_DOWNLOAD and (
            not model_path.is_file() or not config_path.is_file()
        ):
            from huggingface_hub import hf_hub_download

            model_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.parent.mkdir(parents=True, exist_ok=True)

            if not model_path.is_file():
                hf_hub_download(
                    repo_id=TTS_MODEL_REPO,
                    filename="best_model_91323.pth",
                    local_dir=str(model_path.parent),
                )

            if not config_path.is_file():
                hf_hub_download(
                    repo_id=TTS_MODEL_REPO,
                    filename="config.json",
                    local_dir=str(config_path.parent),
                )

        if not model_path.is_file() or not config_path.is_file():
            raise FileNotFoundError(
                "Persian TTS model files are missing. "
                "Verify model paths, persistent storage, "
                "and model download access."
            )

        from TTS.api import TTS

        tts_model = TTS(
            model_path=str(model_path),
            config_path=str(config_path),
            progress_bar=False,
            gpu=False,
        )
        tts_error = None

    except Exception as exc:
        tts_model = None
        tts_error = f"{type(exc).__name__}: {exc}"
        print(f"TTS_INIT_FAILED: {tts_error}", flush=True)


@app.get("/health")
def health() -> dict:
    return {
        "service": "farast-voice",
        "status": "ok",
        "asr_ready": asr_model is not None,
        "tts_ready": tts_model is not None,
        "voice_id": VOICE_ID,
    }


@app.get("/ready")
def ready() -> dict:
    if asr_model is None or tts_model is None:
        raise HTTPException(
            status_code=503,
            detail={
                "service": "farast-voice",
                "asr_ready": asr_model is not None,
                "tts_ready": tts_model is not None,
                "asr_error": asr_error,
                "tts_error": tts_error,
            },
        )

    return {
        "status": "ready",
        "asr_ready": True,
        "tts_ready": True,
        "voice_id": VOICE_ID,
    }


@app.get("/v1/status", dependencies=[Depends(authorize)])
def status() -> dict:
    return {
        "asr_ready": asr_model is not None,
        "tts_ready": tts_model is not None,
        "voice_id": VOICE_ID,
        "asr_error": asr_error if asr_model is None else None,
        "tts_error": tts_error if tts_model is None else None,
    }


@app.post("/v1/transcribe", dependencies=[Depends(authorize)])
async def transcribe(
    audio: UploadFile = File(...),
    locale: str = Form("fa-IR"),
) -> dict:
    if asr_model is None:
        raise HTTPException(
            status_code=503,
            detail="ASR model is not ready; inspect service logs",
        )

    if locale not in {"fa-IR", "en-US", "ar-SA"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported locale",
        )

    data = await audio.read(MAX_AUDIO_BYTES + 1)

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Audio file is empty",
        )

    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio exceeds configured size limit",
        )

    suffix = Path(audio.filename or "recording.webm").suffix.lower()

    if suffix not in {
        ".webm",
        ".wav",
        ".mp3",
        ".m4a",
        ".mp4",
        ".ogg",
        ".opus",
        ".mpeg",
    }:
        suffix = ".audio"

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(data)
            tmp.flush()

            language = {
                "fa-IR": "fa",
                "en-US": "en",
                "ar-SA": "ar",
            }[locale]

            with model_lock:
                segments, _info = asr_model.transcribe(
                    tmp.name,
                    language=language,
                    vad_filter=True,
                    beam_size=3,
                    condition_on_previous_text=False,
                )

                transcript = " ".join(
                    segment.text.strip() for segment in segments
                ).strip()

        return {"text": transcript}

    except Exception as exc:
        print(
            f"ASR_REQUEST_FAILED: {type(exc).__name__}: {exc}",
            flush=True,
        )
        raise HTTPException(
            status_code=422,
            detail="Could not decode or transcribe this audio",
        ) from exc


@app.post("/v1/synthesize", dependencies=[Depends(authorize)])
def synthesize(request: SynthesizeRequest) -> Response:
    if request.language != "fa-IR" or request.voice_id != VOICE_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only the configured fixed Persian voice is supported"
            ),
        )

    text = request.text.strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text is empty",
        )

    if tts_model is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Fixed Persian TTS model is not ready; "
                "inspect service logs"
            ),
        )

    try:
        with model_lock:
            wav = tts_model.tts(text=text)

        sample_rate = int(
            getattr(
                tts_model.synthesizer,
                "output_sample_rate",
                22050,
            )
        )

        output = io.BytesIO()

        sf.write(
            output,
            wav,
            sample_rate,
            format="WAV",
            subtype="PCM_16",
        )

        return Response(
            content=output.getvalue(),
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store",
                "X-Farast-Voice-Id": VOICE_ID,
            },
        )

    except Exception as exc:
        print(
            f"TTS_REQUEST_FAILED: {type(exc).__name__}: {exc}",
            flush=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Speech synthesis failed",
        ) from exc
```
