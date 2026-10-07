"""Citation accuracy of real LLM answers on the benchmark, before and after core/citations.check().

    python eval/citations.py            # answers are cached in data/eval_answers.json (delete to regenerate)
    python eval/citations.py --write    # also append the table to eval/results.md

"Gold citation" = a cited page that is one of the question's gold pages. This is a proxy: an answer
may also cite a correct secondary page, so precision below 100% is not always an error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, ingest, rag, retriever  # noqa: E402
from core.citations import check  # noqa: E402
from eval.benchmark import BENCH, write_section  # noqa: E402
from samples.make_benchmark_corpus import corpus  # noqa: E402

CACHE = ROOT / "data" / "eval_answers.json"


def cited_pages(answer: str) -> list[tuple[str, int]]:
    out = []
    for label in rag.CITE_RE.findall(answer):
        name, _, ref = label.rpartition(" ")
        if " " in name and name.endswith((" slide", " part")):   # "file.pptx slide 3"
            name, ref = name.rsplit(" ", 1)[0], label.rsplit(" ", 1)[1]
        try:
            out.append((name, int(ref.replace("p.", ""))))
        except ValueError:
            continue
    return out


def score(rows: list[dict], key: str) -> dict:
    answered = [r for r in rows if not rag.is_not_found(r[key])]
    with_cites = [r for r in answered if cited_pages(r[key])]
    cites_gold = [r for r in with_cites if any(c in r["gold"] for c in cited_pages(r[key]))]
    all_cites = [c for r in answered for c in cited_pages(r[key])]
    gold_cites = [c for r in answered for c in cited_pages(r[key]) if c in r["gold"]]
    return {"answered": len(answered), "with_citation": len(with_cites), "cites_gold": len(cites_gold),
            "precision": len(gold_cites) / max(1, len(all_cites)), "n_citations": len(all_cites)}


def injection_test(cache: dict, seed: int = 0) -> tuple[int, int, int, int]:
    """Replace each citation with another retrieved page; count repairs and false corrections.
    Returns (false corrections on clean answers, injected, repaired, restored to the original page)."""
    import random
    rng = random.Random(seed)
    false_fix, injected, repaired, restored = 0, 0, 0, 0
    for v in cache.values():
        a, src = v["answer"], v["sources"]
        if rag.is_not_found(a) or not rag.CITE_RE.findall(a):
            continue
        false_fix += len(check(a, src).corrected)
        labels = sorted({f"{s['filename']} {s['ref']}" for s in src})
        for lab in set(rag.CITE_RE.findall(a)):
            wrong = [x for x in labels if x != lab]
            if not wrong:
                continue
            w = rng.choice(wrong)
            injected += 1
            fixes = check(a.replace(f"[{lab}]", f"[{w}]"), src).corrected
            repaired += any(old == w for old, _ in fixes)
            restored += any(old == w and new == lab for old, new in fixes)
    return false_fix, injected, repaired, restored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    files = bench["files"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client = ingest.get_client(tmp)
        retriever.get_client = lambda *a, **k: client
        doc_ids = [ingest.ingest_file(p.read_bytes(), p.name, client=client).doc_id for p in corpus()]
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        rows, t0 = [], time.time()
        for i, q in enumerate(bench["questions"], 1):
            gold = [(files[f], p) for f, p in q["gold"]]
            if q["q"] not in cache:
                out = rag.answer(q["q"], doc_ids)
                cache[q["q"]] = {"answer": out["answer"], "sources": out["sources"]}
                CACHE.parent.mkdir(exist_ok=True)
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {i}/{len(bench['questions'])} answered ({time.time() - t0:.0f}s)", flush=True)
            a, sources = cache[q["q"]]["answer"], cache[q["q"]]["sources"]
            fixed = check(a, sources)
            rows.append({"q": q["q"], "category": q["category"], "gold": gold, "before": a, "after": fixed.text,
                         "corrected": fixed.corrected, "unsupported": fixed.unsupported, "scorer": fixed.scorer})

    b, a = score(rows, "before"), score(rows, "after")
    n_corr = sum(len(r["corrected"]) for r in rows)
    n_unsup = sum(bool(r["unsupported"]) for r in rows)
    md = ("| | answers citing a gold page | citations pointing to a gold page |\n|---|---:|---:|\n"
          f"| Model as written | {b['cites_gold']}/{b['answered']} ({b['cites_gold'] / max(1, b['answered']):.0%}) "
          f"| {b['precision']:.0%} of {b['n_citations']} |\n"
          f"| After citation check | {a['cites_gold']}/{a['answered']} ({a['cites_gold'] / max(1, a['answered']):.0%}) "
          f"| {a['precision']:.0%} of {a['n_citations']} |\n\n"
          f"{n_corr} citations corrected; {n_unsup} answers with a sentence flagged as not clearly supported "
          f"(support judged by: {rows[0]['scorer'] if rows else '-'}).")
    false_fix, injected, repaired, restored = injection_test(cache)
    md += (f"\n\n**Injection test** (each citation replaced by another retrieved page): "
           f"{repaired}/{injected} wrong citations repaired ({repaired / max(1, injected):.0%}), "
           f"{restored} back to the exact original page; {false_fix} false corrections on the clean answers.")
    print(f"\n{md}\n")
    for r in rows:
        if r["corrected"] or r["unsupported"]:
            ok = lambda txt: "gold" if any(c in r["gold"] for c in cited_pages(txt)) else "not gold"
            print(f"- {r['q'][:70]}\n    corrected {r['corrected']} unsupported {[u[:70] for u in r['unsupported']]}"
                  f"  ({ok(r['before'])} -> {ok(r['after'])})")
    if args.write:
        write_section("Citation check", f"_{len(rows)} benchmark questions answered by `{config.OLLAMA_MODEL}` with "
                                        f"`{config.RETRIEVAL_MODE}` retrieval._\n\n{md}")
        print("Saved eval/results.md")


if __name__ == "__main__":
    main()
