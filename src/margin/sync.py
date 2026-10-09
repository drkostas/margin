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
        out = []
        with repo_lock(self.s, self.repo.name):
            wanted = set()
            for rel in rels:
                parts = rel.split("/")
                wanted.update("/".join(parts[:d]) for d in range(1, len(parts)))
                parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
                wanted.add(parent if parent and name.lower() == "readme.md" else rel)
            for key, src in tree(files(self.repo)):
                if key in wanted:
                    self.one(key, src)
            for rel in rels:
                parent, name = rel.rpartition("/")[0], rel.rpartition("/")[2]
                key = parent if parent and name.lower() == "readme.md" else rel
                row = self.s.doc(self.repo.name, key)
                if row:
                    out.append((rel, self.o.document(row["doc_id"])))
        return out

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
        for widen in range(tries):
            if current == target:
                return
            ok = True
            for find, repl in plan(current, target, widen):
                expected = apply(current, [(find, repl)])
                current = self.o.patch(doc_id, find, repl)["text"]
                if current != expected:
                    ok = False
                    break
            if ok and current == target:
                return
        if current != target:
            raise CannotPatch("Outline's text still differs from the file after widening")

    def _parent_id(self, key: str) -> str | None:
        parent = key.rpartition("/")[0]
        if not parent:
            return None
        row = self.s.doc(self.repo.name, parent)
        return row["doc_id"] if row else None

    def one(self, key: str, src: str | None) -> Result:
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
        status, detail = ("ok", None) if after == target else (
            "drift", "after patching, Outline's text differs from the file as Outline renders it")
        # The file's baseline only moves when Outline really matches it, so a failed pass is
        # retried next time instead of being remembered as done.
        self.s.save_doc(self.repo.name, key, row["doc_id"], row["collection_id"],
                        text if status == "ok" else row["base_repo"], after, status, detail)
        return Result(key, "patched" if status == "ok" else "drift", detail or "")
