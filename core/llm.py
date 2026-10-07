"""Provider-agnostic LLM wrapper.

Select the backend with LLM_PROVIDER in .env:
  ollama  - free, local (default). Needs `ollama serve` + `ollama pull <OLLAMA_MODEL>`.
  groq    - free hosted API (no card). Needs GROQ_API_KEY. Rotates through GROQ_MODELS.
  openai  - any other OpenAI-compatible API (OpenRouter, Gemini, ...): OPENAI_BASE_URL/_API_KEY/_MODELS.
  claude  - Anthropic API. Needs ANTHROPIC_API_KEY.
  fake    - deterministic offline stub, used by tests/CI. Never calls a model.

Public API:
  generate(system, user, json_schema=None, temperature=...) -> str
  stream(system, user, temperature=...) -> Iterator[str]
"""
from __future__ import annotations

import json
import re
import threading
import time
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
    if config.LLM_PROVIDER in COMPAT:
        name, _, _, models = _compat_settings()
        return f"{name} ({_active_model(models) or models[0] if models else '?'})"
    return f"Ollama ({config.OLLAMA_MODEL})"


def health() -> tuple[bool, str]:
    """Quick reachability check for the sidebar. Returns (ok, message)."""
    p = config.LLM_PROVIDER
    if p == "fake":
        return True, "offline stub"
    if p == "claude":
        return (True, "API key set") if config.ANTHROPIC_API_KEY else (False, "ANTHROPIC_API_KEY is empty in .env")
    if p in COMPAT:
        return _compat_health()
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
    if p in COMPAT:
        return strip_think(_compat(system, user, json_schema, temperature, max_tokens))
    raise LLMError(f"Unknown LLM_PROVIDER '{p}'. Use ollama, groq, openai, claude or fake.")


def stream(system: str, user: str, temperature: float = 0.2, max_tokens: int = 2048) -> Iterator[str]:
    """Token stream for the chat UI. A leading <think>...</think> block is swallowed."""
    p = config.LLM_PROVIDER
    if p == "ollama":
        yield from _skip_think(_ollama_stream(system, user, temperature, max_tokens))
    elif p == "claude":
        yield from _claude_stream(system, user, temperature, max_tokens)
    elif p in COMPAT:
        yield from _skip_think(_compat_stream(system, user, temperature, max_tokens))
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



# --------------------------------------------------------------------------- OpenAI-compatible (Groq, ...)
COMPAT = ("groq", "openai")
GROQ_URL = "https://api.groq.com/openai/v1"
WAIT_UP_TO = 20          # seconds: a per-minute limit is waited out; anything longer moves to the next model
_exhausted: dict[str, float] = {}   # model -> time when its quota resets
_quota_lock = threading.Lock()


def _compat_settings() -> tuple[str, str, str, list[str]]:
    if config.LLM_PROVIDER == "groq":
        return "Groq", GROQ_URL, config.GROQ_API_KEY, config.GROQ_MODELS
    return "API", config.OPENAI_BASE_URL.rstrip("/"), config.OPENAI_API_KEY, config.OPENAI_MODELS


def _active_model(models: list[str]) -> str | None:
    now = time.time()
    with _quota_lock:
        return next((m for m in models if _exhausted.get(m, 0) <= now), None)


def _reasoning_params(model: str) -> dict:
    """Keep 'thinking' short: on free tiers reasoning tokens count against the token quota."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}
    if model.startswith("qwen/qwen3"):
        return {"reasoning_effort": "none"}
    return {}


def _compat_payload(model, system, user, temperature, max_tokens, json_schema=None, stream_=False,
                    json_mode_fallback: bool = False) -> dict:
    payload = {"model": model, "temperature": temperature, "max_completion_tokens": max_tokens, "stream": stream_,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               **_reasoning_params(model)}
    if json_schema is not None and not json_mode_fallback:
        # best-effort schema mode (strict mode would require every field to be required); Pydantic validates after
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "output", "schema": json_schema, "strict": False}}
    elif json_schema is not None:
        # model without schema support: plain JSON mode, with the schema spelled out in the prompt
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][1]["content"] += "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + \
            json.dumps(json_schema)
    return payload


def _compat_post(payload_for, stream_: bool = False) -> requests.Response:
    """POST to /chat/completions, waiting out short rate limits and rotating models on long ones."""
    name, url, key, models = _compat_settings()
    if not key:
        raise LLMError(f"LLM_PROVIDER={config.LLM_PROVIDER} but its API key is empty in .env")
    tried_without_schema = False
    while True:
        model = _active_model(models)
        if model is None:
            wait = min(_exhausted.values()) - time.time()
            raise LLMError(f"The free {name} quota is used up for every configured model. It resets in about "
                           f"{max(1, round(wait / 60))} min. Try again later, or switch LLM_PROVIDER to ollama.")
        payload = payload_for(model, tried_without_schema)
        try:
            r = requests.post(f"{url}/chat/completions", json=payload, stream=stream_, timeout=180,
                              headers={"Authorization": f"Bearer {key}"})
        except requests.RequestException as e:
            raise LLMError(f"Cannot reach {name} ({url}): {e}") from e
        if r.status_code == 429:
            retry = float(r.headers.get("retry-after", 60) or 60)
            if retry <= WAIT_UP_TO:
                time.sleep(retry + 0.5)          # per-minute token/request limit: wait, then same model
                continue
            with _quota_lock:
                _exhausted[model] = time.time() + retry   # daily limit: use the next model
            continue
        if r.status_code == 400 and "response_format" in payload and not tried_without_schema \
                and "json_schema" in r.text:
            tried_without_schema = True          # model without schema support: fall back to JSON mode
            continue
        if r.status_code == 401:
            raise LLMError(f"{name} rejected the API key (401). Check the key in .env.")
        if not r.ok:
            raise LLMError(f"{name} error {r.status_code}: {r.text[:300]}")
        return r


def _compat(system, user, json_schema, temperature, max_tokens) -> str:
    r = _compat_post(lambda m, fallback: _compat_payload(m, system, user, temperature, max_tokens, json_schema,
                                                         json_mode_fallback=fallback))
    return r.json()["choices"][0]["message"].get("content") or ""


def _compat_stream(system, user, temperature, max_tokens) -> Iterator[str]:
    r = _compat_post(lambda m, fallback: _compat_payload(m, system, user, temperature, max_tokens, stream_=True),
                     stream_=True)
    # Decode the raw bytes as UTF-8 ourselves: event streams usually declare no charset, and requests then
    # falls back to Latin-1, which turns every Arabic character into two garbage ones ("Ø·Ù...").
    for raw in r.iter_lines():
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        delta = json.loads(data)["choices"][0].get("delta", {})
        if delta.get("content"):
            yield delta["content"]


_health_cache: dict[str, tuple[float, tuple[bool, str]]] = {}


def _compat_health() -> tuple[bool, str]:
    """Key set and API reachable (cached for a minute: the sidebar calls this on every rerun)."""
    name, url, key, models = _compat_settings()
    if not key:
        return False, f"Set {'GROQ_API_KEY' if config.LLM_PROVIDER == 'groq' else 'OPENAI_API_KEY'} in .env " \
                      f"(free key: https://console.groq.com/keys)."
    if not models or not url:
        return False, "Set the API URL and at least one model in .env."
    cached = _health_cache.get(url)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    try:
        r = requests.get(f"{url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=5)
        result = (True, "connected") if r.ok else (False, f"{name} answered {r.status_code}: check the API key.")
    except requests.RequestException:
        result = (False, f"Cannot reach {name} at {url}.")
    if _active_model(models) is None:
        result = (False, f"The free {name} quota is used up for now; it resets within the day.")
    _health_cache[url] = (time.time(), result)
    return result


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
def _fake_sentences(user: str) -> list[tuple[str, str]]:
    """(page, sentence) pairs from the excerpts in a prompt (MCQ-style or study-style labels)."""
    excerpts = re.findall(r"\((?:page|slide|part) (\d+)\)\n(.+?)(?=\n--- Excerpt|\n\nReturn JSON|\n\n[A-Z]+:|\Z)", user, flags=re.S)
    excerpts += [(re.findall(r"\d+", lab)[-1], t) for lab, t in re.findall(r"^\[([^\]\n]+)\]\n(.+)$", user, flags=re.M)]
    return [(p, " ".join(s.split())) for p, t in excerpts for s in re.split(r"(?<=[.!])\s+", t)
            if len(s.split()) >= 6 and not s.strip().endswith("?")]


def _fake_phase2(user: str, props: dict) -> str:
    """Deterministic answers for the Phase 2 prompts (question types, blind checks, grading, cards)."""
    sents = _fake_sentences(user) or [("1", "The transport layer moves data between processes on hosts.")]
    offset = len(re.findall(r"^- ", user, flags=re.M))
    n = int((re.search(r"exactly (\d+) |at most (\d+) ", user) or [0, 3])[1] or 3)
    pick = [sents[(i + offset) % len(sents)] for i in range(n)]
    flat = " ".join(" ".join(user.split("STATEMENTS:")[0].split("SENTENCES:")[0].split()).split())
    if "statements" in props:  # true = sentence as written; false = negated wording (not in the text)
        return json.dumps({"statements": [{"statement": s if i % 2 == 0 else f"It is untrue that {s[0].lower()}{s[1:]}",
                                           "answer": i % 2 == 0, "explanation": s, "source_page": int(p)}
                                          for i, (p, s) in enumerate(pick)]})
    if "judgements" in props:
        items = re.findall(r"^(\d+)\. (.+)$", user.split("STATEMENTS:")[-1], flags=re.M)
        return json.dumps({"judgements": [{"number": int(k), "verdict": "true" if s in flat else "false"} for k, s in items]})
    if "blanks" in props:
        out = []
        for p, s in pick:
            words = [w.strip(".,;:()") for w in s.split()]
            word = max((w for w in words if w.isalpha() and len(w) >= 5 and s.lower().count(w.lower()) == 1),
                       key=len, default=None)
            if word:
                out.append({"sentence": s.replace(word, "_____", 1), "answer": word, "alternatives": [],
                            "explanation": s, "source_page": int(p)})
        return json.dumps({"blanks": out})
    if "fills" in props:
        fills = []
        for k, s in re.findall(r"^(\d+)\. (.+)$", user.split("SENTENCES:")[-1], flags=re.M):
            before, _, after = s.partition("_____")
            m = re.search(re.escape(before.strip()) + r"\s*([\w-]+)\s*" + re.escape(after.strip()[:20]), flat)
            fills.append({"number": int(k), "answer": m.group(1) if m else ""})
        return json.dumps({"fills": fills})
    if "short_answers" in props:
        return json.dumps({"short_answers": [{"question": f"Explain: {s[:80]}", "reference": s,
                                              "key_points": [" ".join(s.split()[:4])], "source_page": int(p)}
                                             for p, s in pick]})
    if "score" in props:  # grade by word overlap between the student's answer and the reference
        ref = set(re.findall(r"\w+", re.search(r"REFERENCE ANSWER: (.*)", user).group(1).lower()))
        ans = set(re.findall(r"\w+", re.search(r"STUDENT ANSWER: (.*)", user).group(1).lower()))
        overlap = len(ref & ans) / max(1, len(ref))
        score = 1 if overlap >= .5 else .5 if overlap >= .2 else 0
        return json.dumps({"score": score, "feedback": f"(fake LLM) overlap {overlap:.0%}", "missing": []})
    return json.dumps({"cards": [{"front": f"What does the lecture say about {' '.join(s.split()[:3])}?",
                                  "back": s[:200], "page": int(p)} for p, s in pick]})



def _fake(system, user, json_schema):
    """Deterministic stub: echoes context for Q&A, builds trivially-valid MCQs from excerpts."""
    labels = re.findall(r"\[([^\[\]\n]*?(?:p\.|slide |part )\d+)\]", user)
    props = (json_schema or {}).get("properties", {})
    if json_schema is None:
        if "cheat sheet" in system or "study notes" in system:
            refs = list(dict.fromkeys(labels))[:3] or ["p.1"]
            return "\n".join(["## Key ideas", *[f"- (fake LLM) key idea from the material [{r}]" for r in refs],
                              "## Definitions", f"- (fake LLM) a definition [{refs[0]}]",
                              "## Numbers & formulas", "- None in this material", "## Likely exam points",
                              f"- (fake LLM) an exam point [{refs[-1]}]"])
        if "tutor" in system:
            return "1. **The main idea**: (fake LLM) explanation of the page.\n\n3. **Remember for the exam**: - one point"
        return f"(fake LLM) Based on the material [{labels[0]}]." if labels else "(fake LLM) Not found."
    if props.keys() & {"statements", "blanks", "short_answers", "judgements", "fills", "cards", "score"}:
        return _fake_phase2(user, props)
    if "terms" in props:
        # one term per excerpt: its first two words, with Arabic placeholders that pass validation
        terms = []
        for label, text in re.findall(r"\[([^\]]+)\]\n(.+)", user)[:8]:
            words = re.findall(r"[A-Za-z][A-Za-z0-9-]+", text)
            if len(words) >= 2:
                terms.append({"term": " ".join(words[:2]), "arabic": "مصطلح", "definition_en": text[:120],
                              "definition_ar": "تعريف المصطلح من المحاضرة", "page": int(re.findall(r"\d+", label)[-1])})
        return json.dumps({"terms": terms})
    if "nodes" in props:
        bullets = re.findall(r"^- (.+?)(?: \[([^\]]+)\])?$", user, flags=re.M)[:6]
        nodes = [{"id": f"c{i}", "label": f"Concept {i + 1}", "page": 1} for i in range(max(3, len(bullets)))]
        edges = [{"source": f"c{i}", "target": f"c{i + 1}", "label": "relates to"} for i in range(len(nodes) - 1)]
        return json.dumps({"nodes": nodes, "edges": edges})
    if "reviews" in props:
        # verification: the correct option is the one whose text the fake question quotes
        reviews = []
        for num, q_text, opts in re.findall(r"^(\d+)\. (.+)\n((?:   [A-D]\) .*\n?)+)", user, flags=re.M):
            found = [L for L, o in re.findall(r"([A-D])\) (.*)", opts) if o.strip() and o.strip() in q_text]
            reviews.append({"number": int(num), "correct_options": found})
        return json.dumps({"reviews": reviews})
    n = int((re.search(r"exactly (\d+) ", user) or [0, 3])[1])
    excerpts = re.findall(r"\((?:page|slide|part) (\d+)\)\n(.+?)(?=\n--- Excerpt|\n\nReturn JSON|\Z)", user, flags=re.S)
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
