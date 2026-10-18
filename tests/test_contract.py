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
