"""Central settings, loaded from .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Downloaded models (embeddings now; re-ranker / Whisper / OCR later) live inside the project
# folder instead of ~/.cache, which sits on a nearly full C: drive on the dev machine.
# Must be set before sentence-transformers / huggingface_hub are imported.
MODEL_CACHE = os.getenv("MODEL_CACHE", str(ROOT / "models"))
os.environ.setdefault("HF_HOME", MODEL_CACHE)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()  # ollama | claude | fake
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
# Only sent for models that support it (qwen3, deepseek-r1...). Empty = don't send the flag.
_think = os.getenv("OLLAMA_THINK", "").strip().lower()
OLLAMA_THINK = None if _think == "" else _think in ("1", "true", "yes")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
CHROMA_DIR = os.getenv("CHROMA_DIR", str(ROOT / "data" / "chroma"))
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "studyrag.db"))  # quiz history / progress

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 800))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 150))
TOP_K = int(os.getenv("TOP_K", 5))
# vector | keyword | hybrid | hybrid+rerank. The default was chosen by eval/benchmark.py
# (hit@1 78% -> 90% vs vector-only; see eval/results.md).
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "hybrid+rerank")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", 10))  # 10 vs 20: same accuracy, half the time

# A page with fewer extractable characters than this is treated as "probably scanned".
MIN_PAGE_CHARS = 30
