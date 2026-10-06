"""Central settings, loaded from .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

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

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 800))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 150))
TOP_K = int(os.getenv("TOP_K", 5))

# A page with fewer extractable characters than this is treated as "probably scanned".
MIN_PAGE_CHARS = 30
