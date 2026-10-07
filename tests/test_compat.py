"""OpenAI-compatible provider (Groq preset) against a fake HTTP server: no network, no API key."""
import json

import pytest

from core import config, llm
from core.llm import LLMError


class Resp:
    def __init__(self, status=200, body=None, headers=None, lines=None):
        self.status_code, self._body, self.headers, self._lines = status, body or {}, headers or {}, lines or []
        self.text = json.dumps(self._body)
        self.ok = status < 400

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


def answer(text):
    return Resp(200, {"choices": [{"message": {"content": text}}]})


@pytest.fixture
def groq(monkeypatch):
    """Point the provider at Groq with two models; record every request; no real sleeping."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "GROQ_MODELS", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
    monkeypatch.setattr(llm, "_exhausted", {})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls, replies = [], []

    def post(url, json=None, **kw):
        calls.append(json)
        return replies.pop(0)
    monkeypatch.setattr(llm.requests, "post", post)
    return calls, replies


def test_schema_and_reasoning_settings(groq):
    calls, replies = groq
    replies.append(answer('{"ok": true}'))
    out = llm.generate("sys", "user prompt", json_schema={"type": "object"})
    p = calls[0]
    assert out == '{"ok": true}'
    assert p["model"] == "openai/gpt-oss-120b" and p["reasoning_effort"] == "low" and p["include_reasoning"] is False
    assert p["response_format"]["type"] == "json_schema"
    assert "JSON schema" not in p["messages"][1]["content"]       # schema enforced by the API, not repeated


def test_qwen_reasoning_is_switched_off():
    assert llm._reasoning_params("qwen/qwen3.8-27b") == {"reasoning_effort": "none"}


def test_short_rate_limit_waits_and_retries_same_model(groq):
    calls, replies = groq
    replies += [Resp(429, headers={"retry-after": "3"}), answer("hi")]
    assert llm.generate("s", "u") == "hi"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"] * 2


def test_daily_limit_rotates_to_next_model(groq):
    calls, replies = groq
    replies += [Resp(429, headers={"retry-after": "3600"}), answer("from qwen")]
    assert llm.generate("s", "u") == "from qwen"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    replies.append(answer("still qwen"))
    llm.generate("s", "u")
    assert calls[-1]["model"] == "qwen/qwen3.8-27b"                # exhausted model is skipped next time


def test_all_models_exhausted(groq):
    _, replies = groq
    replies += [Resp(429, headers={"retry-after": "7200"})] * 2
    with pytest.raises(LLMError, match="quota is used up"):
        llm.generate("s", "u")


def test_falls_back_to_json_mode_without_schema_support(groq):
    calls, replies = groq
    replies += [Resp(400, {"error": "response_format json_schema is not supported"}), answer("{}")]
    llm.generate("s", "u", json_schema={"type": "object", "properties": {"a": {"type": "string"}}})
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in calls[1]["messages"][1]["content"]


def test_bad_key_and_missing_key(groq, monkeypatch):
    _, replies = groq
    replies.append(Resp(401, {"error": "invalid"}))
    with pytest.raises(LLMError, match="API key"):
        llm.generate("s", "u")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(LLMError, match="empty"):
        llm.generate("s", "u")
    assert llm.health()[0] is False


def test_streaming(groq):
    _, replies = groq
    chunk = lambda t: "data: " + json.dumps({"choices": [{"delta": {"content": t}}]})
    replies.append(Resp(200, lines=[chunk("Hel"), "", chunk("lo"), "data: [DONE]"]))
    assert "".join(llm.stream("s", "u")) == "Hello"


def test_provider_label(groq):
    assert llm.provider_label() == "Groq (openai/gpt-oss-120b)"
