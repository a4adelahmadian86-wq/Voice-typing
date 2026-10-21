from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_gateway_path = Path(__file__).resolve().parents[1] / "app.py"
_spec = importlib.util.spec_from_file_location("runflare_gateway_app", _gateway_path)
assert _spec is not None and _spec.loader is not None
gateway = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gateway
_spec.loader.exec_module(gateway)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gateway, "API_TOKEN", "test-gateway-token")
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(gateway, "CLOUDFLARE_LLM_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8")
    monkeypatch.setattr(gateway, "CLOUDFLARE_HEAVY_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    monkeypatch.setattr(gateway, "CLOUDFLARE_ASR_MODEL", "@cf/openai/whisper-large-v3-turbo")
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_MODEL", "Qwen3-1.7B-Q4_K_M")
    monkeypatch.setattr(gateway, "RENDER_ASR_URL", "")
    monkeypatch.setattr(gateway, "RENDER_ASR_TOKEN", "")
    monkeypatch.setattr(gateway, "RENDER_TTS_URL", "")
    monkeypatch.setattr(gateway, "RENDER_TTS_TOKEN", "")
    return TestClient(gateway.app)


def auth():
    return {"Authorization": "Bearer test-gateway-token"}


def test_health_is_public_and_does_not_disclose_configuration(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "farast-ai-gateway", "status": "ok"}
    assert "token" not in response.text.lower()


def test_inference_requires_bearer_token(client):
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401


def test_status_reports_configured_provider_without_exposing_secrets(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    response = client.get("/v1/status", headers=auth())
    assert response.status_code == 200
    assert response.json()["cloudflare_configured"] is True
    assert response.json()["cloudflare_llm_model"] == "@cf/qwen/qwen3-30b-a3b-fp8"
    assert "secret-token" not in response.text
    assert "account-id" not in response.text


def test_transcribe_uses_cloudflare_whisper_when_configured(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")

    async def fake_cloudflare(filename, data, mime, locale):
        assert filename == "sample.webm"
        assert data == b"valid-audio-bytes"
        assert mime == "audio/webm"
        assert locale == "fa-IR"
        return "سلام، این یک آزمایش است."

    monkeypatch.setattr(gateway, "_cloudflare_transcribe", fake_cloudflare)
    response = client.post(
        "/v1/transcribe",
        headers=auth(),
        data={"locale": "fa-IR"},
        files={"audio": ("sample.webm", b"valid-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "text": "سلام، این یک آزمایش است.",
        "engine": "cloudflare-workers-ai",
        "model": "@cf/openai/whisper-large-v3-turbo",
    }


def test_transcribe_falls_back_to_render_when_cloudflare_fails(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    monkeypatch.setattr(gateway, "RENDER_ASR_URL", "https://render.example/v1/transcribe")
    monkeypatch.setattr(gateway, "RENDER_ASR_TOKEN", "render-secret")

    async def broken_cloudflare(*args):
        raise RuntimeError("simulated provider outage")

    async def fake_render(filename, data, mime, locale):
        assert locale == "fa-IR"
        return "متن از مسیر پشتیبان"

    monkeypatch.setattr(gateway, "_cloudflare_transcribe", broken_cloudflare)
    monkeypatch.setattr(gateway, "_render_transcribe", fake_render)
    response = client.post(
        "/v1/transcribe",
        headers=auth(),
        data={"locale": "fa-IR"},
        files={"audio": ("sample.webm", b"valid-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["engine"] == "render-asr-fallback"
    assert response.json()["text"] == "متن از مسیر پشتیبان"


def test_transcribe_rejects_unsupported_locale(client):
    response = client.post(
        "/v1/transcribe",
        headers=auth(),
        data={"locale": "fr-FR"},
        files={"audio": ("sample.webm", b"audio", "audio/webm")},
    )
    assert response.status_code == 400


def test_cloudflare_transcribe_posts_base64_audio_to_server_api(monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    captured = {}

    class FakeResponse:
        is_success = True
        status_code = 200

        def json(self):
            return {"result": {"text": "متن تشخیص داده‌شده"}}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(gateway._cloudflare_transcribe("a.webm", b"audio-bytes", "audio/webm", "fa-IR"))
    assert result == "متن تشخیص داده‌شده"
    assert captured["url"] == "https://api.cloudflare.com/client/v4/accounts/account-id/ai/run/@cf/openai/whisper-large-v3-turbo"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["body"]["audio"] == base64.b64encode(b"audio-bytes").decode("ascii")
    assert captured["body"]["language"] == "fa"
    assert captured["body"]["task"] == "transcribe"


def test_synthesize_prefers_edge_tts_and_returns_mp3(client, monkeypatch):
    async def fake_edge(text, language):
        assert text == "سلام فراست"
        assert language == "fa-IR"
        return b"fake-mp3-data"

    monkeypatch.setattr(gateway, "_edge_synthesize", fake_edge)
    response = client.post(
        "/v1/synthesize",
        headers=auth(),
        json={"text": "سلام فراست", "language": "fa-IR", "voice_id": "farast-fa-male-v1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["x-farast-voice-engine"] == "edge-tts"
    assert response.content == b"fake-mp3-data"


def test_synthesize_falls_back_to_render_when_edge_tts_fails(client, monkeypatch):
    monkeypatch.setattr(gateway, "RENDER_TTS_URL", "https://render.example/v1/synthesize")
    monkeypatch.setattr(gateway, "RENDER_TTS_TOKEN", "render-secret")

    async def broken_edge(text, language):
        raise RuntimeError("simulated Edge TTS outage")

    async def fake_render(text, language):
        return b"render-mp3-data"

    monkeypatch.setattr(gateway, "_edge_synthesize", broken_edge)
    monkeypatch.setattr(gateway, "_render_synthesize", fake_render)
    response = client.post(
        "/v1/synthesize",
        headers=auth(),
        json={"text": "سلام فراست", "language": "fa-IR", "voice_id": "farast-fa-male-v1"},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-voice-engine"] == "render-fallback"
    assert response.content == b"render-mp3-data"


def test_chat_routes_general_requests_to_cloudflare_first(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-secret")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append((provider, model))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ باکیفیت"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "cloudflare"
    assert calls == [("cloudflare", "@cf/qwen/qwen3-30b-a3b-fp8")]


def test_heavy_model_uses_configured_heavy_model(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append((provider, model))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ مدل سنگین"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-heavy", "messages": [{"role": "user", "content": "تحلیل پیچیده"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "cloudflare"
    assert calls == [("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")]


def test_chat_falls_back_to_local_when_cloudflare_fails(client, monkeypatch):
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-secret")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append(provider)
        if provider == "cloudflare":
            return 429, b'{"error":"daily quota reached"}', "application/json"
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ پشتیبان محلی"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "local"
    assert calls == ["cloudflare", "local"]


def test_chat_returns_service_unavailable_when_no_provider_is_configured(client):
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 503


def test_local_provider_bounds_context_and_output_tokens(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-test-key")
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"ok":true}'
        headers = {"content-type": "application/json"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    original_messages = [{"role": "system", "content": "S" * 5000}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": ("متن " + str(i) + " ") * 500}
        for i in range(7)
    ]
    payload = {"messages": original_messages, "max_tokens": 700, "temperature": 0.2}
    code, body, content_type = asyncio.run(gateway._chat_provider_request("local", "Qwen3-1.7B-Q4_K_M", payload))

    assert code == 200
    assert content_type == "application/json"
    assert captured["url"] == "https://local-llm.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer local-test-key"
    assert captured["body"]["max_tokens"] == 160
    assert len(captured["body"]["messages"]) <= 5
    assert len(captured["body"]["messages"][0]["content"]) <= 1000
    assert captured["body"]["messages"][-1]["content"].endswith("/no_think")
    assert len(captured["body"]["messages"][-1]["content"]) <= 910
    assert len(payload["messages"]) == len(original_messages)
    assert json.loads(body) == {"ok": True}


def test_streaming_wraps_provider_response_as_sse(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "")
    monkeypatch.setattr(gateway, "CLOUDFLARE_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(gateway, "CLOUDFLARE_API_TOKEN", "secret-token")

    async def fake_provider(provider, model, payload):
        assert provider == "cloudflare"
        assert payload.get("stream") is False
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ پشتیبان"}}]}, ensure_ascii=False).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "stream": True, "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-farast-ai-provider"] == "cloudflare"
    assert "پاسخ پشتیبان" in response.text
    assert "[DONE]" in response.text
