"""Retrieval benchmark: compare search modes on 50 hand-written questions (eval/benchmark.json).

    python eval/benchmark.py                               # vector, keyword, hybrid
    python eval/benchmark.py --modes vector hybrid+rerank  # any subset
    python eval/benchmark.py --write                       # also save eval/results.md

A question is a hit@k if one of its gold (file, page) pairs is among the top-k chunks. MRR uses the
rank of the first gold chunk in the top 5. Everything is indexed into a temporary database.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ingest, retriever  # noqa: E402
from samples.make_benchmark_corpus import corpus  # noqa: E402

BENCH = Path(__file__).with_name("benchmark.json")
RESULTS = Path(__file__).with_name("results.md")
CATEGORIES = ["keyword", "paraphrase", "confusable", "arabic"]


def evaluate(mode: str, questions: list[dict], doc_ids: list[str], client, k: int = 5) -> dict:
    per_cat: dict[str, list] = defaultdict(list)
    misses, t0 = [], time.perf_counter()
    for q in questions:
        hits = retriever.retrieve(q["q"], doc_ids, k=k, client=client, mode=mode)
        found = [(h["filename"], h["page"]) for h in hits]
        gold = {(f, p) for f, p in q["gold"]}
        rank = next((i for i, f in enumerate(found, 1) if f in gold), None)
        per_cat[q["category"]].append(rank)
        if rank != 1:
            misses.append((q["q"], sorted(gold), found[:3], rank))
    ms = 1000 * (time.perf_counter() - t0) / len(questions)

    def stats(ranks):
        n = len(ranks)
        return {"n": n, **{f"hit@{j}": sum(r is not None and r <= j for r in ranks) / n for j in (1, 3, 5)},
                "mrr": sum(1 / r for r in ranks if r) / n}

    all_ranks = [r for rs in per_cat.values() for r in rs]
    return {"mode": mode, "overall": stats(all_ranks), "by_category": {c: stats(per_cat[c]) for c in CATEGORIES},
            "ms_per_query": ms, "misses": misses}


def table(results: list[dict]) -> str:
    lines = ["| Mode | hit@1 | hit@3 | hit@5 | MRR@5 | " + " | ".join(f"{c} hit@1" for c in CATEGORIES) + " | ms/query |",
             "|---|" + "---:|" * (5 + len(CATEGORIES))]
    for r in results:
        o = r["overall"]
        cats = " | ".join(f"{r['by_category'][c]['hit@1']:.0%}" for c in CATEGORIES)
        lines.append(f"| `{r['mode']}` | **{o['hit@1']:.0%}** | {o['hit@3']:.0%} | {o['hit@5']:.0%} | {o['mrr']:.3f} | "
                     f"{cats} | {r['ms_per_query']:.0f} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["vector", "keyword", "hybrid"], choices=retriever.MODES)
    ap.add_argument("--write", action="store_true", help="save the table to eval/results.md")
    ap.add_argument("--misses", action="store_true", help="list the questions not answered at rank 1")
    args = ap.parse_args()

    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    files = bench["files"]
    questions = [{**q, "gold": [[files[f], p] for f, p in q["gold"]]} for q in bench["questions"]]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client = ingest.get_client(tmp)
        doc_ids = []
        for path in corpus():
            res = ingest.ingest_file(path.read_bytes(), path.name, client=client)
            doc_ids.append(res.doc_id)
        n_chunks = sum(len(retriever.all_chunks([d], client=client)) for d in doc_ids)
        print(f"Corpus: {len(doc_ids)} documents, {n_chunks} chunks; {len(questions)} questions\n")
        retriever.retrieve("warm up", doc_ids, client=client, mode=args.modes[-1])  # load models before timing
        results = [evaluate(m, questions, doc_ids, client) for m in args.modes]

    md = table(results)
    print(md)
    if args.misses:
        for r in results:
            print(f"\n--- {r['mode']}: not at rank 1")
            for q, gold, found, rank in r["misses"]:
                print(f"  [{rank or '-'}] {q}\n      gold {gold}\n      got  {found}")
    if args.write:
        RESULTS.write_text(
            f"# Retrieval benchmark\n\n_{date.today()} · {len(doc_ids)} documents, {n_chunks} chunks, "
            f"{len(questions)} hand-written questions (`eval/benchmark.json`) · top-k = 5 · CPU embeddings_\n\n"
            f"{md}\n\nhit@k: a gold page is among the top k chunks. MRR@5: mean of 1/rank of the first gold "
            f"chunk. Categories: keyword ({bench['categories']['keyword']}), paraphrase, confusable, "
            f"arabic (Arabic questions about English slides).\n", encoding="utf-8")
        print(f"\nSaved {RESULTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
