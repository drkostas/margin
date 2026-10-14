"""margin's state: one SQLite file. What was synced, which comments arrived, which were delivered.

Nothing here is a second copy of the documents. `doc` keeps, per file, the text both sides had at the
last sync, which is exactly what is needed to tell "only the repo changed" from "both changed".
"""
from __future__ import annotations

import contextlib
import fcntl
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc (
  repo TEXT NOT NULL, path TEXT NOT NULL,          -- path relative to the repo, '' for the repo root
  doc_id TEXT NOT NULL, collection_id TEXT NOT NULL,
  base_repo TEXT,                                  -- the repo file's text at the last sync
  base_outline TEXT,                               -- Outline's text right after the last sync
  status TEXT NOT NULL DEFAULT 'ok',               -- ok | conflict | cannot-patch | missing-file
  detail TEXT, synced_at REAL,
  PRIMARY KEY (repo, path)
);
CREATE UNIQUE INDEX IF NOT EXISTS doc_by_id ON doc(doc_id);
CREATE TABLE IF NOT EXISTS comment (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, parent_id TEXT,
  author_id TEXT, author TEXT, text TEXT, anchor TEXT,
  created_at TEXT NOT NULL, received_at REAL NOT NULL,
  mentions_agent INTEGER NOT NULL DEFAULT 0,
  route TEXT,                                      -- NULL when the document is not one margin syncs
  via TEXT NOT NULL,                               -- webhook | poll
  delivered_at REAL, batch_id TEXT,
  resolved_at TEXT,                                -- when the thread was resolved, if it was
  anchor_before TEXT,                              -- the words before the passage in its paragraph
  anchor_lost INTEGER NOT NULL DEFAULT 0           -- 1 when its passage has no mark left in the text
);
CREATE INDEX IF NOT EXISTS comment_pending ON comment(route) WHERE delivered_at IS NULL;
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class _Locked:
    """The connection's execute family, each call under the store's lock."""

    def __init__(self, db, lock):
        self._d, self._l = db, lock

    def execute(self, *a):
        with self._l:
            return _Rows(self._d.execute(*a))

    def executemany(self, *a):
        with self._l:
            return self._d.executemany(*a)

    def executescript(self, *a):
        with self._l:
            return self._d.executescript(*a)


class _Rows:
    """A finished cursor: rows fetched while the lock was held."""

    def __init__(self, cur):
        self.rowcount = cur.rowcount
        self._rows = cur.fetchall() if cur.description else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # One connection shared by the webhook thread and the main loop, so every statement
        # goes through one lock.
        self._db = sqlite3.connect(str(path), isolation_level=None, timeout=30,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db = _Locked(self._db, self.lock)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        # a store made before a column existed gains it
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(comment)").fetchall()}
        for col, kind in (("resolved_at", "TEXT"), ("anchor_before", "TEXT"),
                          ("anchor_lost", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE comment ADD COLUMN {col} {kind}")

    # ---- docs
    def doc(self, repo: str, path: str):
        return self.db.execute("SELECT * FROM doc WHERE repo=? AND path=?", (repo, path)).fetchone()

    def doc_by_id(self, doc_id: str):
        return self.db.execute("SELECT * FROM doc WHERE doc_id=?", (doc_id,)).fetchone()

    def docs(self, repo: str | None = None):
        if repo:
            return self.db.execute("SELECT * FROM doc WHERE repo=? ORDER BY path", (repo,)).fetchall()
        return self.db.execute("SELECT * FROM doc ORDER BY repo, path").fetchall()

    def save_doc(self, repo, path, doc_id, collection_id, base_repo, base_outline,
                 status="ok", detail=None):
        self.db.execute(
            "INSERT INTO doc(repo,path,doc_id,collection_id,base_repo,base_outline,status,detail,"
            "synced_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(repo,path) DO UPDATE SET "
            "doc_id=excluded.doc_id, collection_id=excluded.collection_id, "
            "base_repo=excluded.base_repo, base_outline=excluded.base_outline, "
            "status=excluded.status, detail=excluded.detail, synced_at=excluded.synced_at",
            (repo, path, doc_id, collection_id, base_repo, base_outline, status, detail, time.time()))

    def mark(self, repo, path, status, detail=None):
        self.db.execute("UPDATE doc SET status=?, detail=? WHERE repo=? AND path=?",
                        (status, detail, repo, path))

    # ---- comments
    def add_comment(self, c: dict) -> bool:
        """Record a comment once. Returns True when it is new. A comment stored with
        `delivered_at` set is kept for reading and never handed on as work."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO comment(id,document_id,parent_id,author_id,author,text,anchor,"
            "created_at,received_at,mentions_agent,route,via,delivered_at,resolved_at,anchor_before) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["id"], c["document_id"], c.get("parent_id"), c.get("author_id"), c.get("author"),
             c.get("text"), c.get("anchor"), c["created_at"], time.time(),
             int(bool(c.get("mentions_agent"))), c.get("route"), c["via"], c.get("delivered_at"),
             c.get("resolved_at"), c.get("anchor_before")))
        return cur.rowcount == 1

    def set_place(self, comment_id: str, before: str | None) -> None:
        """Where a comment's passage sits: the words before it in its paragraph ("" when it opens
        it), or None when the text has no mark for it any more (anchor_lost)."""
        self.db.execute("UPDATE comment SET anchor_before=?, anchor_lost=? WHERE id=?",
                        (before, int(before is None), comment_id))

    def anchored(self, document_id: str) -> list[str]:
        """The comments that open a thread on a passage of the document."""
        return [r["id"] for r in self.db.execute(
            "SELECT id FROM comment WHERE document_id=? AND parent_id IS NULL AND anchor IS NOT NULL "
            "AND anchor <> ''", (document_id,)).fetchall()]

    def without_anchor_before(self):
        """Comments on a passage whose place in the document is not recorded yet."""
        return self.db.execute("SELECT id, document_id FROM comment WHERE parent_id IS NULL AND "
                               "anchor IS NOT NULL AND anchor <> '' AND anchor_before IS NULL "
                               "AND anchor_lost = 0 ORDER BY document_id").fetchall()

    def set_resolved(self, comment_id: str, at: str | None) -> None:
        """When a thread was resolved, or None when it was opened again."""
        self.db.execute("UPDATE comment SET resolved_at=? WHERE id=?", (at, comment_id))

    def pending(self):
        return self.db.execute("SELECT * FROM comment WHERE delivered_at IS NULL AND route IS NOT "
                               "NULL ORDER BY received_at").fetchall()

    def delivered(self, ids: list[str], batch_id: str):
        self.db.executemany("UPDATE comment SET delivered_at=?, batch_id=? WHERE id=?",
                            [(time.time(), batch_id, i) for i in ids])

    # ---- small values (watermarks)
    def get(self, k: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def put(self, k: str, v: str):
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, v))


@contextlib.contextmanager
def repo_lock(store: "Store", repo: str):
    """One writer per repo across processes: the serve loop and a chat's `margin link` both sync,
    and two passes that both see "no document yet" would each create one."""
    lock = store.path.parent / f"{store.path.stem}.{repo}.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
