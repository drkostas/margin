"""A document nobody edited and nobody commented on is replaced whole when it cannot be patched.

A chat's plan was published by the serve pass, the chat went on rewriting it, and the patches from
the first rendering to the new text failed on its tables, so the chat's own `margin link` met a
document it could not change. With no comment anchored in it and no person's edit, a replace loses
nothing, which is the rule accept-file applied by hand.
"""
from pathlib import Path

import pytest

from margin.config import Repo
from margin.store import Store
from margin.sync import Syncer


class FakeOutline:
    """Renders text as it is, and refuses every patch, so converge() can never reach the target."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.comments_on: dict[str, list] = {}
        self.editors: dict[str, list[str]] = {}
        self.n = 0
        self.replaced: list[str] = []

    def me(self):
        return {"id": "margin", "name": "margin"}

    def data(self, method, body=None):
        if method == "collections.info":
            return {"id": body["id"]}
        if method == "documents.create":
            return self.create_document(body["collectionId"], body["title"], body["text"], None)
        raise AssertionError(method)

    def ensure_collection(self, name, description=""):
        return {"id": "col"}

    def _doc(self, doc_id):
        d = self.docs[doc_id]
        return {"id": doc_id, "title": d["title"], "text": d["text"],
                "updatedBy": {"id": self.editors[doc_id][-1]}}

    def create_document(self, collection_id, title, text, parent_id):
        self.n += 1
        doc_id = f"d{self.n}"
        self.docs[doc_id] = {"title": title, "text": text}
        self.editors[doc_id] = ["margin"]
        return self._doc(doc_id)

    def document(self, doc_id):
        return self._doc(doc_id)

    def patch(self, doc_id, find, text):
        return self._doc(doc_id)             # nothing changes: the patch "did not take"

    def append(self, doc_id, text):
        return self._doc(doc_id)

    def replace(self, doc_id, text):
        self.docs[doc_id]["text"] = text
        self.editors[doc_id].append("margin")
        self.replaced.append(doc_id)
        return self._doc(doc_id)

    def set_title(self, doc_id, title):
        self.docs[doc_id]["title"] = title
        return self._doc(doc_id)

    def revisions(self, doc_id):
        return [{"createdBy": {"id": who}} for who in self.editors[doc_id]]

    def comments(self, collection_id=None, document_id=None):
        return list(self.comments_on.get(document_id, []))


V1 = "# A plan\n\n| Term | Meaning |\n|---|---|\n| one | the first meaning |\n| two | the second meaning |\n\nA closing line.\n"
V2 = "# A plan\n\n| Term | Meaning |\n|---|---|\n| one | the first meaning, reworded |\n| two | the second meaning |\n\nA closing line, reworded.\n"


@pytest.fixture
def world(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plan.md").write_text(V1)
    store = Store(tmp_path / "state.db")
    store.put("collection:t", "col")
    o = FakeOutline()
    s = Syncer(o, store, Repo(name="t", path=repo_dir))
    assert s.one("plan.md", "plan.md").action == "created"
    (repo_dir / "plan.md").write_text(V2)
    return o, store, s


def test_unedited_uncommented_document_is_replaced_when_it_cannot_be_patched(world):
    o, store, s = world
    r = s.one("plan.md", "plan.md")
    assert r.action == "patched" and r.detail.startswith("replaced:")
    assert o.docs["d1"]["text"] == V2.split("\n\n", 1)[1].rstrip("\n")   # the body, as split_title gives it
    row = store.doc("t", "plan.md")
    assert row["status"] == "ok" and row["base_repo"] == V2
    assert s.one("plan.md", "plan.md").action == "unchanged"


def test_a_comment_keeps_it_patch_only(world):
    o, store, s = world
    o.comments_on["d1"] = [{"id": "c1", "anchorText": "the first meaning"}]
    r = s.one("plan.md", "plan.md")
    assert r.action == "cannot-patch"
    assert o.replaced == [] and store.doc("t", "plan.md")["status"] == "cannot-patch"


def test_a_persons_edit_keeps_it_patch_only(world):
    o, store, s = world
    o.editors["d1"].append("someone")          # a person saved a revision
    o.docs["d1"]["text"] = V1.split("\n\n", 1)[1]   # so the baseline still reads as unchanged
    r = s.one("plan.md", "plan.md")
    assert r.action in ("cannot-patch", "conflict") and o.replaced == []


def test_a_version_that_failed_before_is_replaced_once_when_nothing_is_lost(world):
    o, store, s = world
    o.comments_on["d1"] = [{"id": "c1"}]
    assert s.one("plan.md", "plan.md").action == "cannot-patch"     # stuck, as before the change
    assert s.one("plan.md", "plan.md").action == "cannot-patch"     # and not sent again
    o.comments_on["d1"] = []                                         # the comment was resolved
    r = s.one("plan.md", "plan.md")
    assert r.action == "cannot-patch" and o.replaced == []           # this version was tried once already
    (s.repo.path / "plan.md").write_text(V2 + "\nOne more line.\n")
    r = s.one("plan.md", "plan.md")
    assert r.action == "patched" and r.detail.startswith("replaced:")
    assert o.docs["d1"]["text"].endswith("One more line.")
    assert store.doc("t", "plan.md")["status"] == "ok"
