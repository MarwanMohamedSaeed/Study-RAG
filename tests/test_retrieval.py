from core import rag
from core.mcq import generate_quiz
from core.retriever import retrieve, sample_spread


def test_ingest_result(indexed):
    assert indexed.n_pages == 10 and indexed.n_chunks > 10
    assert indexed.empty_pages == [8]
    assert indexed.warning is None


def test_reingest_is_cached(sample_pdf, chroma, indexed):
    from core.ingest import ingest_pdf
    assert ingest_pdf(sample_pdf, "networks_lecture.pdf", client=chroma).already_indexed


def test_retrieval_returns_metadata(indexed, chroma):
    hits = retrieve("What is the size of the UDP header?", [indexed.doc_id], k=5, client=chroma)
    assert len(hits) == 5
    for h in hits:
        assert h["filename"] == "networks_lecture.pdf"
        assert isinstance(h["page"], int) and 1 <= h["page"] <= 10
        assert h["text"] and h["score"] is not None
    assert hits[0]["page"] == 3  # UDP page
    assert [h["score"] for h in hits] == sorted([h["score"] for h in hits], reverse=True)


def test_arabic_query_finds_arabic_page(indexed, chroma):
    hits = retrieve("ما هو الفرق بين TCP و UDP؟", [indexed.doc_id], k=3, client=chroma)
    assert 10 in [h["page"] for h in hits]


def test_page_range_filter(indexed, chroma):
    hits = retrieve("congestion window", [indexed.doc_id], k=5, page_range=(2, 4), client=chroma)
    assert hits and all(2 <= h["page"] <= 4 for h in hits)


def test_sample_spread_covers_document(indexed, chroma):
    chunks = sample_spread([indexed.doc_id], 5, client=chroma)
    assert len(chunks) == 5
    assert len({c["page"] for c in chunks}) >= 4


def test_language_detection():
    assert rag.detect_language("What is TCP?") == "en"
    assert rag.detect_language("ما هو بروتوكول TCP؟") == "ar"


def test_quiz_pipeline_with_fake_llm(indexed, chroma, monkeypatch):
    import core.retriever as r
    from core import ingest
    monkeypatch.setattr(ingest, "get_client", lambda *a, **k: chroma)
    monkeypatch.setattr(r, "get_client", lambda *a, **k: chroma)
    qs = generate_quiz([indexed.doc_id], n=7, difficulty="easy", seed=1)
    assert len(qs) == 7
    assert all(len(q.options) == 4 and q.correct in "ABCD" for q in qs)
