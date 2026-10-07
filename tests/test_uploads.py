"""Private demo uploads (owner-tagged documents) and the Arabic text clean-ups."""
import time

import chromadb
import pytest
from chromadb.config import Settings

from core import ingest
from core.ingest import (delete_expired_uploads, doc_id_for, fix_rtl_brackets, ingest_file, list_documents,
                         visible_documents)
from core.rag import clean_citations


@pytest.fixture
def client(tmp_path):
    # its own database: these tests add and delete documents
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))


def test_doc_id_is_private_per_owner():
    data = b"same lecture bytes"
    assert doc_id_for(data) == doc_id_for(data)                       # re-upload reuses the index
    assert doc_id_for(data, "alice") != doc_id_for(data)              # a visitor's copy is separate
    assert doc_id_for(data, "alice") != doc_id_for(data, "bob")


def test_uploads_are_visible_only_to_their_owner(sample_pdf, client):
    shared = ingest_file(sample_pdf, "networks_lecture.pdf", client=client)
    alice = ingest_file(sample_pdf, "networks_lecture.pdf", client=client, owner="alice")
    assert not alice.already_indexed and alice.doc_id != shared.doc_id
    docs = list_documents(client)
    assert {d["owner"] for d in docs} == {None, "alice"}
    assert {d["doc_id"] for d in visible_documents(docs, "alice")} == {shared.doc_id, alice.doc_id}
    assert {d["doc_id"] for d in visible_documents(docs, "bob")} == {shared.doc_id}


def test_expired_uploads_are_deleted_shared_documents_kept(sample_pdf, client, monkeypatch):
    shared = ingest_file(sample_pdf, "networks_lecture.pdf", client=client)
    ingest_file(sample_pdf, "old.pdf", client=client, owner="alice")
    assert delete_expired_uploads(24, client) == 0                    # fresh upload: kept
    later = time.time() + 25 * 3600
    monkeypatch.setattr(ingest.time, "time", lambda: later)
    assert delete_expired_uploads(24, client) == 1
    assert [d["doc_id"] for d in list_documents(client)] == [shared.doc_id]


def test_rtl_brackets_unmirrored():
    assert fix_rtl_brackets("المكدس )LIFO(: آخر عنصر") == "المكدس (LIFO): آخر عنصر"
    assert fix_rtl_brackets("التعقيد O(1 ( ثابت") == "التعقيد O(1 ) ثابت"   # as extracted from a real PDF


@pytest.mark.parametrize("text", [
    "المكدس (LIFO): آخر عنصر",          # already correct
    "The stack (LIFO) is O(1).",        # no Arabic
    "قائمة بدون أقواس",                 # no brackets
])
def test_rtl_brackets_correct_text_unchanged(text):
    assert fix_rtl_brackets(text) == text


def test_clean_citations():
    assert clean_citations("الجواب 【lecture.pdf p.3】.") == "الجواب [lecture.pdf p.3]."
    assert clean_citations("See ［ slides.pptx slide 2 ］") == "See [slides.pptx slide 2]"
    assert clean_citations("Already [notes.pdf p.1]") == "Already [notes.pdf p.1]"
