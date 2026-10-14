"""The inbox keeps every comment of a thread, the agent's own included, and when a thread is resolved.

An app that shows a document's threads reads them from margin's store, so the store holds both sides
of the conversation. The agent's own comments are stored as already delivered and with no route,
so they are never handed to anyone as work.
"""
from pathlib import Path

from margin.config import Config, Repo
from margin.inbox import Inbox
from margin.store import Store

AGENT, PERSON = "agent-1", "person-1"


class FakeOutline:
    def me(self):
        return {"id": AGENT}

    def comment(self, cid):
        return {"id": cid, "anchorText": ""}

    def structure(self, doc_id):
        return {}


def make(tmp_path: Path) -> tuple[Inbox, Store]:
    repo = Repo(name="notes", path=tmp_path, route="cli:notes-chat")
    cfg = Config(url="https://outline.example.org", api_key_command=["true"], admin_key_command=None,
                 pin_ip_command=None, listen="127.0.0.1:0", webhook_url="", secret_command=None,
                 poll_seconds=60, quiet_seconds=1, cap_seconds=1, notify_command=None, notify_cwd=None,
                 mcp_url=None, author_command=None, repos=[repo], state=tmp_path / "m.db")
    store = Store(cfg.state)
    store.save_doc("notes", "plan.md", "doc-1", "col-1", "text", "text")
    return Inbox(cfg, FakeOutline(), store), store


def comment(cid, author, parent=None, anchor="a passage", resolved=None):
    return {"id": cid, "documentId": "doc-1", "parentCommentId": parent, "createdById": author,
            "createdBy": {"name": "Agent" if author == AGENT else "Person"}, "anchorText": anchor,
            "data": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": cid}]}]},
            "createdAt": f"2026-10-14T10:00:0{cid[-1]}.000Z", "resolvedAt": resolved}


def test_both_sides_of_a_thread_are_kept_and_only_the_person_is_work(tmp_path):
    inbox, store = make(tmp_path)
    assert inbox.record(comment("c1", PERSON), "webhook") is True
    assert inbox.record(comment("c2", AGENT, parent="c1"), "webhook") is False   # not new work
    rows = {r["id"]: r for r in store.db.execute("SELECT * FROM comment").fetchall()}
    assert set(rows) == {"c1", "c2"}
    assert rows["c2"]["route"] is None and rows["c2"]["delivered_at"] is not None
    assert [r["id"] for r in store.pending()] == ["c1"]


def test_a_resolve_and_a_reopen_are_recorded(tmp_path):
    inbox, store = make(tmp_path)
    inbox.record(comment("c1", PERSON), "webhook")
    model = comment("c1", PERSON, resolved="2026-10-14T11:00:00.000Z")
    assert inbox.on_webhook({"event": "comments.update", "payload": {"id": "c1", "model": model}}) == "updated"
    at = store.db.execute("SELECT resolved_at FROM comment WHERE id='c1'").fetchone()[0]
    assert at == "2026-10-14T11:00:00.000Z"
    inbox.on_webhook({"event": "comments.update", "payload": {"id": "c1", "model": comment("c1", PERSON)}})
    assert store.db.execute("SELECT resolved_at FROM comment WHERE id='c1'").fetchone()[0] is None


def test_an_older_store_gains_the_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE comment (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, parent_id TEXT, "
               "author_id TEXT, author TEXT, text TEXT, anchor TEXT, created_at TEXT NOT NULL, "
               "received_at REAL NOT NULL, mentions_agent INTEGER NOT NULL DEFAULT 0, route TEXT, "
               "via TEXT NOT NULL, delivered_at REAL, batch_id TEXT)")
    db.commit()
    db.close()
    store = Store(path)
    cols = {r["name"] for r in store.db.execute("PRAGMA table_info(comment)").fetchall()}
    assert "resolved_at" in cols


def test_where_a_passage_sits_is_read_from_the_marks():
    from margin.inbox import anchor_before
    mark = {"type": "comment", "attrs": {"id": "c1"}}
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Search is first. "}]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Then a "},
            {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
            {"type": "text", "text": " word and "},
            {"type": "text", "text": "search", "marks": [mark]},
            {"type": "text", "text": " again."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "opens", "marks": [
            {"type": "comment", "attrs": {"id": "c2"}}]}]}]}
    assert anchor_before(doc, "c1") == "Then a bold word and "
    assert anchor_before(doc, "c2") == ""                       # the passage opens its paragraph
    assert anchor_before(doc, "c3") is None                     # no mark for it
    long = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "x" * 100}, {"type": "text", "text": "here", "marks": [mark]}]}]}
    assert anchor_before(long, "c1") == "x" * 40


def test_a_new_comment_on_a_passage_records_where_it_sits(tmp_path):
    inbox, store = make(tmp_path)
    doc = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "Before the "},
        {"type": "text", "text": "a passage", "marks": [{"type": "comment", "attrs": {"id": "c1"}}]}]}]}
    inbox.o.structure = lambda doc_id: doc
    inbox.record(comment("c1", PERSON), "webhook")
    inbox.record(comment("c2", AGENT, parent="c1"), "webhook")
    rows = {r["id"]: r["anchor_before"] for r in store.db.execute("SELECT id, anchor_before FROM comment").fetchall()}
    assert rows == {"c1": "Before the ", "c2": None}
    assert [r["id"] for r in store.without_anchor_before()] == []


def test_after_a_change_each_passage_is_placed_again_or_marked_lost(tmp_path):
    inbox, store = make(tmp_path)
    inbox.o.structure = lambda doc_id: {}
    inbox.record(comment("c1", PERSON), "webhook")
    inbox.record(comment("c3", PERSON), "webhook")
    assert sorted(r["id"] for r in store.without_anchor_before()) == ["c1", "c3"]
    inbox.o.structure = lambda doc_id: {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "Kept: "},
        {"type": "text", "text": "a passage", "marks": [{"type": "comment", "attrs": {"id": "c1"}}]}]}]}
    assert inbox.place("doc-1") == (1, 1)
    rows = {r["id"]: (r["anchor_before"], r["anchor_lost"]) for r in
            store.db.execute("SELECT id, anchor_before, anchor_lost FROM comment").fetchall()}
    assert rows == {"c1": ("Kept: ", 0), "c3": (None, 1)}
    assert list(store.without_anchor_before()) == []
