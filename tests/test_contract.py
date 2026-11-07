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
            for _ in range(10):
                websocket.send_bytes(_pcm_frame(5000))
            # The first 200 ms are retained for calibration; sustained speech
            # must survive in pre-roll and meet the active-frame minimum.
            for _ in range(6):
                websocket.send_bytes(_pcm_frame(5000))
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



def test_live_stream_rejects_short_noise_burst_without_calling_asr(monkeypatch):
    calls = {"count": 0}

    async def fake_transcription(pcm, locale):
        calls["count"] += 1
        return "نباید تولید شود"

    monkeypatch.setattr(main, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(main, "transcribe_live_utterance", fake_transcription)
    with TestClient(main.app) as client:
        with client.websocket_connect("/v1/stream") as websocket:
            websocket.send_json({"type": "start", "token": _live_token(), "locale": "fa-IR"})
            assert websocket.receive_json()["type"] == "ready"
            # A short burst can trip the initial VAD candidate, but must not
            # satisfy the minimum sustained voiced-frame requirement.
            for _ in range(4):
                websocket.send_bytes(_pcm_frame(5000))
            for _ in range(6):
                websocket.send_bytes(_pcm_frame(0))
            assert websocket.receive_json()["type"] == "speech_started"
            for _ in range(50):
                websocket.send_bytes(_pcm_frame(0))
            websocket.send_json({"type": "stop"})
    assert calls["count"] == 0


def test_live_stream_can_flush_and_resume_without_reopening_the_socket(monkeypatch):
    calls = {"count": 0}

    async def fake_transcription(pcm, locale):
        calls["count"] += 1
        assert locale == "fa-IR"
        assert len(pcm) >= 640 * 10
        return "سلام فراست"

    monkeypatch.setattr(main, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(main, "transcribe_live_utterance", fake_transcription)
    with TestClient(main.app) as client:
        with client.websocket_connect("/v1/stream") as websocket:
            websocket.send_json({"type": "start", "token": _live_token(), "locale": "fa-IR"})
            assert websocket.receive_json()["type"] == "ready"

            for _ in range(10):
                websocket.send_bytes(_pcm_frame(0))
            for _ in range(20):
                websocket.send_bytes(_pcm_frame(5000))
            assert websocket.receive_json()["type"] == "speech_started"
            websocket.send_json({"type": "end_utterance"})
            assert websocket.receive_json()["type"] == "processing"
            first_final = websocket.receive_json()
            assert first_final["type"] == "final"
            assert first_final["endpoint_reason"] == "user_stop"

            # A second capture can reuse the same authenticated WebSocket session.
            for _ in range(20):
                websocket.send_bytes(_pcm_frame(5000))
            assert websocket.receive_json()["type"] == "speech_started"
            websocket.send_json({"type": "end_utterance"})
            assert websocket.receive_json()["type"] == "processing"
            second_final = websocket.receive_json()
            assert second_final["type"] == "final"
            assert second_final["text"] == "سلام فراست"
            assert calls["count"] == 2
            websocket.send_json({"type": "stop"})


def test_live_transcription_sends_persian_context_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {
                "text": "سلام، فراست",
                "segments": [{
                    "text": " سلام، فراست",
                    "no_speech_prob": 0.02,
                    "avg_logprob": -0.2,
                    "compression_ratio": 1.1,
                }],
            }

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
    assert "سکوت" in captured["data"]["prompt"]
    assert "فراست" not in captured["data"]["prompt"]
    assert captured["filename"] == "farast-utterance.wav"
    assert captured["content_type"] == "audio/wav"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["data"]["timestamp_granularities[]"] == "segment"


def test_live_transcription_rejects_silence_before_calling_provider(monkeypatch):
    async def unexpected_client(*args, **kwargs):
        raise AssertionError("ASR provider must not be called for silence")

    monkeypatch.setattr(main.httpx, "AsyncClient", unexpected_client)
    transcript = asyncio.run(main.transcribe_live_utterance(b"\x00" * 6400, "fa-IR"))
    assert transcript == ""


def test_live_transcription_rejects_low_confidence_non_speech(monkeypatch):
    class FakeResponse:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {
                "text": "موسیقی",
                "segments": [{
                    "text": " موسیقی",
                    "no_speech_prob": 0.98,
                    "avg_logprob": -0.2,
                }],
            }

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, data, files):
            assert data["response_format"] == "verbose_json"
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    transcript = asyncio.run(main.transcribe_live_utterance(_pcm_frame(5000) * 30, "fa-IR"))
    assert transcript == ""



def test_live_transcription_rejects_plausible_sentence_from_ambiguous_audio(monkeypatch):
    class FakeResponse:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {
                "text": "از اینجا باید برداری کنید",
                "segments": [{
                    "text": " از اینجا باید برداری کنید",
                    "no_speech_prob": 0.42,
                    "avg_logprob": -0.15,
                    "compression_ratio": 1.2,
                }],
            }

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, data, files):
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    transcript = asyncio.run(main.transcribe_live_utterance(_pcm_frame(5000) * 30, "fa-IR"))
    assert transcript == ""


def test_health_allows_cross_origin_render_wake_probe():
    response = TestClient(main.app).get(
        "/health",
        headers={"Origin": "https://farast.example"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"

def test_live_transcription_rejects_repeated_hallucinated_sentence(monkeypatch):
    repeated = "همینطور که این تصویر برنامه باید برگردیم. این تصویر باید برگردیم. این تصویر باید برگردیم."

    class FakeResponse:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {
                "text": repeated,
                "segments": [{
                    "text": repeated,
                    "no_speech_prob": 0.05,
                    "avg_logprob": -0.2,
                    "compression_ratio": 1.2,
                }],
            }

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, data, files):
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    transcript = asyncio.run(main.transcribe_live_utterance(_pcm_frame(5000) * 30, "fa-IR"))
    assert transcript == ""

