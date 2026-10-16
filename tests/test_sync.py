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


def _person_edited(tmp_path):
    """A person changed a heading in Outline while the file gained a new paragraph."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\n## Kind on the record\n\nFirst line.\n\nOld line.\n")
    o = FakeOutlineWithPeople()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    [(_, doc, _, _)] = s.publish_status(["notes.md"])
    d = o.docs[doc["id"]]
    d["text"] = d["text"].replace("## Kind on the record", "## Subjects")
    o.revisions[doc["id"]] = [{"createdAt": "2099-01-01T00:00:00Z",
                               "createdBy": {"id": "u-kostas", "name": "Kostas"}}]
    o.notes[doc["id"]] = [{"id": "c1"}]
    (root / "notes.md").write_text("# Notes\n\n## Kind on the record\n\nFirst line.\n\nOld line.\n\nNew line.\n")
    assert s.publish_status(["notes.md"])[0][2] == "conflict"
    assert s.accept_file("notes.md").action == "refused"     # a person edited it
    return root, o, s, doc["id"]


def test_accept_merged_refuses_until_the_file_carries_the_outline_edit(tmp_path):
    root, o, s, doc_id = _person_edited(tmp_path)
    r = s.accept_merged("notes.md")
    assert r.action == "refused" and "## Subjects" in r.detail
    assert "New line." not in o.docs[doc_id]["text"]
    (root / "notes.md").write_text("# Notes\n\n## Subjects\n\nFirst line.\n\nOld line.\n\nNew line.\n")
    r = s.accept_merged("notes.md")
    assert r.action == "patched", r
    text = o.docs[doc_id]["text"]
    assert "## Subjects" in text and "New line." in text and "Kind on the record" not in text
    assert s.publish_status(["notes.md"])[0][2] == "ok"


def test_accept_merged_refuses_a_line_removed_in_outline_that_the_file_still_has(tmp_path):
    root, o, s, doc_id = _person_edited(tmp_path)
    d = o.docs[doc_id]
    d["text"] = d["text"].replace("\n\nOld line.", "")
    (root / "notes.md").write_text("# Notes\n\n## Subjects\n\nFirst line.\n\nOld line.\n\nNew line.\n")
    r = s.accept_merged("notes.md")
    assert r.action == "refused" and "Old line." in r.detail and "removed" in r.detail
    (root / "notes.md").write_text("# Notes\n\n## Subjects\n\nFirst line.\n\nNew line.\n")
    assert s.accept_merged("notes.md").action == "patched"
    assert "Old line." not in o.docs[doc_id]["text"]


def test_accept_merged_undoes_only_what_the_person_agreed_to_undo(tmp_path):
    root, o, s, doc_id = _person_edited(tmp_path)
    d = o.docs[doc_id]
    d["text"] = d["text"].replace("First line.", "First line. A slip")
    (root / "notes.md").write_text("# Notes\n\n## Subjects\n\nFirst line.\n\nOld line.\n\nNew line.\n")
    assert s.accept_merged("notes.md").action == "refused"            # the slip is not in the file
    r = s.accept_merged("notes.md", undo=("A slip",))
    assert r.action == "patched", r
    text = o.docs[doc_id]["text"]
    assert "A slip" not in text and "## Subjects" in text and "New line." in text

def test_accept_merged_refuses_when_nobody_edited_in_outline(tmp_path):
    root, o, s = _world(tmp_path)
    s.publish_status(["notes.md"])
    r = s.accept_merged("notes.md")
    assert r.action == "refused" and "nobody edited" in r.detail
    assert s.accept_merged("missing.md").action == "refused"


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


class OutlineRefusesPatches(FakeOutline):
    """An Outline that takes no patch at all: every patch is counted and changes nothing."""

    def __init__(self):
        super().__init__()
        self.patches = 0

    def patch(self, doc_id: str, find: str, text: str) -> dict:
        self.patches += 1
        return dict(self.docs[doc_id])


def test_a_patch_outline_will_not_take_is_not_sent_again_until_the_file_changes(tmp_path):
    # every patch is an update to the document, and a failed one used to be retried on every pass:
    # 81,566 updates in a day on 17 documents, each a revision and a webhook delivery
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nFirst line.\n\nSecond line.\n")
    o = OutlineRefusesPatches()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    s.publish_status(["notes.md"])
    (root / "notes.md").write_text("# Notes\n\nFirst line, changed.\n\nSecond line.\n")
    [(_, _, status, _)] = s.publish_status(["notes.md"])
    assert status == "cannot-patch"
    tried = o.patches
    assert tried > 0
    for _ in range(3):
        [(_, _, status, _)] = s.publish_status(["notes.md"])
        assert status == "cannot-patch"
    assert o.patches == tried, f"{o.patches - tried} more patches sent for a file that did not change"
    (root / "notes.md").write_text("# Notes\n\nFirst line, changed again.\n\nSecond line.\n")
    s.publish_status(["notes.md"])
    assert o.patches > tried, "a new version of the file is tried again"


class OutlineSpellsPatchesItsOwnWay(FakeOutline):
    """Outline as it is: a patch's result spells two literal asterisks `\\*\\*` and ends a line of a
    paragraph with a backslash, where a whole-body save wrote `\\**` and a plain newline."""

    def patch(self, doc_id: str, find: str, text: str) -> dict:
        super().patch(doc_id, find, text)
        # Outline stores the patched document in its own spelling, whatever the patch said
        d = self.docs[doc_id]
        d["text"] = d["text"].replace("\\**", "\\*\\*").replace("stop\n", "stop\\\n")
        return dict(d)


def test_outlines_own_spelling_of_a_patch_is_published_not_cannot_patch(tmp_path):
    # 2026-10-16: fifteen documents (and two of purchases' notes) stayed at cannot-patch, "find text
    # not present: '\\**'", because a hunk set aside for its spelling broke the hunks planned after it
    root = tmp_path / "repo"
    root.mkdir()
    (root / "note.md").write_text("# Note\n\nIntro.\n\nOld section.\n")
    o = OutlineSpellsPatchesItsOwnWay()
    s = Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))
    s.publish_status(["note.md"])
    (root / "note.md").write_text("# Note\n\nIntro, longer now.\n\nA price of \\** 3, then stop\nand go on.\n\n"
                                  "**Bold** section, rewritten whole.\n")
    [(_, doc, status, detail)] = s.publish_status(["note.md"])
    assert status == "ok", (status, detail, o.docs[doc["id"]]["text"])
    text = o.docs[doc["id"]]["text"]
    assert "Intro, longer now." in text and "rewritten whole" in text and "Old section" not in text
