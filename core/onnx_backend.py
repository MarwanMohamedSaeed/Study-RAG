"""Lightweight model backend: 8-bit ONNX models run with ONNX Runtime, without PyTorch.

Same models as the default backend (multilingual-e5-small, mmarco-mMiniLMv2 re-ranker), but the
int8-quantized ONNX exports published in their Hugging Face repositories: about a quarter of the
memory and no 2 GB PyTorch install. Used by the online demo (Streamlit Community Cloud gives ~1 GB
of RAM) and selectable locally with MODEL_BACKEND=onnx. Quality is checked with eval/benchmark.py.

The classes mimic the two methods the app uses: SentenceTransformer.encode() and CrossEncoder.predict().
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

INT8_FILE = "onnx/model_qint8_avx512_vnni.onnx"
MAX_TOKENS = 512


@lru_cache(maxsize=4)
def _tokenizer(repo: str):
    """A 250k-word XLM-R tokenizer takes ~270 MB of RAM, so it is loaded once per repository."""
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json"))
    tok.enable_truncation(max_length=MAX_TOKENS)
    tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 0, pad_token="<pad>")
    return tok


def _session(repo: str, onnx_file: str, tokenizer_repo: str | None = None):
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    model_path = hf_hub_download(repo, onnx_file)
    tok = _tokenizer(tokenizer_repo or repo)
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    # No pre-allocated memory pool: ~60 MB less per model (measured) for a small speed cost.
    opts.enable_cpu_mem_arena = False
    opts.enable_mem_pattern = False
    sess = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
    return sess, tok, {i.name for i in sess.get_inputs()}


def _feeds(encodings, input_names: set[str]) -> dict:
    feeds = {"input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
             "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64)}
    if "token_type_ids" in input_names:
        feeds["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
    return {k: v for k, v in feeds.items() if k in input_names}


class OnnxEmbedder:
    """Mean-pooled sentence embeddings (the pooling multilingual-e5 is trained with)."""

    def __init__(self, repo: str, onnx_file: str = INT8_FILE):
        self.sess, self.tok, self.inputs = _session(repo, onnx_file)

    def encode(self, texts, normalize_embeddings: bool = False, batch_size: int = 32, **_):
        single = isinstance(texts, str)
        texts = [texts] if single else list(texts)
        out = []
        for i in range(0, len(texts), batch_size):
            enc = self.tok.encode_batch(texts[i:i + batch_size])
            feeds = _feeds(enc, self.inputs)
            hidden = self.sess.run(None, feeds)[0]                       # (batch, tokens, dim)
            mask = feeds["attention_mask"][..., None].astype(np.float32)
            emb = (hidden * mask).sum(1) / np.clip(mask.sum(1), 1e-9, None)
            if normalize_embeddings:
                emb = emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
            out.append(emb.astype(np.float32))
        result = np.vstack(out) if out else np.zeros((0, 0), dtype=np.float32)
        return result[0] if single else result

    def get_sentence_embedding_dimension(self) -> int:
        return int(self.encode("dimension probe").shape[-1])


class OnnxCrossEncoder:
    """Relevance logits for (query, passage) pairs, like CrossEncoder.predict().

    tokenizer_repo lets it reuse the embedder's tokenizer: the e5 and mmarco tokenizers are both
    XLM-R and produced identical ids for every benchmark text and (question, chunk) pair (measured),
    which saves ~270 MB."""

    def __init__(self, repo: str, onnx_file: str = INT8_FILE, tokenizer_repo: str | None = None):
        self.sess, self.tok, self.inputs = _session(repo, onnx_file, tokenizer_repo)

    def predict(self, pairs, batch_size: int = 16, **_):
        pairs = list(pairs)
        scores = []
        for i in range(0, len(pairs), batch_size):
            enc = self.tok.encode_batch([(q, p) for q, p in pairs[i:i + batch_size]])
            logits = self.sess.run(None, _feeds(enc, self.inputs))[0]
            scores.extend(np.asarray(logits).reshape(len(enc), -1)[:, 0].tolist())
        return np.array(scores, dtype=np.float32)
