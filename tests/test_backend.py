"""The int8 ONNX backend (default) and re-embedding after a backend change."""
import numpy as np

from core import config, ingest
from core.onnx_backend import OnnxCrossEncoder, OnnxEmbedder


def test_embedder_shapes_and_normalization():
    e = ingest.get_embedder()
    assert isinstance(e, OnnxEmbedder)                       # the default backend
    one = e.encode("query: How long is the UDP header?", normalize_embeddings=True)
    many = e.encode(["passage: a", "passage: b", "passage: c"], normalize_embeddings=True, batch_size=2)
    assert one.shape == (384,) and many.shape == (3, 384)
    assert np.allclose(np.linalg.norm(many, axis=1), 1, atol=1e-5)


def test_embedder_ranks_the_relevant_passage_higher():
    e = ingest.get_embedder()
    q, good, bad = e.encode(["query: How long is the UDP header?", "passage: The UDP header is only 8 bytes long.",
                             "passage: DHCP servers listen on port 67."], normalize_embeddings=True)
    assert q @ good > q @ bad


def test_cross_encoder_scores_relevance():
    ce = OnnxCrossEncoder(config.RERANK_MODEL, tokenizer_repo=config.EMBED_MODEL)
    rel, unrel = ce.predict([("How long is the IPv6 header?", "The IPv6 header has a fixed length of 40 bytes."),
                             ("How long is the IPv6 header?", "DHCP runs over UDP on port 67.")])
    assert rel > 2 > 0 > unrel   # logits: clearly relevant vs clearly unrelated (thresholds used by citations.py)


def test_outdated_index_is_reembedded(tmp_path, sample_pdf, monkeypatch):
    chroma = ingest.get_client(str(tmp_path))   # own database: documents are keyed by content, don't touch shared state
    monkeypatch.setattr(config, "EMBEDDER_ID", "intfloat/multilingual-e5-small|torch")
    res = ingest.ingest_file(sample_pdf, "old.pdf", client=chroma)
    monkeypatch.setattr(config, "EMBEDDER_ID", "intfloat/multilingual-e5-small|onnx")
    assert "old.pdf" in ingest.reembed_outdated(chroma)
    assert "old.pdf" not in ingest.reembed_outdated(chroma)                 # only once
    col = chroma.get_collection(res.doc_id)
    assert col.metadata["embedder"].endswith("|onnx")
    from core.retriever import retrieve
    assert retrieve("How long is the UDP header?", [res.doc_id], k=1, client=chroma, mode="vector")[0]["page"] == 3
