"""Central settings, loaded from .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Public demo (Hugging Face Space): sample lectures only, per-visitor progress, LLM budget per visitor.
DEMO_MODE = os.getenv("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")
DEMO_ACTIONS = int(os.getenv("DEMO_ACTIONS", 25))        # LLM calls one visitor may spend
DEMO_MAX_QUESTIONS = int(os.getenv("DEMO_MAX_QUESTIONS", 10))
REPO_URL = os.getenv("REPO_URL", "")                      # shown in the demo banner

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
# --- OpenAI-compatible APIs (LLM_PROVIDER=groq, or LLM_PROVIDER=openai for any other compatible service).
# Several models can be listed: free tiers have per-model daily quotas, so when one model's quota runs out
# the next one is used.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODELS = [m.strip() for m in os.getenv(
    "GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b").split(",") if m.strip()]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")          # e.g. https://openrouter.ai/api/v1
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODELS = [m.strip() for m in os.getenv("OPENAI_MODELS", "").split(",") if m.strip()]
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
# onnx (default): the models' int8 ONNX exports via ONNX Runtime. Measured on the benchmark: same
#   accuracy as full precision (hit@1 92% vs 90%), 69% less memory (668 MB vs 2,155 MB), faster,
#   and no PyTorch install. See core/onnx_backend.py and eval/results.md.
# torch: full-precision models via sentence-transformers (pip install -r requirements-torch.txt).
MODEL_BACKEND = os.getenv("MODEL_BACKEND", "onnx").strip().lower()
EMBEDDER_ID = f"{os.getenv('EMBED_MODEL', 'intfloat/multilingual-e5-small')}|{MODEL_BACKEND}"
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
