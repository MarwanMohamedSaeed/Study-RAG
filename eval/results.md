# Retrieval benchmark

_2026-10-07 · 3 documents, 31 chunks, 50 hand-written questions (`eval/benchmark.json`) · top-k = 5 · CPU embeddings_

| Mode | hit@1 | hit@3 | hit@5 | MRR@5 | keyword hit@1 | paraphrase hit@1 | confusable hit@1 | arabic hit@1 | ms/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `vector` | **78%** | 92% | 94% | 0.848 | 92% | 57% | 100% | 60% | 42 |
| `keyword` | **76%** | 90% | 94% | 0.832 | 92% | 64% | 86% | 60% | 40 |
| `hybrid` | **82%** | 94% | 94% | 0.873 | 100% | 64% | 100% | 60% | 53 |
| `hybrid+rerank` | **90%** | 94% | 98% | 0.930 | 100% | 71% | 100% | 90% | 713 |

hit@k: a gold page is among the top k chunks. MRR@5: mean of 1/rank of the first gold chunk. Categories: keyword (exact terms, numbers or identifiers that appear in the text), paraphrase, confusable, arabic (Arabic questions about English slides).

# Citation check

_50 benchmark questions answered by `qwen3:4b-instruct` with `hybrid+rerank` retrieval._

| | answers citing a gold page | citations pointing to a gold page |
|---|---:|---:|
| Model as written | 48/48 (100%) | 100% of 52 |
| After citation check | 48/48 (100%) | 100% of 52 |

0 citations corrected; 4 answers with a sentence flagged as not clearly supported (support judged by: cross-encoder).

**Injection test** (each citation replaced by another retrieved page): 28/50 wrong citations repaired (56%), 25 back to the exact original page; 0 false corrections on the clean answers.
