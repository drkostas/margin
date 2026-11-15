"""A file over Outline's length limit is reported in plain words and never sent.

Outline refuses a document text longer than its limit, so every pass sent the whole file again and logged
the refusal, while the stored status still read ok. margin now checks the length first. The limit is made
small here so the texts stay short.
"""
import sqlite3
from pathlib import Path

import pytest

from margin import sync
from margin.config import Repo
from margin.store import Store
from margin.sync import Syncer
from test_sync import FakeOutline


class CountingOutline(FakeOutline):
    def __init__(self):
        super().__init__()
        self.writes = []

    def create_document(self, collection_id, title, text, parent_id=None):
        self.writes.append("create")
        return super().create_document(collection_id, title, text, parent_id)

    def patch(self, doc_id, find, text):
        self.writes.append("patch")
        return super().patch(doc_id, find, text)

    def append(self, doc_id, text):
        self.writes.append("append")
        return super().append(doc_id, text)

    def replace(self, doc_id, text):
        self.writes.append("replace")
        return super().replace(doc_id, text)


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr(sync, "MAX_TEXT", 200)


def world(tmp_path: Path, text: str):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text(text)
    o = CountingOutline()
    return root, o, Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))


SHORT = "# Notes\n\nFirst line.\n"
LONG = "# Notes\n\n" + "A sentence that makes the file long. " * 10 + "\n"


def test_a_new_file_over_the_limit_is_never_sent_and_said_once(tmp_path):
    root, o, s = world(tmp_path, LONG)
    [(rel, doc, status, detail)] = s.publish_status(["notes.md"])
    assert (doc, status) == (None, "too-big") and "over Outline's limit of 200 characters" in detail
    assert "create" not in o.writes and o.docs == {}
    assert [r.action for r in s.run() if r.path == "notes.md"] == ["unchanged"]      # said once


def test_a_published_file_that_grows_over_the_limit_keeps_the_older_text(tmp_path):
    root, o, s = world(tmp_path, SHORT)
    [(_, doc, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok"
    before = o.docs[doc["id"]]["text"]
    (root / "notes.md").write_text(LONG)
    o.writes.clear()
    first = [r for r in s.run() if r.path == "notes.md"]
    assert first[0].action == "too-big" and first[0].detail.startswith("the file is ")
    assert "over Outline's limit of 200. Split it into smaller files" in first[0].detail
    assert o.writes == [] and o.docs[doc["id"]]["text"] == before
    row = s.s.doc("repo", "notes.md")
    assert row["status"] == "too-big" and row["text_len"] > 200
    assert [r.action for r in s.run() if r.path == "notes.md"] == ["unchanged"]       # said once a version
    assert s.s.doc("repo", "notes.md")["status"] == "too-big"                          # the status stays
    (root / "notes.md").write_text(LONG + "One more line.\n")
    assert [r.action for r in s.run() if r.path == "notes.md"] == ["too-big"]         # a new version, said again


def test_a_file_back_under_the_limit_is_published_again(tmp_path):
    root, o, s = world(tmp_path, SHORT)
    [(_, doc, _, _)] = s.publish_status(["notes.md"])
    (root / "notes.md").write_text(LONG)
    s.run()
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nA second line.\n")
    [(_, doc, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok" and "A second line." in o.docs[doc["id"]]["text"]
    assert s.s.get("too-big:repo:notes.md") is None


def test_each_pass_keeps_the_length_margin_would_publish(tmp_path):
    root, o, s = world(tmp_path, SHORT)
    s.publish_status(["notes.md"])
    s.run()
    first = s.s.doc("repo", "notes.md")["text_len"]
    assert first == len("First line.\n") or 0 < first < 200
    (root / "notes.md").write_text(SHORT + "\nMore.\n")
    s.run()
    assert s.s.doc("repo", "notes.md")["text_len"] > first


def test_a_store_made_before_the_length_column_gains_it(tmp_path):
    path = tmp_path / "m.db"
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE doc (repo TEXT NOT NULL, path TEXT NOT NULL, doc_id TEXT NOT NULL,
          collection_id TEXT NOT NULL, base_repo TEXT, base_outline TEXT,
          status TEXT NOT NULL DEFAULT 'ok', detail TEXT, synced_at REAL, outline_updated_at TEXT,
          PRIMARY KEY (repo, path));
        INSERT INTO doc VALUES ('r', 'a.md', 'd1', 'c1', 'x', 'x', 'ok', NULL, 1.0, NULL);
    """)
    db.commit()
    db.close()
    s = Store(path)
    assert s.doc("r", "a.md")["text_len"] is None
    s.set_text_len("r", "a.md", 12)
    assert s.doc("r", "a.md")["text_len"] == 12
