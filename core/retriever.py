"""Retrieval over one or more per-document Chroma collections: vector, keyword (BM25), hybrid
(Reciprocal Rank Fusion) and hybrid + cross-encoder re-ranking."""
from __future__ import annotations

import re
import threading
import unicodedata
from functools import lru_cache

import numpy as np

from core import config
from core.ingest import embed_query, fmt_ref, get_client


def _page_filter(page_range: tuple[int, int] | None) -> dict | None:
    if not page_range:
        return None
    lo, hi = page_range
    return {"$and": [{"page": {"$gte": int(lo)}}, {"page": {"$lte": int(hi)}}]}


def _to_chunk(text: str, meta: dict, distance: float | None = None) -> dict:
    unit = meta.get("unit", "page")  # documents indexed before Phase 1 have no unit: they were PDFs
    return {
        "text": text,
        "filename": meta.get("filename", "?"),
        "page": int(meta.get("page", 0)),
        "unit": unit,
        "ref": fmt_ref(unit, int(meta.get("page", 0))),
        "chunk_index": int(meta.get("chunk_index", 0)),
        "doc_id": meta.get("doc_id", ""),
        # cosine distance -> similarity in [0, 1]-ish; higher is better
        "score": None if distance is None else round(1 - float(distance), 4),
    }


def _collection(client, doc_id: str):
    """The document's collection, or None if it was deleted (old quiz questions can point to it)."""
    try:
        return client.get_collection(doc_id)
    except Exception:  # chromadb raises NotFoundError (ValueError in older versions)
        return None


MODES = ("vector", "keyword", "hybrid", "hybrid+rerank")
CANDIDATES = config.RERANK_CANDIDATES   # chunks each method proposes before fusion / re-ranking
RRF_K = 60        # Reciprocal Rank Fusion constant (the usual default from the original paper)


def _key(c: dict) -> tuple:
    return c["doc_id"], c["page"], c["chunk_index"]


def retrieve(query: str, doc_ids: list[str], k: int = config.TOP_K, page_range: tuple[int, int] | None = None,
             client=None, mode: str | None = None) -> list[dict]:
    """Top-k chunks across all selected documents.

    mode (default: RETRIEVAL_MODE in .env):
      vector         meaning-based search with the e5 embeddings
      keyword        BM25 keyword search only
      hybrid         both lists merged with Reciprocal Rank Fusion
      hybrid+rerank  hybrid, then a cross-encoder re-scores the candidates
    Every result keeps a cosine-similarity "score" so the UI and topic filtering work in all modes."""
    mode = mode or config.RETRIEVAL_MODE
    if mode not in MODES:
        raise ValueError(f"unknown retrieval mode {mode!r}; use one of {MODES}")
    client = client or get_client()
    if not doc_ids or not query.strip():
        return []
    q_emb = embed_query(query)
    if mode == "vector":
        return _vector(q_emb, doc_ids, k, page_range, client)
    n = max(CANDIDATES, k)
    keyword_hits = _bm25(query, doc_ids, n, page_range, client)
    if mode == "keyword":
        hits = keyword_hits
    else:
        hits = _rrf([_vector(q_emb, doc_ids, n, page_range, client), keyword_hits])
    _fill_scores(hits[:n], q_emb, client)
    if mode == "hybrid+rerank":
        hits = rerank(query, hits[:n])
    return hits[:k]


def _vector(q_emb: list[float], doc_ids: list[str], k: int, page_range, client) -> list[dict]:
    where = _page_filter(page_range)
    hits: list[dict] = []
    for doc_id in doc_ids:
        col = _collection(client, doc_id)
        n = col.count() if col else 0
        if n == 0:
            continue
        res = col.query(query_embeddings=[q_emb], n_results=min(k, n), where=where,
                        include=["documents", "metadatas", "distances"])
        for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(_to_chunk(text, meta, dist))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:k]


# ---------------------------------------------------------------- keyword search (BM25)
_STOPWORDS = set("""a an the of to in on for and or is are was were be been by with as at from that this these those it
its what which how why does do did when who whom into than then there their can could should would will about
""".split()) | {"ما", "هو", "هي", "في", "من", "على", "عن", "إلى", "الى", "كيف", "متى", "لماذا", "هل", "التي", "الذي", "و"}
_bm25_cache: dict[tuple, tuple] = {}


def tokenize(text: str) -> list[str]:
    """Lower-case words; English stop words dropped; Arabic 'ال' prefix removed so ال-forms match."""
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())
    out = []
    for w in words:
        if w in _STOPWORDS or (len(w) == 1 and not w.isdigit()):
            continue
        if w.startswith("ال") and len(w) > 4:
            w = w[2:]
        out.append(w)
    return out


def _bm25_index(doc_ids: list[str], client):
    """One BM25 index over all chunks of the selected documents (IDF must be shared to compare scores)."""
    key = (id(client), tuple(sorted(doc_ids)))
    chunks = all_chunks(doc_ids, client=client)
    cached = _bm25_cache.get(key)
    if cached and cached[0] == len(chunks):
        return cached[1], cached[2]
    from rank_bm25 import BM25Okapi
    index = BM25Okapi([tokenize(c["text"]) for c in chunks]) if chunks else None
    _bm25_cache[key] = (len(chunks), index, chunks)
    return index, chunks


def _bm25(query: str, doc_ids: list[str], n: int, page_range, client) -> list[dict]:
    index, chunks = _bm25_index(doc_ids, client)
    terms = tokenize(query)
    if index is None or not terms:
        return []
    scores = index.get_scores(terms)
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    out = []
    for i in ranked:
        if scores[i] <= 0:
            break
        c = chunks[i]
        if page_range and not (page_range[0] <= c["page"] <= page_range[1]):
            continue
        out.append({**c, "bm25": round(float(scores[i]), 3)})
        if len(out) == n:
            break
    return out


def _rrf(rankings: list[list[dict]]) -> list[dict]:
    """Reciprocal Rank Fusion: score = sum over lists of 1 / (RRF_K + rank). Robust because it ignores
    the raw scores, which are on different scales for BM25 and cosine similarity."""
    fused: dict[tuple, dict] = {}
    for ranking in rankings:
        for rank, c in enumerate(ranking, start=1):
            entry = fused.setdefault(_key(c), {**c, "rrf": 0.0})
            entry["rrf"] += 1 / (RRF_K + rank)
            for extra in ("score", "bm25"):
                if c.get(extra) is not None:
                    entry[extra] = c[extra]
    return sorted(fused.values(), key=lambda c: c["rrf"], reverse=True)


def _fill_scores(hits: list[dict], q_emb: list[float], client) -> None:
    """Keyword-only hits have no cosine similarity yet: compute it from their stored embeddings."""
    missing = [h for h in hits if h.get("score") is None]
    by_doc: dict[str, list[dict]] = {}
    for h in missing:
        by_doc.setdefault(h["doc_id"], []).append(h)
    for doc_id, items in by_doc.items():
        col = _collection(client, doc_id)
        if col is None:
            continue
        ids = [f"{doc_id}-p{h['page']}-c{h['chunk_index']}" for h in items]
        res = col.get(ids=ids, include=["embeddings"])
        emb = {i: e for i, e in zip(res["ids"], res["embeddings"])}
        for h, i in zip(items, ids):
            if i in emb:
                h["score"] = round(float(np.dot(emb[i], q_emb)), 4)


# ---------------------------------------------------------------- re-ranking (cross-encoder)
_rerank_lock = threading.Lock()
reranker_error: str | None = None   # set if the model could not be loaded (the UI shows it)


@lru_cache(maxsize=1)
def _load_reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(config.RERANK_MODEL, max_length=512, device="cpu")


def rerank(query: str, hits: list[dict]) -> list[dict]:
    """A cross-encoder reads the question and each chunk together, which judges relevance better than
    comparing two separate embeddings, but is too slow to run on every chunk, hence only on candidates."""
    global reranker_error
    if not hits:
        return hits
    try:
        with _rerank_lock:
            model = _load_reranker()
    except Exception as e:  # e.g. first run without internet: fall back to the hybrid order
        reranker_error = f"{type(e).__name__}: {e}"[:300]
        return hits
    scores = model.predict([(query, h["text"]) for h in hits])
    for h, s in zip(hits, scores):
        h["rerank"] = round(float(s), 4)
    return sorted(hits, key=lambda h: h["rerank"], reverse=True)


def all_chunks(doc_ids: list[str], page_range: tuple[int, int] | None = None, client=None) -> list[dict]:
    """Every chunk of the selected documents in reading order."""
    client = client or get_client()
    out: list[dict] = []
    for doc_id in doc_ids:
        col = _collection(client, doc_id)
        if col is None:
            continue
        res = col.get(where=_page_filter(page_range), include=["documents", "metadatas"])
        out.extend(_to_chunk(t, m) for t, m in zip(res["documents"], res["metadatas"]))
    out.sort(key=lambda c: (c["doc_id"], c["page"], c["chunk_index"]))
    return out


def sample_spread(doc_ids: list[str], n: int, page_range: tuple[int, int] | None = None,
                  client=None, phase: float = 0.0) -> list[dict]:
    """n chunks spread evenly across the document(s) - used for MCQs without a topic.
    `phase` (0..1) shifts the sampling points, so two calls can pick different chunks."""
    chunks = all_chunks(doc_ids, page_range, client)
    if len(chunks) <= n:
        return chunks
    step = len(chunks) / n
    return [chunks[int((i + phase) * step) % len(chunks)] for i in range(n)]
