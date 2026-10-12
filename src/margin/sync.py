"""Publish a repo's markdown into an Outline collection, keeping comments alive.

Each repo is one collection. Folders become parent documents, and a folder's README.md is that
folder document's text. A file's first `# Heading` becomes the document title.

The rule this module exists to keep: a document that may carry comments is only ever PATCHED,
never replaced, because a replace removes every comment anchor. To patch, margin needs the new
file in Outline's own markdown style (Outline rewrites `-` lists as `*`, escapes, and so on), so it
writes the file into a private draft first and reads back what Outline made of it.

Direction (phase 1): repo to Outline. Both sides' text at the last sync is kept, so:
  only the repo changed     -> patch Outline
  only Outline changed      -> leave both alone and report it   (phase 2 brings it back to the repo)
  both changed              -> conflict: leave both alone and report it
Nothing is ever settled by "the newest wins".
"""
from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import ALWAYS_EXCLUDE, Repo
from .outline import Outline, OutlineError
from .patches import CannotPatch, apply, plan
from .store import Store, repo_lock


@dataclass
class Result:
    path: str
    action: str          # created | patched | unchanged | outline-edited | conflict | ...
    detail: str = ""


TITLE_MAX = 100     # Outline refuses a longer title with a 400


def loose(text: str) -> str:
    """Text with only its spacing removed: blank lines between blocks and spaces at the end of a
    line. Outline keeps an empty paragraph a patch tries to remove (a third newline), and one
    between two list items splits the list in two, so the words match while the newlines do not."""
    import re
    return re.sub(r"\n+", "\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def _epoch(stamp: str | None) -> float:
    """Seconds since 1970 for an Outline timestamp ("2026-10-09T12:12:30.547Z"); 0 when absent."""
    import datetime as dt
    if not stamp:
        return 0.0
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def clamp_title(title: str, limit: int = TITLE_MAX) -> str:
    """A title Outline accepts: cut at a word boundary, with an ellipsis when it was cut."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[: limit - 1].rsplit(" ", 1)[0] or title[: limit - 1]
    return cut.rstrip(" ,.;:-") + "…"


def split_title(text: str, fallback: str) -> tuple[str, str]:
    lines = text.lstrip("﻿").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        body = "\n".join(lines[i + 1:]).strip("\n")
        return lines[i][2:].strip() or fallback, body
    return fallback, text.strip("\n")


def _match(rel: str, patterns: list[str]) -> bool:
    p = PurePosixPath(rel)
    return any(p.match(pat) or fnmatch.fnmatch(rel, pat) or
               (pat.startswith("**/") and fnmatch.fnmatch(rel, pat[3:])) for pat in patterns)


def files(repo: Repo) -> list[str]:
    """Repo-relative paths of every markdown file margin publishes. Working tree, not git, so
    untracked and git-ignored docs are included on purpose.

    Excluded folders are skipped during the walk, not filtered afterwards, so a root that holds
    tens of thousands of other files (a home config folder full of transcripts) costs nothing."""
    import os
    excludes = ALWAYS_EXCLUDE + repo.exclude
    md_only = all(p.lower().endswith(".md") for p in repo.include)
    out = []
    for dirpath, dirnames, filenames in os.walk(repo.path):
        base = Path(dirpath).relative_to(repo.path).as_posix()
        base = "" if base == "." else base + "/"
        # a folder is skipped when anything inside it would be excluded
        dirnames[:] = sorted(d for d in dirnames if not _match(f"{base}{d}/x", excludes))
        for name in sorted(filenames):
            if md_only and not name.lower().endswith(".md"):
                continue          # cheap: most of a big folder is not markdown at all
            rel = base + name
            if _match(rel, repo.include) and not _match(rel, excludes):
                out.append(rel)
    return sorted(out)


def tree(paths: list[str]) -> list[tuple[str, str | None]]:
    """(key, source file) for every document, parents first. A folder's key is its path and its
    source is its README.md, when it has one. A README at the repo root is an ordinary page."""
    keys: dict[str, str | None] = {}
    for rel in paths:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            keys.setdefault("/".join(parts[:depth]), None)
    for rel in paths:
        parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
        if parent and name.lower() == "readme.md":
            keys[parent] = rel
        else:
            keys[rel] = rel
    return sorted(keys.items(), key=lambda kv: (kv[0].count("/"), kv[0]))


class Syncer:
    def __init__(self, outline: Outline, store: Store, repo: Repo, full: bool = False):
        self.o, self.s, self.repo = outline, store, repo
        self.full = full        # also fetch documents whose file did not change
        self._collection: str | None = None

    # ---- the collection and the scratch draft
    @property
    def collection(self) -> str:
        if self._collection is None:
            key = f"collection:{self.repo.name}"
            cid = self.s.get(key)
            if cid:
                try:
                    self.o.data("collections.info", {"id": cid})
                except OutlineError as e:
                    if e.status != 404:
                        raise
                    cid = None
            if not cid:
                cid = self.o.ensure_collection(
                    self.repo.name, f"Markdown from {self.repo.name}, published by margin.")["id"]
                self.s.put(key, cid)
            self._collection = cid
        return self._collection

    def canonical(self, body: str) -> str:
        """`body` as Outline itself writes it back, via a private draft only margin uses."""
        if not body.strip():
            return ""
        h = hashlib.sha256(body.encode()).hexdigest()
        cached = self.s.get(f"canon:{h}")
        if cached is not None:
            return cached
        key = "scratch:" + self.repo.name
        sid = self.s.get(key)
        doc = None
        if sid:
            try:
                doc = self.o.replace(sid, body)
            except OutlineError as e:
                if e.status != 404:
                    raise
        if doc is None:
            doc = self.o.data("documents.create", {"collectionId": self.collection,
                                                   "title": "margin scratch (private draft)",
                                                   "text": body, "publish": False})
            self.s.put(key, doc["id"])
        text = doc["text"]
        self.s.put(f"canon:{h}", text)
        return text

    # ---- one pass
    def run(self) -> list[Result]:
        with repo_lock(self.s, self.repo.name):
            return self._run()

    def publish(self, rels: list[str]) -> list[tuple[str, dict]]:
        """Publish just these repo-relative files (and the folders above them) now, and return
        (path, document) for each, so a caller can hand out links without waiting for a pass."""
        return [(rel, doc) for rel, doc, _status, _detail in self.publish_status(rels)]

    def publish_status(self, rels: list[str], root: Path | None = None
                       ) -> list[tuple[str, dict, str, str]]:
        """`publish`, with each file's sync status after it ("ok", "conflict", "cannot-patch",
        "drift", "outline-edited", ...) and its detail. Only "ok" means the document now says
        what the file says; anything else means the link points at an older or different text."""
        out = []
        with repo_lock(self.s, self.repo.name):
            if root is not None and Path(root).resolve() != self.repo.path.resolve():
                for rel in rels:                          # linked from a worktree: pin them
                    parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
                    self.pin(parent if parent and name.lower() == "readme.md" else rel,
                             Path(root) / rel)
            wanted = set()
            for rel in rels:
                parts = rel.split("/")
                wanted.update("/".join(parts[:d]) for d in range(1, len(parts)))
                parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
                wanted.add(parent if parent and name.lower() == "readme.md" else rel)
            done = set()
            for key, src in tree(files(self.repo)):
                if key in wanted:
                    self.one(key, src)
                    done.add(key)
            for key in sorted((wanted - done) & set(self.pins())):   # only in a worktree so far
                if self.source(key, None) is not None:
                    self.one(key, None)
            for rel in rels:
                parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
                key = parent if parent and name.lower() == "readme.md" else rel
                row = self.s.doc(self.repo.name, key)
                if row:
                    out.append((rel, self.o.document(row["doc_id"]), row["status"] or "ok",
                                row["detail"] or ""))
        return out

    def accept_file(self, rel: str, root: Path | None = None) -> Result:
        """Let the file's side win a conflict that no person made: the document's text since the
        last sync was written only by margin's own user, and nobody has commented on it. Outline's
        current text becomes the base, so the next pass sees only the file's change and patches it
        in. Anything a person did refuses it, because their edit or their comment's anchor would be
        lost; then ask them."""
        parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
        key = parent if parent and name.lower() == "readme.md" else rel
        with repo_lock(self.s, self.repo.name):
            row = self.s.doc(self.repo.name, key)
            if row is None:
                return Result(key, "refused", "it was never published; margin link publishes it")
            if root is not None and Path(root).resolve() != self.repo.path.resolve():
                self.pin(key, Path(root) / rel)          # the worktree's version is the file
            if row["status"] in ("ok", None):
                return Result(key, "refused", "there is no conflict: the document matches the file")
            me = self.o.me()["id"]
            notes = list(self.o.comments(document_id=row["doc_id"]))
            if notes:
                return Result(key, "refused", f"{len(notes)} comment(s) on the document; answer them "
                                              "first, because the file's text would move their anchors")
            since = (row["synced_at"] or 0) - 60
            people = set()
            for r in self.o.paged("revisions.list", {"documentId": row["doc_id"]}):
                who = r.get("createdBy") or {}
                if who.get("id") != me and _epoch(r.get("createdAt")) > since:
                    people.add(who.get("name") or who.get("id") or "someone")
            doc = self.o.document(row["doc_id"])
            last = doc.get("updatedBy") or {}
            if last.get("id") not in (None, me):
                people.add(last.get("name") or "someone")
            if people:
                return Result(key, "refused", f"edited in Outline by {', '.join(sorted(people))} "
                                              "since the last sync; ask them which text is right")
            # Outline's text becomes the base, and the file's baseline is cleared so the next step
            # patches the file in even when only Outline had moved (outline-edited)
            self.s.save_doc(self.repo.name, key, row["doc_id"], row["collection_id"],
                            "", doc["text"], "ok", None)
            src = dict(tree(files(self.repo))).get(key)
            r = self.one(key, src)
            if r.action not in ("cannot-patch", "drift"):
                return r
            # Nobody but margin wrote it and nothing is anchored in it, so the whole text may be
            # replaced, the same reason an empty document is replaced (a replace drops comment
            # anchors, and here there are none).
            text = (self.repo.path / self.source(key, src)).read_text(errors="replace")
            title, body = split_title(text, PurePosixPath(key).name)
            after = self.o.replace(row["doc_id"], body)["text"]
            self.s.save_doc(self.repo.name, key, row["doc_id"], row["collection_id"],
                            text, after, "ok",
                            "the file replaced the document (no person had edited it, no comments)")
            return Result(key, "patched", "replaced: no person had edited it and it had no comments")

    # ---- files linked from a worktree
    # ⚠️ EVERY CHAT WORKS IN ITS OWN GIT WORKTREE (2026-10-12), and margin read only the main
    # folder, so a new document was published by copying it there by hand. A file linked from a
    # worktree is PINNED: until the main folder has the same text (or the worktree file is gone),
    # the sync pass publishes the worktree's version instead of the main folder's older one, and
    # does not mark a file that exists only in the worktree as missing.
    def _pin_key(self, key: str) -> str:
        return f"pin:{self.repo.name}:{key}"

    def pin(self, key: str, file: Path) -> None:
        self.s.put(self._pin_key(key), str(Path(file).resolve()))

    def pins(self) -> dict[str, Path]:
        prefix = self._pin_key("")
        rows = self.s.db.execute("SELECT k, v FROM kv WHERE k LIKE ?", (prefix + "%",)).fetchall()
        return {r["k"][len(prefix):]: Path(r["v"]) for r in rows if r["v"]}

    def unpin(self, key: str) -> None:
        self.s.db.execute("DELETE FROM kv WHERE k=?", (self._pin_key(key),))

    def source(self, key: str, src: str | None) -> str | None:
        """Where `key`'s text comes from: its pinned worktree file while that differs from the main
        folder's, else the main folder's file. A pin lifts itself once the main folder caught up or
        the worktree file is gone."""
        pinned = self.pins().get(key)
        if pinned is None:
            return src
        if not pinned.exists():
            self.unpin(key)
            return src
        main = self.repo.path / src if src else None
        if main is not None and main.exists() and main.read_bytes() == pinned.read_bytes():
            self.unpin(key)
            return src
        return str(pinned)

    def _run(self) -> list[Result]:
        paths = files(self.repo)
        results = []
        seen = set()
        for key, src in tree(paths):
            seen.add(key)
            try:
                results.append(self.one(key, src))
            except OutlineError as e:
                results.append(Result(key, "error", str(e)))
        for key in sorted(set(self.pins()) - seen):        # files so far only in a worktree
            if self.source(key, None) is None:
                continue          # the pin lifted and the main folder has no file: missing, below
            seen.add(key)
            try:
                results.append(self.one(key, None))
            except OutlineError as e:
                results.append(Result(key, "error", str(e)))
        for row in self.s.docs(self.repo.name):
            if row["path"] not in seen and row["status"] != "missing-file":
                self.s.mark(self.repo.name, row["path"], "missing-file",
                            "the file is gone from the repo; the document is kept")
                results.append(Result(row["path"], "missing-file"))
        return results

    def converge(self, doc_id: str, current: str, target: str, tries: int = 4) -> None:
        """Patch until Outline's text is the target. Every patch's result is checked against what
        it should have produced; on a difference, re-plan from what Outline really has, with
        patches that cross one more block boundary."""
        if target.startswith(current) and target[len(current):].startswith("\n\n"):
            # only new blocks at the end: append them, which leaves every existing block alone
            current = self.o.append(doc_id, target[len(current):])["text"]
            if current == target:
                return
        # ⚠️ ONE HUNK OUTLINE WILL NOT TAKE MUST NOT STOP THE OTHERS (2026-10-12). Outline keeps an
        # empty paragraph a patch tries to remove (it reads as a third newline between blocks), and
        # the hunks go bottom-up, so that one hunk used to end the pass and every change above it
        # (a report's intro, its title) never landed. A hunk whose result differs from what it
        # should have produced is set aside, and the rest are re-planned from what Outline has;
        # only when nothing else is left do the hunks widen across more block boundaries.
        failed: set = set()
        widen = 0
        budget = 2 * len(plan(current, target, 0)) + 4 * tries
        while budget > 0 and current != target:
            hunks = [h for h in plan(current, target, widen) if h not in failed]
            if not hunks:
                if widen + 1 >= tries:
                    break
                widen += 1
                continue
            for find, repl in hunks:
                budget -= 1
                expected = apply(current, [(find, repl)])
                current = self.o.patch(doc_id, find, repl)["text"]
                if current != expected:
                    failed.add((find, repl))
                    break
        if current != target and loose(current) != loose(target):
            raise CannotPatch("Outline's text still differs from the file after widening")

    def _parent_id(self, key: str) -> str | None:
        parent = key.rpartition("/")[0]
        if not parent:
            return None
        row = self.s.doc(self.repo.name, parent)
        return row["doc_id"] if row else None

    def one(self, key: str, src: str | None) -> Result:
        src = self.source(key, src)
        name = PurePosixPath(key).name
        fallback = name[:-3] if name.lower().endswith(".md") else name
        text = (self.repo.path / src).read_text(errors="replace") if src else ""
        title, body = split_title(text, fallback)
        title = clamp_title(title)
        row = self.s.doc(self.repo.name, key)

        if row is None:
            doc = self.o.create_document(self.collection, title, body, self._parent_id(key))
            self.s.save_doc(self.repo.name, key, doc["id"], self.collection, text, doc["text"])
            return Result(key, "created")

        repo_changed = text != row["base_repo"]
        dirty_key = f"dirty:{row['doc_id']}"
        if not (repo_changed or self.full or self.s.get(dirty_key) or row["status"] != "ok"):
            return Result(key, "unchanged")       # nothing says either side moved: no API call
        self.s.db.execute("DELETE FROM kv WHERE k=?", (dirty_key,))
        try:
            doc = self.o.document(row["doc_id"])
        except OutlineError as e:
            if e.status == 404:
                self.s.mark(self.repo.name, key, "deleted-in-outline",
                            "the document was deleted in Outline; margin will not recreate it")
                return Result(key, "deleted-in-outline")
            raise
        current = doc["text"]
        outline_changed = current != row["base_outline"]

        if outline_changed and repo_changed:
            self.s.mark(self.repo.name, key, "conflict",
                        "both the file and the Outline document changed since the last sync")
            return Result(key, "conflict")
        if outline_changed:
            self.s.mark(self.repo.name, key, "outline-edited",
                        "the Outline document was edited; the repo file is unchanged")
            return Result(key, "outline-edited")
        if not repo_changed:
            if row["status"] != "ok":
                self.s.mark(self.repo.name, key, "ok")
            return Result(key, "unchanged")

        target = self.canonical(body)
        try:
            if not current.strip():
                self.o.replace(row["doc_id"], body)      # an empty document cannot hold a comment
            else:
                self.converge(row["doc_id"], current, target)
        except CannotPatch as e:
            self.s.mark(self.repo.name, key, "cannot-patch", str(e))
            return Result(key, "cannot-patch", str(e))
        if title != doc["title"]:
            self.o.set_title(row["doc_id"], title)
        after = self.o.document(row["doc_id"])["text"]
        if after == target:
            status, detail = "ok", None
        elif loose(after) == loose(target):
            status, detail = "ok", "only blank lines differ from the file"
        else:
            status, detail = ("drift", "after patching, Outline's text differs from the file as "
                                       "Outline renders it")
        # The file's baseline only moves when Outline really matches it, so a failed pass is
        # retried next time instead of being remembered as done.
        self.s.save_doc(self.repo.name, key, row["doc_id"], row["collection_id"],
                        text if status == "ok" else row["base_repo"], after, status, detail)
        return Result(key, "patched" if status == "ok" else "drift", detail or "")
