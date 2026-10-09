from pathlib import Path

from margin.config import Repo
from margin.sync import files, split_title, tree


def test_title_comes_from_the_first_heading():
    assert split_title("\n# Hello\n\nbody", "f") == ("Hello", "body")
    assert split_title("no heading", "f") == ("f", "no heading")


def test_folders_become_parents_and_readme_is_the_folder_text():
    t = tree(["README.md", "docs/README.md", "docs/a.md", "docs/sub/b.md"])
    assert t == [("README.md", "README.md"), ("docs", "docs/README.md"),
                 ("docs/a.md", "docs/a.md"), ("docs/sub", None), ("docs/sub/b.md", "docs/sub/b.md")]


def test_files_reads_the_working_tree_and_skips_junk(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x")
    (tmp_path / "node_modules" / "p").mkdir(parents=True)
    (tmp_path / "node_modules" / "p" / "r.md").write_text("x")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "s.md").write_text("x")
    (tmp_path / "top.md").write_text("x")
    r = Repo("t", tmp_path, exclude=["skip/**"])
    assert files(r) == ["docs/a.md", "top.md"]


def test_store_is_usable_from_another_thread(tmp_path):
    import threading
    from margin.store import Store
    s = Store(tmp_path / "s.db")
    errors = []

    def worker():
        try:
            s.put("k", "v")
            s.add_comment({"id": "c", "document_id": "d", "created_at": "t", "via": "webhook"})
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not errors and s.get("k") == "v" and s.add_comment(
        {"id": "c", "document_id": "d", "created_at": "t", "via": "poll"}) is False
