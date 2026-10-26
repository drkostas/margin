"""margin's own edits are never mistaken for a person's (2026-10-13).

A long plan with tables failed to patch part way. The patches that had applied changed Outline's
text, the baseline was not moved, and the next pass read margin's own edits as someone else's and
called the document a conflict. A fake Outline stands in: it applies patches like Outline does,
except that it can refuse any patch that touches a given text, the way the tables did.
"""
from pathlib import Path

from margin.config import Repo
from margin.store import Store
from margin.sync import Syncer

MARGIN = "margin-user"


class FakeOutline:
    def __init__(self, text, updated_by=MARGIN, revisions=(MARGIN,), refuse=None, comments=()):
        self.text, self.updated_by, self.revs, self.refuse = text, updated_by, list(revisions), refuse
        self.notes = list(comments)

    def comments(self, collection_id=None, document_id=None):
        return list(self.notes)

    def me(self):
        return {"id": MARGIN}

    def data(self, method, body=None):
        return {"id": "c1"}

    def document(self, doc_id):
        return {"id": doc_id, "text": self.text, "title": "T", "updatedBy": {"id": self.updated_by}}

    def patch(self, doc_id, find, text):
        if self.refuse and self.refuse in find:
            return {"text": self.text}                    # Outline could not apply it
        assert find in self.text
        self.text = self.text.replace(find, text, 1)
        return {"text": self.text}

    def append(self, doc_id, text):
        self.text += text
        return {"text": self.text}

    def replace(self, doc_id, text):                      # the private scratch draft only
        return {"text": text}

    def set_title(self, doc_id, title):
        return {}

    def revisions(self, doc_id):
        return [{"createdBy": {"id": u}} for u in self.revs]


def setup(tmp_path: Path, outline_text: str, base_outline: str, file_body: str):
    repo = Repo(name="r", path=tmp_path)
    store = Store(tmp_path / "margin.db")
    store.put("collection:r", "c1")
    store.put("scratch:r", "s1")
    store.save_doc("r", "doc.md", "d1", "c1", "# T\n\nold file\n", base_outline)
    (tmp_path / "doc.md").write_text(f"# T\n\n{file_body}\n")
    return repo, store


def test_a_failed_patch_keeps_what_it_changed_as_the_baseline(tmp_path):
    # a comment is anchored in it, so it stays patch-only; with none and no person's edit it would
    # be replaced whole instead (test_sync_replace.py, 2026-10-26)
    start = "a\n\nb\n\nmid\n\nc"
    repo, store = setup(tmp_path, start, start, "a\n\nB\n\nmid\n\nC")
    o = FakeOutline(start, refuse="c", comments=[{"id": "n1", "anchorText": "mid"}])
    first = Syncer(o, store, repo).one("doc.md", "doc.md")
    assert first.action == "cannot-patch"
    assert o.text == "a\n\nB\n\nmid\n\nc"                 # the b patch applied, the c one did not
    assert store.doc("r", "doc.md")["base_outline"] == o.text
    again = Syncer(o, store, repo).one("doc.md", "doc.md")
    assert again.action == "cannot-patch"                # retried, not called a conflict


def test_a_difference_only_margin_made_is_not_a_conflict(tmp_path):
    repo, store = setup(tmp_path, "a\n\nb\n\nx", "a\n\nb", "a\n\nB\n\nx")
    o = FakeOutline("a\n\nb\n\nx")
    out = Syncer(o, store, repo).one("doc.md", "doc.md")
    assert out.action == "patched" and o.text == "a\n\nB\n\nx"


def test_a_persons_edit_is_still_a_conflict(tmp_path):
    for kw in ({"revisions": (MARGIN, "person")}, {"updated_by": "person"}, {"revisions": ()}):
        repo, store = setup(tmp_path, "a\n\nb\n\nx", "a\n\nb", "a\n\nB\n\nx")
        o = FakeOutline("a\n\nb\n\nx", **kw)
        out = Syncer(o, store, repo).one("doc.md", "doc.md")
        assert out.action == "conflict", kw
        assert o.text == "a\n\nb\n\nx"                    # left alone
