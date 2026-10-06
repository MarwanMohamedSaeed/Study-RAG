"""Retrieval evaluation: hit rate@k and MRR on hand-written Q&A pairs.

A question is a "hit" if any of the top-k retrieved chunks comes from the page that
contains the answer. Uses a temporary Chroma DB so it never touches your app data.

    python eval/run_eval.py              # retrieval only (fast, no LLM)
    python eval/run_eval.py --answers    # also generate answers with the configured LLM
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, ingest, rag, retriever  # noqa: E402

PDF = ROOT / "samples" / "networks_lecture.pdf"
PAIRS = Path(__file__).with_name("qa_pairs.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=config.TOP_K)
    ap.add_argument("--pdf", type=Path, default=PDF)
    ap.add_argument("--pairs", type=Path, default=PAIRS)
    ap.add_argument("--answers", action="store_true", help="also run the LLM and print answers")
    args = ap.parse_args()

    if not args.pdf.exists():
        from samples.make_sample_pdf import build
        build(args.pdf)
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))

    # Chroma keeps SQLite handles open, so on Windows the temp dir may not delete cleanly.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client = ingest.get_client(tmp)
        # make rag.answer() use the temporary DB too
        retriever.get_client = lambda *a, **k: client
        res = ingest.ingest_pdf(args.pdf.read_bytes(), args.pdf.name, client=client)
        print(f"Indexed {res.n_chunks} chunks from {res.n_pages} pages of {args.pdf.name}\n")

        hits, rr = 0, 0.0
        for i, p in enumerate(pairs, 1):
            got = retriever.retrieve(p["question"], [res.doc_id], k=args.k, client=client)
            pages = [c["page"] for c in got]
            hit = p["page"] in pages
            hits += hit
            rr += 1 / (pages.index(p["page"]) + 1) if hit else 0
            print(f"{'HIT ' if hit else 'MISS'} {i:2d}. {p['question'][:60]:<60} expected p.{p['page']}  got {pages}")
            if args.answers:
                out = rag.answer(p["question"], [res.doc_id], k=args.k)
                print(f"      answer: {out['answer'][:200]!r}\n      gold:   {p['answer']}")

    n = len(pairs)
    print(f"\nHit rate@{args.k}: {hits}/{n} = {hits / n:.0%}    MRR@{args.k}: {rr / n:.3f}")


if __name__ == "__main__":
    main()
