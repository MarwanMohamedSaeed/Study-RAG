"""Ingestion pipeline: PDF -> page text -> chunks -> embeddings -> ChromaDB.

One Chroma collection per uploaded document. The collection name is derived from
the file's SHA-1, so re-uploading the same PDF reuses the existing index.
"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

import pymupdf as fitz

from core import config

# Separators tried in order, coarsest first. Includes Arabic question mark / comma.
SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "؟ ", "، ", "; ", " ", ""]
MIN_CHUNK_CHARS = 20
EMBED_BATCH = 32

ProgressCb = Callable[[float, str], None]


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    n_pages: int
    n_chunks: int
    empty_pages: list[int] = field(default_factory=list)
    already_indexed: bool = False

    @property
    def warning(self) -> str | None:
        if self.n_chunks == 0:
            return (f"No extractable text in '{self.filename}'. It is probably a scanned/image-only "
                    "PDF. Run OCR on it first (e.g. `ocrmypdf in.pdf out.pdf`) and upload again.")
        if self.empty_pages and len(self.empty_pages) >= max(1, self.n_pages // 2):
            return (f"{len(self.empty_pages)}/{self.n_pages} pages of '{self.filename}' have no text "
                    "(scanned images?). Only the text pages were indexed.")
        return None


# --------------------------------------------------------------------------- chunking
def _normalize(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_recursive(text: str, seps: list[str], size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    sep = next(s for s in seps if s == "" or s in text)
    if sep == "":
        return [text[i:i + size] for i in range(0, len(text), size)]
    raw = text.split(sep)
    # keep the separator attached so merged chunks read naturally
    parts = [p + sep for p in raw[:-1]] + [raw[-1]]
    rest = seps[seps.index(sep) + 1:]
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        out.extend([p] if len(p) <= size else _split_recursive(p, rest, size))
    return out


def _merge(pieces: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in pieces:
        if cur and cur_len + len(p) > size:
            chunks.append("".join(cur).strip())
            # drop pieces from the front until only ~overlap chars remain to carry over
            while cur and (cur_len > overlap or cur_len + len(p) > size):
                cur_len -= len(cur.pop(0))
        cur.append(p)
        cur_len += len(p)
    if cur:
        chunks.append("".join(cur).strip())
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def split_text(text: str, chunk_size: int = config.CHUNK_SIZE,
               overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """Recursive character splitter (~chunk_size chars, ~overlap chars shared between neighbours)."""
    text = _normalize(text)
    if not text:
        return []
    return _merge(_split_recursive(text, SEPARATORS, chunk_size), chunk_size, overlap)


# --------------------------------------------------------------------------- PDF parsing
def extract_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Return [(page_number starting at 1, text)] for every page.

    NFKC folds Arabic presentation forms (U+FBxx/U+FExx, which many PDFs emit for shaped
    glyphs) back to normal letters, and expands ligatures like 'ﬁ'. Without it Arabic
    text neither embeds nor matches queries properly."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [(i + 1, unicodedata.normalize("NFKC", page.get_text("text"))) for i, page in enumerate(doc)]


def chunk_pages(pages: list[tuple[int, str]]) -> tuple[list[dict], list[int]]:
    """Chunk page by page so every chunk maps to exactly one page number."""
    chunks, empty = [], []
    for page_no, text in pages:
        if len(text.strip()) < config.MIN_PAGE_CHARS:
            empty.append(page_no)
            continue
        for i, c in enumerate(split_text(text)):
            chunks.append({"text": c, "page": page_no, "chunk_index": i})
    return chunks, empty


# --------------------------------------------------------------------------- embeddings / store
_embedder_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.EMBED_MODEL, device="cpu")


def get_embedder():
    with _embedder_lock:  # avoid loading the model twice when Streamlit threads race
        return _load_embedder()


def embed_passages(texts: list[str]) -> list[list[float]]:
    # e5 models are trained with "passage: " / "query: " prefixes.
    vecs = get_embedder().encode([f"passage: {t}" for t in texts], normalize_embeddings=True,
                                 batch_size=EMBED_BATCH)
    return vecs.tolist()


def embed_query(text: str) -> list[float]:
    return get_embedder().encode(f"query: {text}", normalize_embeddings=True).tolist()


_client_lock = threading.Lock()
_clients: dict[str, object] = {}


def get_client(path: str = config.CHROMA_DIR):
    # Streamlit can run two script threads at once (a rerun while the first run is still going).
    # Building two PersistentClients concurrently yields a half-started client, hence the lock.
    with _client_lock:
        if path not in _clients:
            import chromadb
            from chromadb.config import Settings
            _clients[path] = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
        return _clients[path]


def doc_id_for(pdf_bytes: bytes) -> str:
    return "doc_" + hashlib.sha1(pdf_bytes).hexdigest()[:16]


def ingest_pdf(pdf_bytes: bytes, filename: str, progress: ProgressCb | None = None,
               client=None) -> IngestResult:
    progress = progress or (lambda f, m: None)
    client = client or get_client()
    doc_id = doc_id_for(pdf_bytes)

    progress(0.05, f"Reading {filename}")
    pages = extract_pages(pdf_bytes)
    chunks, empty = chunk_pages(pages)
    result = IngestResult(doc_id, filename, len(pages), len(chunks), empty)

    existing = {getattr(c, "name", c) for c in client.list_collections()}
    if doc_id in existing and client.get_collection(doc_id).count() > 0:
        result.already_indexed = True
        progress(1.0, f"{filename} already indexed")
        return result
    if not chunks:
        progress(1.0, "No text found")
        return result

    col = client.get_or_create_collection(
        doc_id, metadata={"filename": filename, "n_pages": len(pages), "hnsw:space": "cosine"})
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start:start + EMBED_BATCH]
        progress(0.1 + 0.9 * start / len(chunks), f"Embedding chunks {start + 1}-{start + len(batch)} of {len(chunks)}")
        col.add(
            ids=[f"{doc_id}-p{c['page']}-c{c['chunk_index']}" for c in batch],
            documents=[c["text"] for c in batch],
            embeddings=embed_passages([c["text"] for c in batch]),
            metadatas=[{"filename": filename, "page": c["page"], "chunk_index": c["chunk_index"],
                        "doc_id": doc_id} for c in batch],
        )
    progress(1.0, f"Indexed {len(chunks)} chunks from {filename}")
    return result


def list_documents(client=None) -> list[dict]:
    client = client or get_client()
    docs = []
    for c in client.list_collections():
        col = client.get_collection(getattr(c, "name", c))
        meta = col.metadata or {}
        docs.append({"doc_id": col.name, "filename": meta.get("filename", col.name),
                     "n_pages": meta.get("n_pages", 0), "n_chunks": col.count()})
    return sorted(docs, key=lambda d: d["filename"].lower())


def delete_document(doc_id: str, client=None) -> None:
    (client or get_client()).delete_collection(doc_id)
