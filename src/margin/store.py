"""margin's state: one SQLite file. What was synced, which comments arrived, which were delivered.

Nothing here is a second copy of the documents. `doc` keeps, per file, the text both sides had at the
last sync, which is exactly what is needed to tell "only the repo changed" from "both changed".
"""
from __future__ import annotations

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
  delivered_at REAL, batch_id TEXT
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
        # One connection shared by the webhook thread and the main loop, so every statement
        # goes through one lock.
        self._db = sqlite3.connect(str(path), isolation_level=None, timeout=30,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db = _Locked(self._db, self.lock)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

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
        """Record a comment once. Returns True when it is new."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO comment(id,document_id,parent_id,author_id,author,text,anchor,"
            "created_at,received_at,mentions_agent,route,via) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["id"], c["document_id"], c.get("parent_id"), c.get("author_id"), c.get("author"),
             c.get("text"), c.get("anchor"), c["created_at"], time.time(),
             int(bool(c.get("mentions_agent"))), c.get("route"), c["via"]))
        return cur.rowcount == 1

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
