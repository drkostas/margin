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
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "README.md").write_text("x")
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


def test_excluded_folders_are_not_walked(tmp_path, monkeypatch):
    import os
    (tmp_path / "skills" / "a").mkdir(parents=True)
    (tmp_path / "skills" / "a" / "SKILL.md").write_text("x")
    (tmp_path / "projects" / "p" / "memory").mkdir(parents=True)
    (tmp_path / "projects" / "p" / "memory" / "m.md").write_text("x")
    (tmp_path / "projects" / "p" / "1234abcd-session").mkdir()
    (tmp_path / "projects" / "p" / "1234abcd-session" / "big.md").write_text("x")
    (tmp_path / "plugins" / "cache").mkdir(parents=True)
    (tmp_path / "plugins" / "cache" / "r.md").write_text("x")
    seen = []
    real = os.walk

    def spy(top, *a, **k):
        for d, dn, fn in real(top, *a, **k):
            seen.append(d)
            yield d, dn, fn

    monkeypatch.setattr(os, "walk", spy)
    r = Repo("c", tmp_path, include=["skills/**/*.md", "projects/*/memory/*.md"],
             exclude=["plugins/**", "projects/*/*-session/**"])
    assert files(r) == ["projects/p/memory/m.md", "skills/a/SKILL.md"]
    assert not any("plugins" in d or "session" in d for d in seen)


def test_long_titles_are_cut_to_what_outline_accepts():
    from margin.sync import TITLE_MAX, clamp_title
    long = "Acute to chronic workload ratio " * 6
    t = clamp_title(long)
    assert len(t) <= TITLE_MAX and t.endswith("…") and not t[:-1].endswith(" ")
    assert clamp_title("Short title") == "Short title"
    assert len(clamp_title("x" * 300)) == TITLE_MAX
