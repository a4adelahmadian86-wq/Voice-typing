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
    assert response.json()["mode"] == "groq-asr-edge-tts"


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


def test_synthesize_mp3_retries_invalid_provider_audio(monkeypatch):
    calls = {"count": 0}

    class FakeCommunicate:
        def __init__(self, text, voice):
            assert text == "سلام"
            assert voice == main.EDGE_TTS_VOICE

        async def stream(self):
            calls["count"] += 1
            if calls["count"] == 1:
                yield {"type": "audio", "data": b"\x00" * 2048}
                return
            yield {"type": "audio", "data": b"ID3" + (b"\x00" * 2048)}

    monkeypatch.setattr(main.edge_tts, "Communicate", FakeCommunicate)
    audio = asyncio.run(main.synthesize_mp3("سلام"))
    assert calls["count"] == 2
    assert audio.startswith(b"ID3")
    assert main.looks_like_mp3(audio)


def test_looks_like_mp3_rejects_non_audio_payload():
    assert main.looks_like_mp3(b"\x00" * 4096) is False


def test_live_stream_rejects_unconfigured_gateway(monkeypatch):
    monkeypatch.setenv("FARAST_APP_URL", "")
    monkeypatch.setenv("FARAST_VOICE_GATEWAY_SECRET", "")
    with TestClient(main.app) as client:
        with client.websocket_connect("/v1/stream?v=1") as websocket:
            websocket.send_json({"type": "start", "token": "not-a-valid-token", "locale": "fa-IR"})
            message = websocket.receive_json()
            assert message["type"] == "error"
            assert message["message"] == "live_stream_not_configured"
