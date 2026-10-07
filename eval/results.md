# Retrieval benchmark

_2026-10-07 · 3 documents, 31 chunks, 50 hand-written questions (`eval/benchmark.json`) · top-k = 5 · model backend: `onnx`_

| Mode | hit@1 | hit@3 | hit@5 | MRR@5 | keyword hit@1 | paraphrase hit@1 | confusable hit@1 | arabic hit@1 | ms/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `vector` | **76%** | 94% | 96% | 0.842 | 92% | 50% | 100% | 60% | 12 |
| `keyword` | **76%** | 90% | 94% | 0.832 | 92% | 64% | 86% | 60% | 13 |
| `hybrid` | **84%** | 94% | 96% | 0.888 | 100% | 71% | 100% | 60% | 20 |
| `hybrid+rerank` | **92%** | 96% | 98% | 0.942 | 100% | 71% | 100% | 100% | 478 |

hit@k: a gold page is among the top k chunks. MRR@5: mean of 1/rank of the first gold chunk. Categories: keyword (exact terms, numbers or identifiers that appear in the text), paraphrase, confusable, arabic (Arabic questions about English slides).

# Citation check

_50 benchmark questions answered by `qwen3:4b-instruct` with `hybrid+rerank` retrieval._

| | answers citing a gold page | citations pointing to a gold page |
|---|---:|---:|
| Model as written | 48/48 (100%) | 100% of 52 |
| After citation check | 48/48 (100%) | 100% of 52 |

0 citations corrected; 4 answers with a sentence flagged as not clearly supported (support judged by: cross-encoder).

**Injection test** (each citation replaced by another retrieved page): 27/50 wrong citations repaired (54%), 24 back to the exact original page; 0 false corrections on the clean answers.

# Model backend: full precision vs int8 ONNX

_Same benchmark, `hybrid+rerank` mode. Memory = resident memory after loading both models (embeddings + re-ranker)._

| Backend | hit@1 | MRR@5 | Arabic hit@1 | Memory | Time / question | Install |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Full precision (PyTorch, sentence-transformers) | 90% | 0.930 | 90% | 2,155 MB | 0.71 s | ~1.6 GB |
| **int8 ONNX (ONNX Runtime) - default** | **92%** | **0.942** | **100%** | **668 MB** | **0.51 s** | ~0.7 GB |

The two backends differ by one or two questions, i.e. the same accuracy. Memory dropped from 2.2 GB to 0.7 GB
(int8 weights, no PyTorch, ONNX Runtime's memory pool turned off, and one shared tokenizer: the e5 and mmarco
XLM-R tokenizers produced identical ids on every benchmark text and question-chunk pair). This is what lets the
demo run on Streamlit Community Cloud's ~1 GB.
