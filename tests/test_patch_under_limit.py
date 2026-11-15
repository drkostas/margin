"""A document near Outline's length limit can still be patched down, and one refused document does not stop the rest.

Outline refuses a patch whose result is over its limit. A document close to the limit that must lose a large part
and gain a smaller one failed when the gain was patched in first, though the end result fit. And when Outline refused
one document of a `margin link`, the error ended the command and the other files got no link. The limit is made small
here so the texts stay short.
"""
from pathlib import Path

import pytest

from margin import sync
from margin.config import Repo
from margin.outline import OutlineError
from margin.store import Store
from margin.sync import Syncer, under_limit
from test_sync import FakeOutline


class LimitedOutline(FakeOutline):
    """Refuses a patch whose result is over the limit, as Outline does, and can refuse every write to one document."""

    def __init__(self, refuse_doc=None):
        super().__init__()
        self.refuse_doc = refuse_doc
        self.refused = 0

    def patch(self, doc_id, find, text):
        after = self.docs[doc_id]["text"].replace(find, text, 1)
        if doc_id == self.refuse_doc or len(after) > sync.MAX_TEXT:
            self.refused += 1
            raise OutlineError("documents.update", 400, "validation_error",
                               f"Document text content must be {sync.MAX_TEXT} characters or less (text)")
        return super().patch(doc_id, find, text)


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr(sync, "MAX_TEXT", 300)


def world(tmp_path: Path, files: dict, outline=None):
    root = tmp_path / "repo"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    o = outline or LimitedOutline()
    return root, o, Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))


HEAD = "# Notes\n\nThe first paragraph stays.\n"
MIDDLE = "\n" + "Old words that move to another file. " * 6 + "\n"     # one large block, as a moved section is
TAIL = "\nThe last paragraph stays too.\n"
NEW = "".join(f"\nZz fresh entry {n}.\n" for n in range(3))               # new entries at the end, unlike the old


def test_a_document_near_the_limit_loses_its_middle_before_it_gains_its_tail(tmp_path):
    before, after = HEAD + MIDDLE + TAIL, HEAD + TAIL + NEW
    assert len(before) <= 300 and len(after) <= 300 and len(before) + len(NEW) > 300
    root, o, s = world(tmp_path, {"notes.md": before})
    [(_, doc, status, _)] = s.publish_status(["notes.md"])
    assert status == "ok"
    (root / "notes.md").write_text(after)
    [(_, doc, status, detail)] = s.publish_status(["notes.md"])
    assert (status, detail) == ("ok", "") and o.refused == 0
    text = o.docs[doc["id"]]["text"]
    assert "Old words" not in text and all(f"Zz fresh entry {n}." in text for n in range(3))


def test_under_limit_keeps_the_planned_order_when_it_fits():
    hunks = [("tail", "tail and more"), ("a long middle part", "")]
    assert under_limit(hunks, 100, limit=200) == hunks
    assert under_limit(hunks, 195, limit=200) == [("a long middle part", ""), ("tail", "tail and more")]


def test_one_refused_document_is_its_own_result_and_the_others_get_their_links(tmp_path):
    root, o, s = world(tmp_path, {"a.md": "# A\n\nFirst.\n", "b.md": "# B\n\nFirst.\n"})
    got = {rel: (doc, status) for rel, doc, status, _ in s.publish_status(["a.md", "b.md"])}
    assert got["a.md"][1] == got["b.md"][1] == "ok"
    o.refuse_doc = got["a.md"][0]["id"]
    (root / "a.md").write_text("# A\n\nFirst, changed.\n")
    (root / "b.md").write_text("# B\n\nFirst, changed.\n")
    res = {rel: (status, detail) for rel, _doc, status, detail in s.publish_status(["a.md", "b.md"])}
    assert res["a.md"][0] == "error" and "400 validation_error" in res["a.md"][1]
    assert res["b.md"] == ("ok", "") and "First, changed." in o.docs[got["b.md"][0]["id"]]["text"]
