from core import config, llm
from core.llm import _skip_think, strip_think


def test_strip_think_full_block():
    assert strip_think("<think>reasoning...</think>\n\nTCP retransmits. [a.pdf p.4]") == "TCP retransmits. [a.pdf p.4]"


def test_strip_think_only_closing_tag():
    assert strip_think("Okay, let me check...\n</think>\n\nAnswer.") == "Answer."


def test_strip_think_noop():
    assert strip_think("Plain answer.") == "Plain answer."


def test_stream_skips_think_block():
    toks = ["<thi", "nk>let me", " think</th", "ink>\n", "Hello", " world"]
    assert "".join(_skip_think(iter(toks))) == "Hello world"


def test_stream_passthrough_without_think():
    toks = ["He", "llo", " world"]
    assert "".join(_skip_think(iter(toks))) == "Hello world"


def test_health_reports_unreachable_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OLLAMA_HOST", "http://127.0.0.1:1")  # nothing listens here
    ok, msg = llm.health()
    assert not ok and "not running" in msg
