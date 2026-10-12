"""Syncer against a fake Outline: what publish reports for each file."""
from pathlib import Path

from margin.config import Repo
from margin.store import Store
from margin.sync import Syncer


class FakeOutline:
    """Just enough of Outline for a sync: documents held in memory, patches as text replaces."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    def _new(self, title: str, text: str) -> dict:
        doc_id = f"d{len(self.docs) + 1}"
        self.docs[doc_id] = {"id": doc_id, "title": title, "text": text, "url": f"/doc/{doc_id}"}
        return dict(self.docs[doc_id])

    def data(self, method: str, body: dict | None = None):
        if method == "collections.info":
            return {"id": body["id"]}
        if method == "documents.create":
            return self._new(body["title"], body["text"])
        raise AssertionError(method)

    def ensure_collection(self, name: str, description: str = "") -> dict:
        return {"id": "c1"}

    def create_document(self, collection_id, title, text, parent_id=None):
        return self._new(title, text)

    def document(self, doc_id: str) -> dict:
        return dict(self.docs[doc_id])

    def replace(self, doc_id: str, text: str) -> dict:
        self.docs[doc_id]["text"] = text
        return dict(self.docs[doc_id])

    def patch(self, doc_id: str, find: str, text: str) -> dict:
        d = self.docs[doc_id]
        d["text"] = d["text"].replace(find, text, 1)
        return dict(d)

    def append(self, doc_id: str, text: str) -> dict:
        self.docs[doc_id]["text"] += text
        return dict(self.docs[doc_id])

    def set_title(self, doc_id: str, title: str) -> dict:
        self.docs[doc_id]["title"] = title
        return dict(self.docs[doc_id])


def _world(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nSecond line.\n")
    o = FakeOutline()
    return root, o, Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))


def test_publish_status_is_ok_when_the_document_says_what_the_file_says(tmp_path):
    root, o, s = _world(tmp_path)
    [(rel, doc, status, detail)] = s.publish_status(["notes.md"])
    assert (rel, status, detail) == ("notes.md", "ok", "")
    (root / "notes.md").write_text("# Notes\n\nFirst line, changed.\n\nSecond line.\n")
    [(_, doc, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok" and "First line, changed." in o.docs[doc["id"]]["text"]


def test_publish_status_says_conflict_when_both_sides_changed(tmp_path):
    """A conflict leaves the document with the older text, so a caller must not hand its link out
    as published (margin link once did, and a section was reported published that never was)."""
    root, o, s = _world(tmp_path)
    [(_, doc, _, _)] = s.publish_status(["notes.md"])
    o.docs[doc["id"]]["text"] += "\n\nA line typed in Outline."
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nSecond line.\n\nA line in the file.\n")
    [(_, doc, status, detail)] = s.publish_status(["notes.md"])
    assert status == "conflict" and "both" in detail
    assert "A line in the file." not in o.docs[doc["id"]]["text"]
    assert s.publish(["notes.md"])[0][0] == "notes.md"      # publish keeps its old shape


class FakeOutlineWithPeople(FakeOutline):
    """Adds who edited (revisions) and comments, for accept_file."""

    def __init__(self):
        super().__init__()
        self.revisions: dict[str, list[dict]] = {}
        self.notes: dict[str, list[dict]] = {}

    def me(self) -> dict:
        return {"id": "u-margin", "name": "Claude"}

    def comments(self, collection_id=None, document_id=None):
        return list(self.notes.get(document_id, []))

    def paged(self, method: str, body: dict | None = None, limit: int = 100):
        assert method == "revisions.list"
        return iter(self.revisions.get(body["documentId"], []))


def _conflict(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n")
    o = FakeOutlineWithPeople()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    [(_, doc, _, _)] = s.publish_status(["notes.md"])
    o.docs[doc["id"]]["text"] += "\n\nAn older draft margin wrote."
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nThe file's new line.\n")
    assert s.publish_status(["notes.md"])[0][2] == "conflict"
    return root, o, s, doc["id"]


def test_accept_file_lets_the_file_win_when_only_margin_wrote_the_document(tmp_path):
    root, o, s, doc_id = _conflict(tmp_path)
    o.revisions[doc_id] = [{"createdAt": "2099-01-01T00:00:00Z", "createdBy": {"id": "u-margin"}}]
    r = s.accept_file("notes.md")
    assert r.action == "patched", r
    assert "The file's new line." in o.docs[doc_id]["text"]
    assert "An older draft margin wrote." not in o.docs[doc_id]["text"]
    assert s.publish_status(["notes.md"])[0][2] == "ok"


def test_accept_file_refuses_a_person_or_a_comment(tmp_path):
    root, o, s, doc_id = _conflict(tmp_path)
    o.revisions[doc_id] = [{"createdAt": "2099-01-01T00:00:00Z",
                            "createdBy": {"id": "u-kostas", "name": "Kostas"}}]
    r = s.accept_file("notes.md")
    assert r.action == "refused" and "Kostas" in r.detail
    o.revisions[doc_id] = []
    o.notes[doc_id] = [{"id": "c1"}]
    r = s.accept_file("notes.md")
    assert r.action == "refused" and "comment" in r.detail
    assert "The file's new line." not in o.docs[doc_id]["text"]
    assert s.accept_file("missing.md").action == "refused"


class OutlineKeepsEmptyParagraphs(FakeOutline):
    """Outline keeps an empty paragraph a patch tries to remove: such a patch changes nothing."""

    def patch(self, doc_id: str, find: str, text: str) -> dict:
        if "\n\n\n" in self.docs[doc_id]["text"] and find.count("\n") > text.count("\n"):
            return dict(self.docs[doc_id])
        return super().patch(doc_id, find, text)


def test_an_empty_paragraph_does_not_stop_the_changes_above_it(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "report.md").write_text("# Report\n\nIt shipped in three days.\n\nA line.\n\n* an item\n")
    o = OutlineKeepsEmptyParagraphs()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    [(_, doc, _, _)] = s.publish_status(["report.md"])
    # someone's empty paragraph before the list, as Outline writes it, accepted as the new base
    o.docs[doc["id"]]["text"] = o.docs[doc["id"]]["text"].replace("A line.\n\n*", "A line.\n\n\n*")
    s.s.save_doc("repo", "report.md", doc["id"], "c1", s.s.doc("repo", "report.md")["base_repo"],
                 o.docs[doc["id"]]["text"], "ok", None)
    (root / "report.md").write_text("# Report\n\nIt shipped in four days.\n\nA line.\n\n* an item\n")
    [(_, doc, status, detail)] = s.publish_status(["report.md"])
    assert "four days" in o.docs[doc["id"]]["text"], o.docs[doc["id"]]["text"]
    assert status == "ok", detail
