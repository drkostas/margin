"""Comments in: the webhook receiver, the catch-up poll, and handing ready batches to a notifier.

The webhook is the fast path. The poll is the guarantee: every `poll_seconds` it asks Outline for
comments newer than the newest one it has seen, so a comment made while this process was down or
while the Mac slept is still delivered. Both paths write to the same table, keyed by comment id,
so a comment that arrives both ways is recorded once.

The agent's own comments are never recorded, so the agent cannot wake itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import batch
from .config import Config
from .outline import Outline, OutlineError, comment_text, doc_url, mentions
from .store import Store

EVENTS = ["comments.create", "comments.update", "documents.update"]


def verify(secret: str | None, header: str | None, body: bytes, tolerance: float = 300) -> bool:
    """Outline-Signature is `t=<ms>,s=<hex>`, the hex being HMAC-SHA256 of `t + "." + body`."""
    if not secret:
        return True
    if not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, s = parts.get("t"), parts.get("s")
    if not t or not s:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    try:
        fresh = abs(time.time() - int(t) / 1000) <= tolerance
    except ValueError:
        return False
    return fresh and hmac.compare_digest(want, s)


class Inbox:
    def __init__(self, cfg: Config, outline: Outline, store: Store):
        self.cfg, self.o, self.s = cfg, outline, store
        self.agent_id = outline.me()["id"]
        self.repos = {r.name: r for r in cfg.repos}
        self.lock = threading.Lock()

    # ---- recording
    def route_of(self, document_id: str) -> str | None:
        row = self.s.doc_by_id(document_id)
        repo = self.repos.get(row["repo"]) if row else None
        return repo.route_for(row["path"]) if repo else None

    def record(self, c: dict, via: str) -> bool:
        if c.get("createdById") == self.agent_id:
            return False
        rec = {
            "id": c["id"], "document_id": c["documentId"], "parent_id": c.get("parentCommentId"),
            "author_id": c.get("createdById"), "author": (c.get("createdBy") or {}).get("name"),
            "text": comment_text(c.get("data")), "anchor": c.get("anchorText"),
            "created_at": c["createdAt"], "mentions_agent": self.agent_id in mentions(c.get("data")),
            "route": self.route_of(c["documentId"]), "via": via,
        }
        with self.lock:
            return self.s.add_comment(rec)

    def on_webhook(self, event: dict) -> str:
        kind = event.get("event")
        payload = event.get("payload") or {}
        if kind == "documents.update":
            if payload.get("id"):
                self.s.put(f"dirty:{payload['id']}", "1")
            return "noted"
        if kind not in ("comments.create", "comments.update"):
            return "ignored"
        model = payload.get("model")
        if not model or model.get("anchorText") is None:
            try:          # the webhook body has no anchor text; ask for it
                model = self.o.comment(payload.get("id") or model["id"])
            except (OutlineError, KeyError, TypeError):
                pass
        if not model:
            return "no model"
        if kind == "comments.update":
            with self.lock:
                self.s.db.execute("UPDATE comment SET text=? WHERE id=? AND delivered_at IS NULL",
                                  (comment_text(model.get("data")), model["id"]))
            return "updated"
        return "recorded" if self.record(model, "webhook") else "known"

    # ---- the catch-up poll
    def poll(self) -> int:
        n = 0
        for repo in self.cfg.repos:
            cid = self.s.get(f"collection:{repo.name}")
            if not cid:
                continue
            key = f"poll:{repo.name}"
            mark = self.s.get(key)
            newest = mark
            for c in self.o.comments(collection_id=cid):
                if mark and c["createdAt"] <= mark:
                    break
                newest = max(newest or "", c["createdAt"])
                if mark is None:
                    continue          # first run: learn where we are, deliver nothing old
                n += self.record(c, "poll")
            if mark is None and newest is None:
                # first run on a collection with no comments yet: start from now, or the next
                # pass would think it is the first run again and skip the first comments
                newest = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            if newest:
                self.s.put(key, newest)
        return n

    # ---- delivery
    def render(self, route: str, rows) -> dict:
        items, lines = [], []
        by_doc: dict[str, list] = {}
        for r in rows:
            by_doc.setdefault(r["document_id"], []).append(r)
        for doc_id, rs in by_doc.items():
            row = self.s.doc_by_id(doc_id)
            try:
                d = self.o.document(doc_id)
                title, url = d["title"], doc_url(self.cfg.url, d)
            except OutlineError:
                title, url = doc_id, ""
            path = f"{row['repo']}/{row['path']}" if row else ""
            lines.append(f"### {title}\n{path}  {url}".rstrip())
            for r in rs:
                where = f'on "{r["anchor"]}"' if r["anchor"] else (
                    f"reply in thread {r['parent_id']}" if r["parent_id"] else "on the document")
                lines.append(f"- [{r['id']}] {r['author']} {where}:\n  {r['text']}")
                items.append({**dict(r), "doc_title": title, "doc_url": url, "path": path})
            lines.append("")
        text = (f"{len(rows)} new comment(s) in Outline.\n\n" + "\n".join(lines) +
                "\nAnswer each one in its thread: `margin reply <comment-id> \"...\"`. "
                "Change the repo file when the comment asks for a change, then `margin sync`. "
                "`margin resolve <comment-id>` once it is settled.")
        first = self.s.doc_by_id(rows[0]["document_id"]) if rows else None
        repo = self.repos.get(first["repo"]) if first else None
        return {"route": route, "comments": items, "text": text,
                "repo": repo.name if repo else "", "repo_path": str(repo.path) if repo else ""}

    def deliver(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        sent = []
        with self.lock:
            due = batch.ready(self.s.pending(), now, self.cfg.quiet_seconds, self.cfg.cap_seconds)
        for route, rows in due.items():
            msg = self.render(route, rows)
            bid = uuid.uuid4().hex[:12]
            msg["batch_id"] = bid
            if self.notify(route, msg):
                with self.lock:
                    self.s.delivered([r["id"] for r in rows], bid)
                sent.append(route)
        return sent

    def notify(self, route: str, msg: dict) -> bool:
        if not self.cfg.notify_command:
            print(msg["text"], flush=True)
            return True
        subs = {"{route}": route, "{text}": msg["text"], "{repo}": msg.get("repo", ""),
                "{repo_path}": msg.get("repo_path", "")}
        argv = []
        for a in self.cfg.notify_command:
            for k, v in subs.items():
                a = a.replace(k, v)
            argv.append(a)
        p = subprocess.run(argv, input=json.dumps(msg), text=True, capture_output=True,
                           cwd=self.cfg.notify_cwd)
        if p.returncode != 0:
            print(f"notifier failed for {route} ({p.returncode}): {p.stderr.strip()[:300]}",
                  file=sys.stderr, flush=True)
        return p.returncode == 0


def serve(inbox: Inbox, sync_pass=None, sync_every: float = 60.0) -> None:
    """Run the webhook receiver, then loop: deliver ready batches, poll, and sync."""
    host, port = inbox.cfg.listen.rsplit(":", 1)
    secret = inbox.cfg.secret()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.rstrip("/") != "/outline" or not verify(
                    secret, self.headers.get("Outline-Signature"), body):
                self.send_response(401)
                self.end_headers()
                return
            try:
                result = inbox.on_webhook(json.loads(body))
                code = 200
            except Exception as e:                       # never let one bad event stop the server
                result, code = f"error: {e}", 500
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(result.encode())

        def do_GET(self):
            ok = self.path.rstrip("/") == "/health"
            self.send_response(200 if ok else 404)
            self.end_headers()
            if ok:
                self.wfile.write(json.dumps({"ok": True, "pending": len(inbox.s.pending())}).encode())

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"margin listening on {inbox.cfg.listen}", flush=True)
    last_poll = last_sync = 0.0
    while True:
        now = time.time()
        try:
            if now - last_sync >= sync_every and sync_pass:
                sync_pass()
                last_sync = now
            if now - last_poll >= inbox.cfg.poll_seconds:
                inbox.poll()
                last_poll = now
            for route in inbox.deliver(now):
                print(f"delivered a batch to {route}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr, flush=True)
        time.sleep(5)
