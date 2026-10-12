"""Files linked from a git worktree of a configured repo: found, published, and not undone by the
sync pass until the main folder has the same text."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

from margin.config import Repo, locate
from margin.store import Store
from margin.sync import Syncer
from test_sync import FakeOutline


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
                        "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


def _world(tmp_path: Path):
    main, wt = tmp_path / "main", tmp_path / "wt"
    (main / "docs").mkdir(parents=True)
    (main / "docs" / "plan.md").write_text("# Plan\n\nThe main folder's text.\n")
    git(main, "init", "-q")
    git(main, "add", "docs/plan.md")
    git(main, "commit", "-qm", "plan")
    git(main, "worktree", "add", "-q", "-b", "topic", str(wt))
    (wt / "docs" / "plan.md").write_text("# Plan\n\nThe worktree's newer text.\n")
    (wt / "docs" / "new.md").write_text("# New\n\nOnly in the worktree so far.\n")
    repo = Repo(name="repo", path=main)
    o = FakeOutline()
    return main, wt, repo, o, Syncer(o, Store(tmp_path / "margin.db"), repo)


def test_a_worktree_file_is_found_through_git(tmp_path):
    main, wt, repo, _, _ = _world(tmp_path)
    cfg = SimpleNamespace(repos=[repo])
    assert locate(cfg, wt / "docs" / "new.md") == (repo, "docs/new.md", wt.resolve())
    assert locate(cfg, main / "docs" / "plan.md") == (repo, "docs/plan.md", main.resolve())
    assert locate(cfg, tmp_path / "elsewhere.md") is None


def test_a_linked_worktree_file_is_published_and_the_sync_pass_keeps_it(tmp_path):
    main, wt, repo, o, s = _world(tmp_path)
    got = {rel: (d, st) for rel, d, st, _ in s.publish_status(["docs/plan.md", "docs/new.md"], wt)}
    assert got["docs/plan.md"][1] == "ok" and got["docs/new.md"][1] == "ok"
    plan_id, new_id = got["docs/plan.md"][0]["id"], got["docs/new.md"][0]["id"]
    assert "newer text" in o.docs[plan_id]["text"] and "Only in the worktree" in o.docs[new_id]["text"]
    s.run()                                              # the pass that reads the main folder
    assert "newer text" in o.docs[plan_id]["text"], "the main folder's older text came back"
    assert s.s.doc("repo", "docs/new.md")["status"] == "ok", "a worktree-only file was marked missing"


def test_the_pin_lifts_once_the_main_folder_has_the_text_or_the_file_is_gone(tmp_path):
    main, wt, repo, o, s = _world(tmp_path)
    s.publish_status(["docs/plan.md", "docs/new.md"], wt)
    (main / "docs" / "plan.md").write_text((wt / "docs" / "plan.md").read_text())   # merged
    s.run()
    assert "docs/plan.md" not in s.pins()
    (wt / "docs" / "new.md").unlink()                    # the branch was dropped
    s.run()
    assert "docs/new.md" not in s.pins()
    assert s.s.doc("repo", "docs/new.md")["status"] == "missing-file"
