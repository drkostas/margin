"""Pictures: a local image a file shows is uploaded once, attached to its document, and counted as
part of the file."""
import io
import urllib.request
from pathlib import Path

from margin import images
from margin.config import Repo
from margin.outline import Outline
from margin.store import Store
from margin.sync import Syncer

from test_sync import FakeOutline

PNG = b"\x89PNG\r\n\x1a\n" + b"first picture"


class FakeOutlineWithFiles(FakeOutline):
    def __init__(self):
        super().__init__()
        self.uploads: list[tuple[str, str, bytes, str]] = []

    def upload(self, name, content_type, data, document_id):
        self.uploads.append((name, content_type, data, document_id))
        return f"/api/attachments.redirect?id=a{len(self.uploads)}"


def _world(tmp_path: Path, text: str):
    root = tmp_path / "repo"
    (root / "shots").mkdir(parents=True)
    (root / "shots" / "time.png").write_bytes(PNG)
    (root / "page.md").write_text(text)
    o = FakeOutlineWithFiles()
    return root, o, Syncer(o, Store(tmp_path / "margin.db"), Repo(name="repo", path=root))


PAGE = "# Page\n\nThe Time tab.\n\n![The Time tab](shots/time.png)\n\nAfter it.\n"


def test_a_new_page_shows_its_picture_attached_to_that_page(tmp_path):
    root, o, s = _world(tmp_path, PAGE)
    [(_, doc, status, _)] = s.publish_status(["page.md"])
    assert status == "ok"
    [(name, ctype, data, doc_id)] = o.uploads
    assert (name, ctype, data, doc_id) == ("time.png", "image/png", PNG, doc["id"])
    assert "![The Time tab](/api/attachments.redirect?id=a1)" in o.docs[doc["id"]]["text"]
    assert (root / "page.md").read_text() == PAGE           # the file keeps its relative path


def test_an_unchanged_picture_is_not_uploaded_again(tmp_path):
    root, o, s = _world(tmp_path, PAGE)
    s.publish_status(["page.md"])
    (root / "page.md").write_text(PAGE.replace("After it.", "After it, changed."))
    [(_, doc, status, _)] = s.publish_status(["page.md"])
    assert status == "ok" and len(o.uploads) == 1
    assert "After it, changed." in o.docs[doc["id"]]["text"]


def test_a_changed_picture_is_a_changed_file(tmp_path):
    root, o, s = _world(tmp_path, PAGE)
    [(_, doc, _, _)] = s.publish_status(["page.md"])
    (root / "shots" / "time.png").write_bytes(PNG + b" retaken")
    [(_, doc, status, _)] = s.publish_status(["page.md"])
    assert status == "ok" and len(o.uploads) == 2 and o.uploads[1][2] == PNG + b" retaken"
    assert "redirect?id=a2" in o.docs[doc["id"]]["text"]
    assert "redirect?id=a1" not in o.docs[doc["id"]]["text"]


def test_a_picture_shown_twice_is_uploaded_once(tmp_path):
    root, o, s = _world(tmp_path, PAGE + "\nAgain.\n\n![Again](shots/time.png)\n")
    [(_, doc, _, _)] = s.publish_status(["page.md"])
    assert len(o.uploads) == 1
    assert o.docs[doc["id"]]["text"].count("redirect?id=a1") == 2


def test_a_page_without_pictures_publishes_as_before(tmp_path):
    """The compared text of a page with no local picture is the file itself, so documents
    published before pictures existed are not read as changed."""
    root, o, s = _world(tmp_path, "# Plain\n\nNo pictures.\n")
    s.publish_status(["page.md"])
    assert o.uploads == []
    assert s.s.doc("repo", "page.md")["base_repo"] == "# Plain\n\nNo pictures.\n"


def test_only_local_image_files_are_touched(tmp_path):
    base = tmp_path
    (base / "a.png").write_bytes(PNG)
    (base / "notes.txt").write_text("x")
    body = ("![web](https://example.org/a.png) ![served](/api/attachments.redirect?id=x) "
            "![data](data:image/png;base64,AAAA) ![missing](gone.png) ![text](notes.txt) "
            '![local](a.png "A title") ![angle](<a.png>)')
    found = images.local(body, base)
    assert [ref for ref, _ in found] == ["a.png", "<a.png>"]
    out = images.point(body, {"a.png": "/u1", "<a.png>": "/u1"})
    assert '![local](/u1 "A title")' in out and "![angle](/u1)" in out
    assert "![web](https://example.org/a.png)" in out and "![missing](gone.png)" in out


def test_the_key_goes_only_to_outlines_own_upload_address(monkeypatch):
    sent = []

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        sent.append((req.full_url, req.get_header("Authorization")))
        return Answer(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    o = Outline("https://docs.example.org", "k-123")
    for up in ("/api/files.create", "https://bucket.storage.example.com/upload?sig=1"):
        monkeypatch.setattr(o, "data", lambda m, b=None, up=up: {
            "uploadUrl": up, "form": {"key": "uploads/x"},
            "attachment": {"url": "/api/attachments.redirect?id=z"}})
        assert o.upload("a.png", "image/png", PNG, "d1") == "/api/attachments.redirect?id=z"
    assert sent == [("https://docs.example.org/api/files.create", "Bearer k-123"),
                    ("https://bucket.storage.example.com/upload?sig=1", None)]
