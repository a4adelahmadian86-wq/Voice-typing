import asyncio
import os

os.environ.setdefault("FARAST_VOICE_TOKEN", "test-token-for-contract-tests")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

from fastapi.testclient import TestClient
from app import main


def test_health_does_not_expose_secret():
    response = TestClient(main.app).get("/health")
    assert response.status_code == 200
    assert "FARAST_VOICE_TOKEN" not in response.text
    assert "test-token-for-contract-tests" not in response.text
    assert "test-groq-key" not in response.text
    assert response.json()["mode"] == "groq-asr-chat-edge-tts"


def test_status_requires_auth():
    assert TestClient(main.app).get("/v1/status").status_code == 401


def test_status_accepts_bearer_token():
    response = TestClient(main.app).get(
        "/v1/status",
        headers={"Authorization": "Bearer test-token-for-contract-tests"},
    )
    assert response.status_code == 200
    assert response.json()["voice_id"] == main.VOICE_ID
    assert response.json()["asr_provider"] == "groq"
    assert response.json()["asr_ready"] is True
    assert response.json()["tts_ready"] is True
    assert response.json()["chat_ready"] is True


def test_wrong_voice_is_rejected():
    response = TestClient(main.app).post(
        "/v1/synthesize",
        headers={"Authorization": "Bearer test-token-for-contract-tests"},
        json={"text": "سلام", "language": "fa-IR", "voice_id": "another-voice"},
    )
    assert response.status_code == 400


def test_ready_requires_configuration(monkeypatch):
    monkeypatch.setattr(main, "GROQ_API_KEY", "")
    response = TestClient(main.app).get("/ready")
    assert response.status_code == 503



def test_openapi_documents_bearer_auth():
    schema = TestClient(main.app).get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/v1/status"]["get"]["security"] == [{"HTTPBearer": []}]
    assert schema["paths"]["/v1/synthesize"]["post"]["security"] == [{"HTTPBearer": []}]


def test_synthesize_returns_nonempty_mpeg_audio(monkeypatch):
    async def fake_audio(_text):
        return b"ID3" + (b"\x00" * 2048)

    monkeypatch.setattr(main, "synthesize_mp3", fake_audio)
    response = TestClient(main.app).post(
        "/v1/synthesize",
        headers={"Authorization": "Bearer test-token-for-contract-tests"},
        json={
            "text": "سلام، به فراست خوش آمدید.",
            "language": "fa-IR",
            "voice_id": main.VOICE_ID,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["x-farast-tts-provider"] == "microsoft-edge-tts"
    assert len(response.content) > 1024


def test_synthesize_returns_error_when_provider_returns_no_audio(monkeypatch):
    async def fake_empty_audio(_text):
        raise RuntimeError("TTS provider returned empty audio")

    monkeypatch.setattr(main, "synthesize_mp3", fake_empty_audio)
    response = TestClient(main.app).post(
        "/v1/synthesize",
        headers={"Authorization": "Bearer test-token-for-contract-tests"},
        json={"text": "سلام", "language": "fa-IR", "voice_id": main.VOICE_ID},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "Persian speech synthesis provider is unavailable"


def test_synthesize_mp3_retries_transient_provider_failures(monkeypatch):
    calls = {"count": 0}

    class FakeCommunicate:
        def __init__(self, text, voice):
            assert text == "سلام"
            assert voice == main.EDGE_TTS_VOICE

        async def stream(self):
            calls["count"] += 1
            if calls["count"] < 3:
                raise ConnectionError("temporary provider disconnect")
            yield {"type": "audio", "data": b"ID3" + (b"\x00" * 2048)}

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(main.edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    audio = asyncio.run(main.synthesize_mp3("سلام"))
    assert calls["count"] == 3
    assert audio.startswith(b"ID3")
    assert main.looks_like_mp3(audio)


def test_looks_like_mp3_rejects_non_audio_payload():
    assert main.looks_like_mp3(b"\x00" * 4096) is False


def _live_token(locale="fa-IR", uid=42, lifetime=120):
    import base64
    import hashlib
    import hmac
    import json
    import time

    payload = {
        "uid": uid,
        "locale": locale,
        "iat": int(time.time()),
        "exp": int(time.time()) + lifetime,
        "nonce": "test-session-nonce",
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(main.API_TOKEN.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _pcm_frame(amplitude=0):
    import math
    import struct

    samples = [int(amplitude * math.sin(2 * math.pi * 440 * index / 16000)) for index in range(320)]
    return struct.pack("<320h", *samples)


def test_live_session_token_is_verified_without_laravel_callback():
    token = _live_token()
    payload = main.verify_live_session_token(token, "fa-IR")
    assert payload["uid"] == 42
    assert payload["locale"] == "fa-IR"
    assert main.verify_live_session_token(token, "en-US") is None
    assert main.verify_live_session_token(token + "bad", "fa-IR") is None


def test_live_stream_accepts_signed_session_without_laravel_url(monkeypatch):
    monkeypatch.setattr(main, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.delenv("FARAST_APP_URL", raising=False)
    with TestClient(main.app) as client:
        with client.websocket_connect("/v1/stream") as websocket:
            websocket.send_json({"type": "start", "token": _live_token(), "locale": "fa-IR"})
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            assert ready["endpointing"] == "server-vad"
            websocket.send_json({"type": "stop"})


def test_live_stream_detects_silence_endpoint_and_transcribes_complete_turn(monkeypatch):
    async def fake_transcription(pcm, locale):
        assert len(pcm) >= 640 * 12
        assert locale == "fa-IR"
        return "سلام فراست"

    monkeypatch.setattr(main, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(main, "transcribe_live_utterance", fake_transcription)
    with TestClient(main.app) as client:
        with client.websocket_connect("/v1/stream") as websocket:
            websocket.send_json({"type": "start", "token": _live_token(), "locale": "fa-IR"})
            assert websocket.receive_json()["type"] == "ready"
            for _ in range(4):
                websocket.send_bytes(_pcm_frame(5000))
            # Keep the microphone stream continuous while the first 200 ms of
            # audio is calibrated; the initial speech must survive in pre-roll.
            for _ in range(6):
                websocket.send_bytes(_pcm_frame(0))
            assert websocket.receive_json()["type"] == "speech_started"
            for _ in range(59):
                websocket.send_bytes(_pcm_frame(0))
            processing = websocket.receive_json()
            final = websocket.receive_json()
            assert processing["type"] == "processing"
            assert final["type"] == "final"
            assert final["text"] == "سلام فراست"
            assert final["endpoint_reason"] == "silence"
            websocket.send_json({"type": "stop"})



def test_live_transcription_sends_persian_context_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {"text": "سلام، فراست"}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, data, files):
            captured["url"] = url
            captured["data"] = data
            captured["filename"] = files["file"][0]
            captured["content_type"] = files["file"][2]
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    pcm = _pcm_frame(3000) * 30
    transcript = asyncio.run(main.transcribe_live_utterance(pcm, "fa-IR"))

    assert transcript == "سلام، فراست"
    assert captured["data"]["model"] == "whisper-large-v3"
    assert captured["data"]["language"] == "fa"
    assert captured["data"]["temperature"] == "0"
    assert "محاوره‌ای" in captured["data"]["prompt"]
    assert "فراست" in captured["data"]["prompt"]
    assert captured["filename"] == "farast-utterance.wav"
    assert captured["content_type"] == "audio/wav"


def test_health_allows_cross_origin_render_wake_probe():
    response = TestClient(main.app).get(
        "/health",
        headers={"Origin": "https://farast.example"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"
