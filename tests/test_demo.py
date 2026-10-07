"""Demo mode (public Hugging Face Space), run end to end with Streamlit's AppTest."""
import contextvars
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import config, ingest, retriever, store

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def demo(chroma, tmp_path, monkeypatch):
    """Demo mode on a temporary database; the sample lectures are preloaded into it."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_ACTIONS", 3)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "shared.db"))
    monkeypatch.setattr(ingest, "get_client", lambda *a, **k: chroma)
    monkeypatch.setattr(retriever, "get_client", lambda *a, **k: chroma)
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    return at


def test_demo_banner_samples_and_no_uploads(demo):
    sidebar_text = " ".join(i.value for i in demo.sidebar.info)
    assert "Live demo" in sidebar_text and "3** AI actions left" in sidebar_text
    assert len(demo.get("file_uploader")) == 0                       # nobody can upload into a shared server
    names = {d["filename"] for d in ingest.list_documents()}
    assert {"networks_lecture.pdf", "network_layer_lecture.pdf", "routing_slides.pptx"} <= names


def test_demo_budget_is_spent_and_enforced(demo):
    demo.chat_input[0].set_value("How long is the UDP header?").run()
    assert not demo.exception and demo.session_state["demo_used"] == 1
    assert "**2** AI actions left" in " ".join(i.value for i in demo.sidebar.info)   # updated in the same run
    demo.session_state["demo_used"] = 3
    demo.chat_input[0].set_value("And the TCP header?").run()
    assert any("used its AI budget" in w.value for w in demo.warning)


def test_use_db_is_per_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "shared.db"))
    from core.schemas import MCQ
    q = MCQ(question="Which port does DNS use?", options=["53", "80", "22", "443"], correct="A",
            explanation="DNS uses 53.", source_page=2)

    def visitor(name):
        store.use_db(str(tmp_path / f"{name}.db"))
        store.record_attempt([q], ["A"], ["d"], ["x.pdf"])
        return store.totals()["quizzes"]

    assert contextvars.copy_context().run(visitor, "alice") == 1
    assert contextvars.copy_context().run(visitor, "bob") == 1      # bob does not see alice's quiz
    assert store.totals()["quizzes"] == 0                           # nor does the shared database
