import os

os.environ.setdefault("FARAST_VOICE_TOKEN", "test-token-for-contract-tests")

from fastapi.testclient import TestClient
from app import main


def test_health_does_not_expose_secret():
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert "FARAST_VOICE_TOKEN" not in response.text
    assert "test-token-for-contract-tests" not in response.text


def test_status_requires_auth():
    client = TestClient(main.app)
    assert client.get("/v1/status").status_code == 401


def test_status_accepts_bearer_token():
    client = TestClient(main.app)
    response = client.get("/v1/status", headers={"Authorization": "Bearer test-token-for-contract-tests"})
    assert response.status_code == 200
    assert response.json()["voice_id"] == main.VOICE_ID


def test_wrong_voice_is_rejected_even_when_model_is_unavailable():
    client = TestClient(main.app)
    response = client.post(
        "/v1/synthesize",
        headers={"Authorization": "Bearer test-token-for-contract-tests"},
        json={"text": "سلام", "language": "fa-IR", "voice_id": "another-voice"},
    )
    assert response.status_code == 400


def test_ready_reports_unavailable_models():
    original_asr, original_tts = main.asr_model, main.tts_model
    try:
        main.asr_model = None
        main.tts_model = None
        response = TestClient(main.app).get("/ready")
        assert response.status_code == 503
    finally:
        main.asr_model, main.tts_model = original_asr, original_tts
