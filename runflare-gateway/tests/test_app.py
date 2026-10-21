from __future__ import annotations

import asyncio
import json
import importlib.util
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
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "")
    monkeypatch.setattr(gateway, "GEMINI_API_KEY", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_MODEL", "Qwen3-1.7B-Q4_K_M")
    monkeypatch.setattr(gateway, "AI_PRIMARY_PROVIDER", "local")
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


def test_transcribe_uses_quality_first_groq_model(client, monkeypatch):
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(gateway, "GROQ_ASR_MODEL", "whisper-large-v3")

    async def fake_groq(filename, data, mime, language):
        assert filename == "sample.webm"
        assert data == b"valid-audio-bytes"
        assert language == "fa"
        return "سلام، این یک آزمایش است."

    monkeypatch.setattr(gateway, "_groq_transcribe", fake_groq)
    response = client.post(
        "/v1/transcribe",
        headers=auth(),
        data={"locale": "fa-IR"},
        files={"audio": ("sample.webm", b"valid-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "text": "سلام، این یک آزمایش است.",
        "engine": "groq-whisper",
        "model": "whisper-large-v3",
    }


def test_transcribe_falls_back_to_render_when_groq_fails(client, monkeypatch):
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(gateway, "RENDER_ASR_URL", "https://render.example/v1/transcribe")
    monkeypatch.setattr(gateway, "RENDER_ASR_TOKEN", "render-secret")

    async def broken_groq(*args):
        raise RuntimeError("simulated provider outage")

    async def fake_render(filename, data, mime, locale):
        assert locale == "fa-IR"
        return "متن از مسیر پشتیبان"

    monkeypatch.setattr(gateway, "_groq_transcribe", broken_groq)
    monkeypatch.setattr(gateway, "_render_transcribe", fake_render)
    response = client.post(
        "/v1/transcribe",
        headers=auth(),
        data={"locale": "fa-IR"},
        files={"audio": ("sample.webm", b"valid-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["engine"] == "render-fallback"
    assert response.json()["text"] == "متن از مسیر پشتیبان"


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



def test_synthesize_supports_english_voice(client, monkeypatch):
    async def fake_edge(text, language):
        assert text == "Hello FARAST"
        assert language == "en-US"
        return b"english-mp3-data"

    monkeypatch.setattr(gateway, "_edge_synthesize", fake_edge)
    response = client.post(
        "/v1/synthesize",
        headers=auth(),
        json={"text": "Hello FARAST", "language": "en-US", "voice_id": "farast-fa-male-v1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"english-mp3-data"


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


def test_chat_completions_falls_back_from_gemini_to_groq(client, monkeypatch):
    monkeypatch.setattr(gateway, "GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(gateway, "AI_PRIMARY_PROVIDER", "gemini")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append((provider, model))
        if provider == "gemini":
            return 503, b'{"error":"temporary"}', "application/json"
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ پشتیبان"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "groq"
    assert response.json()["choices"][0]["message"]["content"] == "پاسخ پشتیبان"
    assert [call[0] for call in calls] == ["gemini", "groq"]


def test_chat_completions_uses_local_llm_as_primary(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-test-key")
    monkeypatch.setattr(gateway, "LOCAL_LLM_MODEL", "Qwen3-1.7B-Q4_K_M")
    monkeypatch.setattr(gateway, "AI_PRIMARY_PROVIDER", "local")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append((provider, model))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ محلی"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "local"
    assert response.json()["choices"][0]["message"]["content"] == "پاسخ محلی"
    assert calls == [("local", "Qwen3-1.7B-Q4_K_M")]


def test_chat_completions_falls_back_to_groq_when_local_model_fails(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-test-key")
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(gateway, "AI_PRIMARY_PROVIDER", "local")
    calls = []

    async def fake_provider(provider, model, payload):
        calls.append(provider)
        if provider == "local":
            return 503, b'{"error":"model loading"}', "application/json"
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "پاسخ پشتیبان"}}]}).encode()
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "_chat_provider_request", fake_provider)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["x-farast-ai-provider"] == "groq"
    assert calls == ["local", "groq"]


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
    assert len(captured["body"]["messages"][-1]["content"]) <= 909
    assert len(payload["messages"]) == len(original_messages)
    assert json.loads(body) == {"ok": True}

def test_chat_completions_streams_local_llm_sse(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "https://local-llm.example")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "local-test-key")
    captured = {}

    class FakeUpstream:
        is_success = True

        async def aiter_raw(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"\xd8\xb3\xd9\x84\xd8\xa7\xd9\x85"}}]}\n\n'
            yield b'data: [DONE]\n\n'

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def build_request(self, method, url, headers, json):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return {"url": url, "headers": headers, "json": json}

        async def send(self, request, stream=False):
            assert stream is True
            return FakeUpstream()

        async def aclose(self):
            return None

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "farast-general", "stream": True, "messages": [{"role": "user", "content": "سلام"}]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-farast-ai-provider"] == "local"
    assert captured["url"] == "https://local-llm.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer local-test-key"
    assert captured["body"]["stream"] is True
    assert captured["body"]["max_tokens"] == 160
    assert "[DONE]" in response.text

def test_streaming_wraps_full_groq_fallback_as_sse(client, monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_LLM_URL", "")
    monkeypatch.setattr(gateway, "LOCAL_LLM_API_KEY", "")
    monkeypatch.setattr(gateway, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(gateway, "AI_PRIMARY_PROVIDER", "local")

    async def fake_provider(provider, model, payload):
        assert provider == "groq"
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
    assert response.headers["x-farast-ai-provider"] == "groq"
    assert "پاسخ پشتیبان" in response.text
    assert "[DONE]" in response.text