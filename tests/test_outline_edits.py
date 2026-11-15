"""An edit made in Outline between two publishes is found when margin publishes again.

margin does not subscribe to documents.update: Outline keeps a row for every delivery, and margin's own
patches made most of them. So nothing tells margin about an edit when it happens. When the file changes
and margin is about to write the document, it compares the document's updatedAt with the one it
recorded right after its own last write, and reads who wrote it only when that moved. A fake Outline
stands in: every write moves updatedAt and records its author and a revision.
"""
import sqlite3
from pathlib import Path

import pytest

from margin.config import Config, Repo
from margin.inbox import EVENTS, Inbox
from margin.store import Store
from margin.sync import Syncer

ME, ALEX = "u-margin", "u-alex"


class FakeOutline:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.revs: dict[str, list[dict]] = {}
        self.clock = 0
        self.calls: list[str] = []

    def _stamp(self, doc_id: str, who: str) -> None:
        self.clock += 1
        at = f"2099-01-01T00:00:{self.clock:02d}.000Z"
        self.docs[doc_id].update(updatedAt=at, updatedBy={"id": who, "name": "Alex" if who == ALEX else "Claude"})
        self.revs.setdefault(doc_id, []).append({"createdAt": at, "createdBy": {"id": who}})

    def edit_as(self, doc_id: str, who: str, text: str) -> None:
        """A write by someone in Outline's editor."""
        self.docs[doc_id]["text"] = text
        self._stamp(doc_id, who)

    # ---- the calls margin makes
    def me(self):
        return {"id": ME}

    def ensure_collection(self, name, description=""):
        return {"id": "c1"}

    def data(self, method, body=None):
        if method == "collections.info":
            return {"id": body["id"]}
        if method == "documents.create":                  # margin's private scratch draft
            return self.create_document(body.get("collectionId"), body["title"], body["text"])
        raise AssertionError(method)

    def create_document(self, collection_id, title, text, parent_id=None):
        doc_id = f"d{len(self.docs) + 1}"
        self.docs[doc_id] = {"id": doc_id, "title": title, "text": text, "url": f"/doc/{doc_id}",
                             "collectionId": collection_id}
        self._stamp(doc_id, ME)
        return dict(self.docs[doc_id])

    def document(self, doc_id):
        self.calls.append("documents.info")
        return dict(self.docs[doc_id])

    def patch(self, doc_id, find, text):
        d = self.docs[doc_id]
        d["text"] = d["text"].replace(find, text, 1)
        self._stamp(doc_id, ME)
        return dict(d)

    def append(self, doc_id, text):
        self.docs[doc_id]["text"] += text
        self._stamp(doc_id, ME)
        return dict(self.docs[doc_id])

    def replace(self, doc_id, text):
        self.docs[doc_id]["text"] = text
        self._stamp(doc_id, ME)
        return dict(self.docs[doc_id])

    def set_title(self, doc_id, title):
        self.docs[doc_id]["title"] = title
        self._stamp(doc_id, ME)
        return dict(self.docs[doc_id])

    def comments(self, collection_id=None, document_id=None):
        return []

    def documents(self, collection_id, limit=5):
        self.calls.append("documents.list")
        rows = [d for d in self.docs.values() if d.get("collectionId") == collection_id]
        for d in sorted(rows, key=lambda d: d["updatedAt"], reverse=True):
            self.calls.append(f"listed {d['id']}")
            yield dict(d)

    def paged(self, method, body=None, limit=100):
        assert method == "revisions.list"
        self.calls.append("revisions.list")
        return iter(list(self.revs.get(body["documentId"], [])))

    def revisions(self, doc_id):
        return list(self.paged("revisions.list", {"documentId": doc_id}))


def world(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nSecond line.\n")
    o = FakeOutline()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    [(_, doc, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok"
    return root, o, s, doc["id"]


def change_file(root: Path) -> None:
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nSecond line.\n\nA line in the file.\n")


def test_an_edit_made_in_outline_between_two_publishes_is_found_at_the_next(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.edit_as(doc_id, ALEX, o.docs[doc_id]["text"] + "\n\nA line typed in Outline.")
    change_file(root)
    [(_, _, status, detail)] = s.publish_status(["notes.md"])
    assert status == "conflict" and "both" in detail
    assert "A line typed in Outline." in o.docs[doc_id]["text"]        # the person's edit is kept
    assert "A line in the file." not in o.docs[doc_id]["text"]
    r = s.accept_file("notes.md")                                        # and accept-file still refuses
    assert r.action == "refused" and "Alex" in r.detail


def test_an_unedited_document_is_patched_without_reading_its_revisions(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    change_file(root)
    o.calls.clear()
    [(_, _, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok" and "A line in the file." in o.docs[doc_id]["text"]
    assert "revisions.list" not in o.calls


def test_a_text_that_differs_with_no_write_since_is_outlines_rendering(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.docs[doc_id]["text"] = o.docs[doc_id]["text"].replace("First line.", "First line.\n")   # no new updatedAt
    change_file(root)
    [(_, _, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok" and "A line in the file." in o.docs[doc_id]["text"]


def test_a_write_by_margin_it_did_not_record_is_not_an_edit(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.edit_as(doc_id, ME, o.docs[doc_id]["text"] + "\n\nAn older pass margin stopped part way.")
    change_file(root)
    [(_, _, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok" and "A line in the file." in o.docs[doc_id]["text"]


def test_a_person_who_wrote_before_margins_last_write_does_not_count(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.revs[doc_id].insert(0, {"createdAt": "2000-01-01T00:00:00.000Z", "createdBy": {"id": ALEX}})
    o.edit_as(doc_id, ME, o.docs[doc_id]["text"] + "\n\nAn older pass margin stopped part way.")
    change_file(root)
    [(_, _, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok"


def test_an_unchanged_file_asks_outline_nothing(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.edit_as(doc_id, ALEX, o.docs[doc_id]["text"] + "\n\nA line typed in Outline.")
    # a subscription made before this change still delivers the event once
    inbox(tmp_path, s.s).on_webhook({"event": "documents.update", "payload": {"id": doc_id}})
    o.calls.clear()
    assert s.one("notes.md", "notes.md").action == "unchanged"
    assert o.calls == []
    assert "A line typed in Outline." in o.docs[doc_id]["text"]


def test_a_document_synced_before_the_time_was_recorded_keeps_the_older_rule(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    s.s.db.execute("UPDATE doc SET outline_updated_at=NULL")
    o.edit_as(doc_id, ALEX, o.docs[doc_id]["text"] + "\n\nA line typed in Outline.")
    change_file(root)
    assert s.publish_status(["notes.md"])[0][2] == "conflict"


def test_each_write_records_the_documents_updated_at(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    row = s.s.doc("repo", "notes.md")
    assert row["outline_updated_at"] == o.docs[doc_id]["updatedAt"]
    change_file(root)
    s.publish_status(["notes.md"])
    assert s.s.doc("repo", "notes.md")["outline_updated_at"] == o.docs[doc_id]["updatedAt"]


# ---- the webhook and the store
class QuietOutline:
    def me(self):
        return {"id": ME}


def inbox(tmp_path: Path, store: Store) -> Inbox:
    cfg = Config(url="https://outline.example.org", api_key_command=["true"], admin_key_command=None,
                 pin_ip_command=None, listen="127.0.0.1:0", webhook_url="", secret_command=None,
                 poll_seconds=60, quiet_seconds=1, cap_seconds=1, notify_command=None, notify_cwd=None,
                 mcp_url=None, author_command=None, repos=[], state=store.path)
    return Inbox(cfg, QuietOutline(), store)


def test_margin_does_not_subscribe_to_document_updates(tmp_path):
    assert "documents.update" not in EVENTS
    assert {"comments.create", "comments.update"} <= set(EVENTS)
    store = Store(tmp_path / "m.db")
    # a delivery from a subscription made before this change is ignored and leaves nothing behind
    assert inbox(tmp_path, store).on_webhook({"event": "documents.update", "payload": {"id": "d1"}}) == "ignored"
    assert store.with_prefix("dirty:") == []


def test_a_store_made_before_the_column_gains_it_and_loses_the_old_marks(tmp_path):
    path = tmp_path / "m.db"
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE doc (repo TEXT NOT NULL, path TEXT NOT NULL, doc_id TEXT NOT NULL,
          collection_id TEXT NOT NULL, base_repo TEXT, base_outline TEXT,
          status TEXT NOT NULL DEFAULT 'ok', detail TEXT, synced_at REAL, PRIMARY KEY (repo, path));
        CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO doc VALUES ('r', 'a.md', 'd1', 'c1', 'x', 'x', 'ok', NULL, 1.0);
        INSERT INTO kv VALUES ('dirty:d1', '1'), ('pin:r:a.md', '/w/a.md');
    """)
    db.commit()
    db.close()
    s = Store(path)
    assert s.doc("r", "a.md")["outline_updated_at"] is None
    assert s.get("dirty:d1") is None and s.get("pin:r:a.md") == "/w/a.md"
    s.save_doc("r", "a.md", "d1", "c1", "y", "y", updated_at="2099-01-01T00:00:01.000Z")
    assert s.doc("r", "a.md")["outline_updated_at"] == "2099-01-01T00:00:01.000Z"


@pytest.mark.parametrize("status", ["conflict", "outline-edited"])
def test_accept_file_takes_a_conflict_nobody_wrote_since_without_reading_revisions(tmp_path, status):
    root, o, s, doc_id = world(tmp_path)
    change_file(root)
    s.s.mark("repo", "notes.md", status, "set by hand for this test")
    o.calls.clear()
    r = s.accept_file("notes.md")
    assert r.action == "patched", r
    assert "A line in the file." in o.docs[doc_id]["text"]
    assert "revisions.list" not in o.calls


# ---- the catch-up poll finds an edit made in Outline to a file that has not changed
def poller(tmp_path: Path, o: FakeOutline, s: Syncer) -> Inbox:
    cfg = Config(url="https://outline.example.org", api_key_command=["true"], admin_key_command=None,
                 pin_ip_command=None, listen="127.0.0.1:0", webhook_url="", secret_command=None,
                 poll_seconds=60, quiet_seconds=1, cap_seconds=1, notify_command=None, notify_cwd=None,
                 mcp_url=None, author_command=None, repos=[s.repo], state=s.s.path)
    return Inbox(cfg, o, s.s)


def test_the_poll_finds_an_outline_edit_on_an_unchanged_file(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    inbox = poller(tmp_path, o, s)
    inbox.poll()                                                   # the first poll learns where it is
    o.edit_as(doc_id, ALEX, o.docs[doc_id]["text"] + "\n\nA line typed in Outline.")
    inbox.poll()
    assert s.s.get(f"moved:{doc_id}") == o.docs[doc_id]["updatedAt"]
    r = s.one("notes.md", "notes.md")
    assert r.action == "outline-edited"
    assert "A line typed in Outline." in o.docs[doc_id]["text"]
    assert s.s.get(f"moved:{doc_id}") is None


def test_the_first_poll_flags_nothing(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    o.edit_as(doc_id, ALEX, o.docs[doc_id]["text"] + "\n\nA line typed in Outline.")
    poller(tmp_path, o, s).poll()
    assert s.s.with_prefix("moved:") == [] and s.s.get("docs-poll:repo") == o.docs[doc_id]["updatedAt"]


def test_margins_own_writes_are_not_flagged(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    inbox = poller(tmp_path, o, s)
    inbox.poll()
    change_file(root)
    s.publish_status(["notes.md"])                                 # margin writes and records the time
    inbox.poll()
    assert s.s.with_prefix("moved:") == []


def test_a_flagged_write_by_margin_is_recorded_so_it_is_checked_once(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    inbox = poller(tmp_path, o, s)
    inbox.poll()
    o.edit_as(doc_id, ME, o.docs[doc_id]["text"] + "\n\nAn older pass margin stopped part way.")
    inbox.poll()
    assert s.one("notes.md", "notes.md").action == "unchanged"
    assert s.s.doc("repo", "notes.md")["outline_updated_at"] == o.docs[doc_id]["updatedAt"]
    o.calls.clear()
    assert s.one("notes.md", "notes.md").action == "unchanged" and o.calls == []


def test_the_poll_reads_no_further_than_what_it_saw(tmp_path):
    root, o, s, doc_id = world(tmp_path)
    inbox = poller(tmp_path, o, s)
    inbox.poll()
    o.calls.clear()
    inbox.poll()                                                    # nothing moved since
    assert [c for c in o.calls if c.startswith("listed")] == [f"listed {doc_id}"]
