"""A small client for Outline's RPC-style API (POST /api/<method>, JSON in and out).

Only what margin needs: collections, documents, comments and webhook subscriptions. Standard library
only. Errors raise OutlineError with the HTTP status and Outline's own error code, so a caller can
tell "the text to patch was not found" (404) from "not allowed" (403) from "the server is down".
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request


class OutlineError(Exception):
    def __init__(self, method: str, status: int, code: str, message: str):
        super().__init__(f"{method}: {status} {code} {message}")
        self.method, self.status, self.code, self.message = method, status, code, message


def pin_host(host: str, ip: str) -> None:
    """Resolve `host` to `ip` for this process. TLS still checks the certificate against `host`.

    Needed on a machine that serves Outline over Tailscale but cannot resolve its own MagicDNS name.
    """
    real = socket.getaddrinfo

    def gai(h, *a, **k):
        return real(ip if h == host else h, *a, **k)

    socket.getaddrinfo = gai


class Outline:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._me: dict | None = None

    def call(self, method: str, body: dict | None = None, retries: int = 3) -> dict:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            f"{self.url}/api/{method}", data,
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    j = json.loads(raw)
                except ValueError:
                    j = {}
                if e.code == 429 and attempt < retries:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                raise OutlineError(method, e.code, j.get("error", ""), j.get("message", raw[:200]))
            except urllib.error.URLError:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise AssertionError("unreachable")

    def data(self, method: str, body: dict | None = None):
        return self.call(method, body).get("data")

    def paged(self, method: str, body: dict | None = None, limit: int = 100):
        """Every row of a paginated list method."""
        offset = 0
        while True:
            page = self.data(method, {**(body or {}), "limit": limit, "offset": offset}) or []
            yield from page
            if len(page) < limit:
                return
            offset += limit

    # ---- who am I
    def me(self) -> dict:
        if self._me is None:
            self._me = self.data("auth.info")["user"]
        return self._me

    # ---- collections
    def collection_by_name(self, name: str) -> dict | None:
        for c in self.paged("collections.list"):
            if c["name"] == name:
                return c
        return None

    def ensure_collection(self, name: str, description: str = "") -> dict:
        return self.collection_by_name(name) or self.data(
            "collections.create", {"name": name, "description": description,
                                   "permission": "read_write", "sharing": False})

    # ---- documents
    def create_document(self, collection_id: str, title: str, text: str,
                        parent_id: str | None = None) -> dict:
        body = {"collectionId": collection_id, "title": title, "text": text, "publish": True}
        if parent_id:
            body["parentDocumentId"] = parent_id
        return self.data("documents.create", body)

    def document(self, doc_id: str) -> dict:
        return self.data("documents.info", {"id": doc_id})

    def patch(self, doc_id: str, find: str, text: str) -> dict:
        """Replace the first occurrence of `find` (markdown) with `text`. Keeps comments elsewhere."""
        return self.data("documents.update",
                         {"id": doc_id, "editMode": "patch", "findText": find, "text": text})

    def append(self, doc_id: str, text: str) -> dict:
        """Add `text` after the last block. Touches no existing block, so every comment stays."""
        return self.data("documents.update", {"id": doc_id, "editMode": "append", "text": text})

    def replace(self, doc_id: str, text: str) -> dict:
        """Replace the whole body. Removes every comment anchor, so only for docs margin owns
        outright (the scratch document), never for a document someone may have commented on."""
        return self.data("documents.update", {"id": doc_id, "editMode": "replace", "text": text})

    def set_title(self, doc_id: str, title: str) -> dict:
        return self.data("documents.update", {"id": doc_id, "title": title})

    # ---- comments
    def comments(self, collection_id: str | None = None, document_id: str | None = None):
        body: dict = {"includeAnchorText": True, "sort": "createdAt", "direction": "DESC"}
        if collection_id:
            body["collectionId"] = collection_id
        if document_id:
            body["documentId"] = document_id
        return self.paged("comments.list", body)

    def comment(self, comment_id: str) -> dict:
        return self.data("comments.info", {"id": comment_id, "includeAnchorText": True})

    def reply(self, document_id: str, parent_id: str | None, text: str,
              anchor: str | None = None) -> dict:
        body: dict = {"documentId": document_id, "text": text}
        if parent_id:
            body["parentCommentId"] = parent_id
        if anchor:
            body["anchorText"] = anchor
        return self.data("comments.create", body)

    def resolve(self, comment_id: str) -> dict:
        return self.data("comments.resolve", {"id": comment_id})

    # ---- webhooks
    def webhooks(self) -> list[dict]:
        return list(self.paged("webhookSubscriptions.list"))

    def ensure_webhook(self, name: str, url: str, secret: str, events: list[str]) -> dict:
        """Create the subscription, or repair it. Outline switches a subscription off after
        repeated failed deliveries, and an update switches it back on."""
        for w in self.webhooks():
            if w.get("url") == url:
                if w.get("enabled") is False or sorted(w.get("events") or []) != sorted(events):
                    w = self.data("webhookSubscriptions.update",
                                  {"id": w["id"], "name": name, "url": url, "secret": secret,
                                   "events": events})
                return w
        return self.data("webhookSubscriptions.create",
                         {"name": name, "url": url, "secret": secret, "events": events})


def doc_url(base: str, doc: dict) -> str:
    return f"{base.rstrip('/')}{doc.get('url') or '/doc/' + doc['id']}"


def comment_text(data: dict | None) -> str:
    """Plain text of a ProseMirror comment body. Mentions render as @Label."""
    out: list[str] = []

    def walk(node: dict) -> None:
        t = node.get("type")
        if t == "text":
            out.append(node.get("text", ""))
        elif t == "mention":
            out.append("@" + (node.get("attrs", {}).get("label") or ""))
        elif t == "hard_break":
            out.append("\n")
        for child in node.get("content", []) or []:
            walk(child)
        if t in ("paragraph", "heading", "list_item", "blockquote"):
            out.append("\n")

    walk(data or {})
    return "".join(out).strip()


def mentions(data: dict | None) -> set[str]:
    """User ids mentioned in a ProseMirror comment body."""
    found: set[str] = set()

    def walk(node: dict) -> None:
        if node.get("type") == "mention":
            a = node.get("attrs", {})
            if a.get("type", "user") == "user" and a.get("modelId"):
                found.add(a["modelId"])
        for child in node.get("content", []) or []:
            walk(child)

    walk(data or {})
    return found


def quote(s: str) -> str:
    return urllib.parse.quote(s, safe="")
