import pytest

from core import retriever
from core.retriever import _rrf, retrieve, tokenize


@pytest.fixture
def lecture(indexed, chroma, monkeypatch):
    monkeypatch.setattr(retriever, "get_client", lambda *a, **k: chroma)
    return indexed


def test_tokenize():
    assert tokenize("What is the SOCK_DGRAM socket type?") == ["sock_dgram", "socket", "type"]
    assert tokenize("ما هي المصافحة الثلاثية") == ["مصافحة", "ثلاثية"]  # stop words and 'ال' removed


def test_keyword_search_finds_exact_identifier(lecture, chroma):
    hits = retrieve("SOCK_DGRAM", [lecture.doc_id], k=3, client=chroma, mode="keyword")
    assert hits[0]["page"] == 9 and hits[0]["bm25"] > 0
    assert hits[0]["score"] is not None          # cosine similarity filled in for keyword-only hits


def test_keyword_search_respects_page_range(lecture, chroma):
    hits = retrieve("TCP header", [lecture.doc_id], k=5, page_range=(2, 3), client=chroma, mode="keyword")
    assert hits and all(2 <= h["page"] <= 3 for h in hits)


def test_rrf_rewards_agreement():
    a = [{"doc_id": "d", "page": p, "chunk_index": 0, "score": 0.9} for p in (1, 2, 3)]
    b = [{"doc_id": "d", "page": p, "chunk_index": 0, "bm25": 5.0} for p in (3, 2, 9)]
    fused = _rrf([a, b])
    pages = [c["page"] for c in fused]
    assert set(pages[:2]) == {2, 3} and pages[2] == 1   # found by both lists beats first in only one
    assert fused[0]["score"] == 0.9 and fused[0]["bm25"] == 5.0


@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid"])
def test_every_mode_returns_metadata(lecture, chroma, mode):
    hits = retrieve("How long is the UDP header?", [lecture.doc_id], k=3, client=chroma, mode=mode)
    assert len(hits) == 3 and hits[0]["page"] == 3
    assert all(h["filename"] and h["ref"] and h["score"] is not None for h in hits)


def test_rerank_reorders_with_cross_encoder(lecture, chroma, monkeypatch):
    class Stub:  # pretends the checksum chunk is the most relevant
        def predict(self, pairs):
            return [10.0 if "checksum" in text else 0.0 for _, text in pairs]
    monkeypatch.setattr(retriever, "_load_reranker", lambda: Stub())
    hits = retrieve("Tell me about UDP", [lecture.doc_id], k=3, client=chroma, mode="hybrid+rerank")
    assert "checksum" in hits[0]["text"] and hits[0]["rerank"] == 10.0


def test_rerank_falls_back_when_model_missing(lecture, chroma, monkeypatch):
    def boom():
        raise OSError("no internet")
    monkeypatch.setattr(retriever, "_load_reranker", boom)
    hits = retrieve("How long is the UDP header?", [lecture.doc_id], k=3, client=chroma, mode="hybrid+rerank")
    assert hits[0]["page"] == 3 and "no internet" in retriever.reranker_error
    monkeypatch.setattr(retriever, "reranker_error", None)


def test_unknown_mode_rejected(lecture, chroma):
    with pytest.raises(ValueError):
        retrieve("x", [lecture.doc_id], client=chroma, mode="magic")
