"""Provider-agnostic LLM wrapper.

Select the backend with LLM_PROVIDER in .env:
  ollama  - free, local (default). Needs `ollama serve` + `ollama pull <OLLAMA_MODEL>`.
  claude  - Anthropic API. Needs ANTHROPIC_API_KEY.
  fake    - deterministic offline stub, used by tests/CI. Never calls a model.

Public API:
  generate(system, user, json_schema=None, temperature=...) -> str
  stream(system, user, temperature=...) -> Iterator[str]
"""
from __future__ import annotations

import json
import re
from typing import Iterator

import requests

from core import config


class LLMError(RuntimeError):
    pass


def provider_label() -> str:
    if config.LLM_PROVIDER == "claude":
        return f"Claude ({config.CLAUDE_MODEL})"
    if config.LLM_PROVIDER == "fake":
        return "Fake (offline stub)"
    return f"Ollama ({config.OLLAMA_MODEL})"


def health() -> tuple[bool, str]:
    """Quick reachability check for the sidebar. Returns (ok, message)."""
    p = config.LLM_PROVIDER
    if p == "fake":
        return True, "offline stub"
    if p == "claude":
        return (True, "API key set") if config.ANTHROPIC_API_KEY else (False, "ANTHROPIC_API_KEY is empty in .env")
    try:
        r = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=2)
        names = {m["name"] for m in r.json().get("models", [])}
    except (requests.RequestException, ValueError):
        return False, (f"Ollama is not running at {config.OLLAMA_HOST}. Start the **Ollama** app "
                       "(Start menu) or run `ollama serve` in a terminal, then reload this page.")
    if config.OLLAMA_MODEL not in names and f"{config.OLLAMA_MODEL}:latest" not in names:
        return False, f"Model `{config.OLLAMA_MODEL}` is not installed. Run: `ollama pull {config.OLLAMA_MODEL}`"
    return True, "connected"


def strip_think(text: str) -> str:
    """Remove reasoning that 'thinking' models (qwen3, deepseek-r1) may leak into the answer,
    including the case where only the closing </think> tag is emitted."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def generate(system: str, user: str, json_schema: dict | None = None,
             temperature: float = 0.2, max_tokens: int = 4096) -> str:
    """Single completion. If json_schema is given, the model is constrained/asked to return JSON."""
    p = config.LLM_PROVIDER
    if p == "ollama":
        return strip_think(_ollama(system, user, json_schema, temperature, max_tokens))
    if p == "claude":
        return _claude(system, user, json_schema, temperature, max_tokens)
    if p == "fake":
        return _fake(system, user, json_schema)
    raise LLMError(f"Unknown LLM_PROVIDER '{p}'. Use ollama, claude or fake.")


def stream(system: str, user: str, temperature: float = 0.2, max_tokens: int = 2048) -> Iterator[str]:
    """Token stream for the chat UI. A leading <think>...</think> block is swallowed."""
    p = config.LLM_PROVIDER
    if p == "ollama":
        yield from _skip_think(_ollama_stream(system, user, temperature, max_tokens))
    elif p == "claude":
        yield from _claude_stream(system, user, temperature, max_tokens)
    else:
        yield generate(system, user, temperature=temperature, max_tokens=max_tokens)


def _skip_think(tokens: Iterator[str]) -> Iterator[str]:
    buf, passthrough = "", False
    for tok in tokens:
        if passthrough:
            yield tok
            continue
        buf += tok
        if "</think>" in buf:
            passthrough = True
            rest = buf.split("</think>", 1)[1].lstrip()
            if rest:
                yield rest
        elif not buf.lstrip().startswith("<think>"[:len(buf.lstrip())]):
            passthrough = True  # no think block: flush what we held back
            yield buf


# --------------------------------------------------------------------------- Ollama
def _ollama_payload(system, user, temperature, max_tokens, stream_, fmt=None):
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": stream_,
        "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": max_tokens},
    }
    if config.OLLAMA_THINK is not None:
        payload["think"] = config.OLLAMA_THINK  # reasoning models (qwen3, deepseek-r1): off = much faster
    if fmt is not None:
        payload["format"] = fmt  # JSON schema -> Ollama structured outputs (grammar-constrained)
    return payload


def _ollama_post(payload, stream_=False):
    try:
        r = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, stream=stream_, timeout=600)
    except requests.ConnectionError as e:
        raise LLMError(f"Cannot reach Ollama at {config.OLLAMA_HOST}. Start the Ollama app (Start menu) "
                       "or run `ollama serve` in a terminal, then try again.") from e
    if r.status_code == 404:
        raise LLMError(f"Ollama model '{config.OLLAMA_MODEL}' not found. Run: ollama pull {config.OLLAMA_MODEL}")
    if not r.ok:
        raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
    return r


def _ollama(system, user, json_schema, temperature, max_tokens):
    r = _ollama_post(_ollama_payload(system, user, temperature, max_tokens, False, json_schema))
    return r.json()["message"]["content"]


def _ollama_stream(system, user, temperature, max_tokens):
    r = _ollama_post(_ollama_payload(system, user, temperature, max_tokens, True), stream_=True)
    for line in r.iter_lines():
        if line:
            chunk = json.loads(line)
            yield chunk.get("message", {}).get("content", "")
            if chunk.get("done"):
                break


# --------------------------------------------------------------------------- Claude
def _claude_client():
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("LLM_PROVIDER=claude but ANTHROPIC_API_KEY is empty in .env")
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _claude(system, user, json_schema, temperature, max_tokens):
    if json_schema is not None:
        user += ("\n\nReturn ONLY a JSON object matching this JSON schema, no prose, no code fences:\n"
                 + json.dumps(json_schema))
    msg = _claude_client().messages.create(
        model=config.CLAUDE_MODEL, max_tokens=max_tokens, temperature=temperature,
        system=system, messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if b.type == "text")


def _claude_stream(system, user, temperature, max_tokens):
    with _claude_client().messages.stream(
            model=config.CLAUDE_MODEL, max_tokens=max_tokens, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}]) as s:
        yield from s.text_stream


# --------------------------------------------------------------------------- Fake (offline)
def _fake(system, user, json_schema):
    """Deterministic stub: echoes context for Q&A, builds trivially-valid MCQs from excerpts."""
    if json_schema is None:
        m = re.search(r"\[(.+? p\.\d+)\]", user)
        return f"(fake LLM) Based on the material [{m.group(1)}]." if m else "(fake LLM) Not found."
    if "reviews" in json_schema.get("properties", {}):
        # verification: the correct option is the one whose text the fake question quotes
        reviews = []
        for num, q_text, opts in re.findall(r"^(\d+)\. (.+)\n((?:   [A-D]\) .*\n?)+)", user, flags=re.M):
            found = [L for L, o in re.findall(r"([A-D])\) (.*)", opts) if o.strip() and o.strip() in q_text]
            reviews.append({"number": int(num), "correct_options": found})
        return json.dumps({"reviews": reviews})
    n = int((re.search(r"exactly (\d+) ", user) or [0, 3])[1])
    excerpts = re.findall(r"\(page (\d+)\)\n(.+?)(?=\n--- Excerpt|\n\nReturn JSON|\Z)", user, flags=re.S)
    sentences = [(p, " ".join(s.split())) for p, t in excerpts for s in re.split(r"(?<=[.!?؟])\s+", t) if len(s.strip()) > 25]
    sentences = sentences or [("1", "placeholder sentence for the fake model")]
    offset = len(re.findall(r"^- ", user, flags=re.M))  # skip sentences already asked about
    qs = []
    for i in range(n):
        page, sent = sentences[(i + offset) % len(sentences)]
        qs.append({
            "question": f"True or false style: {sent}",
            # distractors: reversed text of similar length (never a substring of the question)
            "options": [sent[:80]] + [f"Wrong {k} {i}: {sent[::-1][:60]}" for k in (1, 2, 3)],
            "correct": "A", "explanation": "Stated in the excerpt.", "source_page": int(page),
        })
    return json.dumps({"questions": qs})
