import numpy as np
import pytest

from core import citations
from core.citations import check

SOURCES = [
    {"filename": "net.pdf", "ref": "p.3", "text": "The UDP header is only 8 bytes long."},
    {"filename": "net.pdf", "ref": "p.7", "text": "TCP Reno halves cwnd on three duplicate ACKs."},
]


@pytest.fixture
def judge(monkeypatch):
    """Stub support scores: a sentence is supported by the source whose key word it shares."""
    def fake(claims, sources):
        keys = ["UDP", "Reno"]
        return np.array([[5.0 if k in c else -5.0 for k in keys] for c in claims]), "stub"
    monkeypatch.setattr(citations, "_support_matrix", fake)


def test_correct_citations_are_left_alone(judge):
    a = "The UDP header is 8 bytes [net.pdf p.3]. TCP Reno halves cwnd [net.pdf p.7]."
    r = check(a, SOURCES)
    assert r.text == a and not r.corrected and not r.unsupported


def test_unrelated_citation_is_replaced(judge):
    r = check("The UDP header is 8 bytes long [net.pdf p.7].", SOURCES)
    assert r.corrected == [("net.pdf p.7", "net.pdf p.3")] and "[net.pdf p.3]" in r.text


def test_invented_label_is_replaced(judge):
    r = check("TCP Reno halves the window on loss [net.pdf p.99].", SOURCES)
    assert r.corrected == [("net.pdf p.99", "net.pdf p.7")]


def test_unsupported_sentence_is_listed_not_changed(judge):
    a = "Ethernet frames carry up to 1500 bytes [net.pdf p.3]."
    r = check(a, SOURCES)
    assert r.unsupported and r.text == a   # no source clearly supports it: nothing to swap to


def test_refusals_and_short_fragments_skipped(judge):
    from core.prompts import NOT_FOUND
    assert check(NOT_FOUND["en"], SOURCES).text == NOT_FOUND["en"]
    assert not check("See below:", SOURCES).corrected


def test_embedding_fallback_runs_without_reranker(monkeypatch):
    from core import retriever
    monkeypatch.setattr(retriever, "_load_reranker", lambda: (_ for _ in ()).throw(OSError("offline")))
    r = check("The UDP header is only 8 bytes long [net.pdf p.3].", SOURCES)
    assert r.scorer == "embeddings" and not r.corrected
