"""Retrieval over one or more per-document Chroma collections."""
from __future__ import annotations

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


def retrieve(query: str, doc_ids: list[str], k: int = config.TOP_K,
             page_range: tuple[int, int] | None = None, client=None) -> list[dict]:
    """Top-k chunks across all selected documents, merged by similarity."""
    client = client or get_client()
    if not doc_ids or not query.strip():
        return []
    q_emb = embed_query(query)
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
