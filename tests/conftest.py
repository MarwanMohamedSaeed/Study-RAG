import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

config.LLM_PROVIDER = "fake"  # tests never call a real model
config.RETRIEVAL_MODE = "hybrid"  # no 450 MB re-ranker download in tests / CI (it has its own tests with a stub)

SAMPLE = ROOT / "samples" / "networks_lecture.pdf"


@pytest.fixture(scope="session")
def sample_pdf() -> bytes:
    if not SAMPLE.exists():
        from samples.make_sample_pdf import build
        build(SAMPLE)
    return SAMPLE.read_bytes()


@pytest.fixture(scope="session")
def chroma(tmp_path_factory):
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(tmp_path_factory.mktemp("chroma")),
                                     settings=Settings(anonymized_telemetry=False))


@pytest.fixture(scope="session")
def indexed(sample_pdf, chroma):
    from core.ingest import ingest_pdf
    return ingest_pdf(sample_pdf, "networks_lecture.pdf", client=chroma)
