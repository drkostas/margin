"""margin: publish repo markdown to Outline and bring comments back to an agent.

    margin sync [REPO...] [--full]     publish changed files (all repos when none named)
    margin serve                       webhook receiver + catch-up poll + batching + periodic sync
    margin webhook                     create Outline's webhook subscription if it is missing
    margin status                      documents that need attention, and comments waiting
    margin reply COMMENT_ID TEXT       answer in the comment's thread, as the configured user
    margin resolve COMMENT_ID          mark the thread resolved
    margin thread COMMENT_ID           print a whole thread with its quoted text
    margin link FILE...                publish these files now and print a Markdown link to each
    margin setup-claude                install the skill, Outline's MCP and its tool denials
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import batch
from .config import DEFAULT_PATH, load, repo_of
from .inbox import EVENTS, Inbox, serve
from .outline import Outline, comment_text, pin_host
from .store import Store
from .sync import Syncer


def connect(cfg):
    if cfg.pin_ip_command:
        from urllib.parse import urlparse
        pin_host(urlparse(cfg.url).hostname, cfg.run(cfg.pin_ip_command).split()[0])
    return Outline(cfg.url, cfg.api_key())


def ensure_webhook(cfg, o: Outline) -> str:
    """Webhook subscriptions need an admin. The agent's own key is a member's, on purpose, so the
    admin key is used for this one call only and never kept on the client."""
    if not (cfg.webhook_url and cfg.secret_command):
        return "no webhook configured; the catch-up poll is the only path"
    if not cfg.admin_key_command:
        return "no admin_key_command; create the webhook once as an admin (margin webhook)"
    admin = Outline(cfg.url, cfg.run(cfg.admin_key_command))
    w = admin.ensure_webhook("margin", cfg.webhook_url, cfg.secret(), EVENTS)
    return f"webhook {w['id']} -> {w['url']} ({', '.join(w.get('events') or [])})"


def thread_of(o: Outline, comment_id: str) -> tuple[dict, list[dict]]:
    c = o.comment(comment_id)
    root = o.comment(c["parentCommentId"]) if c.get("parentCommentId") else c
    rows = [x for x in o.comments(document_id=root["documentId"])
            if x["id"] == root["id"] or x.get("parentCommentId") == root["id"]]
    return root, sorted(rows, key=lambda x: x["createdAt"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="margin", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("repos", nargs="*")
    p.add_argument("--full", action="store_true")
    sub.add_parser("serve")
    sub.add_parser("webhook")
    sub.add_parser("status")
    p = sub.add_parser("reply")
    p.add_argument("comment_id")
    p.add_argument("text")
    p = sub.add_parser("resolve")
    p.add_argument("comment_id")
    p = sub.add_parser("thread")
    p.add_argument("comment_id")
    p = sub.add_parser("link")
    p.add_argument("files", nargs="+")
    sub.add_parser("setup-claude")
    a = ap.parse_args(argv)

    from pathlib import Path
    cfg = load(Path(a.config).expanduser())
    if a.cmd == "setup-claude":
        from .claude import setup
        for line in setup(cfg):
            print(line)
        return 0
    o = connect(cfg)
    store = Store(cfg.state)

    def sync_all(names=(), full=False, quiet=True):
        out = []
        for repo in cfg.repos:
            if names and repo.name not in names:
                continue
            for r in Syncer(o, store, repo, full=full).run():
                if not quiet or r.action != "unchanged":
                    out.append((repo.name, r))
        return out

    if a.cmd == "sync":
        unknown = set(a.repos) - {r.name for r in cfg.repos}
        if unknown:
            print(f"unknown repo(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        res = sync_all(a.repos, a.full)
        for repo, r in res:
            print(f"{r.action:18} {repo}/{r.path}" + (f"  ({r.detail})" if r.detail else ""))
        if not res:
            print("nothing changed")
        return 1 if any(r.action in ("error", "cannot-patch") for _, r in res) else 0

    if a.cmd == "serve":
        inbox = Inbox(cfg, o, store)
        try:
            print(ensure_webhook(cfg, o), flush=True)
        except Exception as e:                   # the poll still delivers without the webhook
            print(f"webhook not ensured: {e}", file=sys.stderr, flush=True)

        def sync_pass():
            for repo, r in sync_all():
                print(f"sync: {r.action} {repo}/{r.path}" + (f"  ({r.detail})" if r.detail else ""),
                      flush=True)

        serve(inbox, sync_pass)
        return 0

    if a.cmd == "webhook":
        print(ensure_webhook(cfg, o))
        return 0

    if a.cmd == "status":
        bad = [d for d in store.docs() if d["status"] != "ok"]
        for d in bad:
            print(f"{d['status']:18} {d['repo']}/{d['path']}  {d['detail'] or ''}")
        pend = store.pending()
        wait = batch.due_in(pend, time.time(), cfg.quiet_seconds, cfg.cap_seconds)
        print(f"{len(store.docs())} documents, {len(bad)} need attention, {len(pend)} comment(s) "
              "waiting" + (f", next batch in {int(wait)} s" if wait is not None else ""))
        return 0

    if a.cmd == "reply":
        c = o.comment(a.comment_id)
        root = c.get("parentCommentId") or c["id"]
        r = o.reply(c["documentId"], root, a.text)
        print(f"replied in thread {root} as {o.me()['name']}: {r['id']}")
        return 0

    if a.cmd == "resolve":
        c = o.comment(a.comment_id)
        o.resolve(c.get("parentCommentId") or c["id"])
        print("resolved")
        return 0

    if a.cmd == "link":
        from .outline import doc_url
        by_repo: dict = {}
        bad = 0
        for f in a.files:
            hit = repo_of(cfg, Path(f))
            if not hit:
                print(f"not published: {f} is in no configured repo. Put it in a repo margin "
                      "publishes, or add its repo to the config.", file=sys.stderr)
                bad = 1
                continue
            by_repo.setdefault(hit[0].name, (hit[0], []))[1].append(hit[1])
        for name, (repo, rels) in by_repo.items():
            got = dict(Syncer(o, store, repo).publish(rels))
            for rel in rels:
                d = got.get(rel)
                if d:
                    print(f"[{d['title']}]({doc_url(cfg.url, d)})")
                else:
                    print(f"not published: {name}/{rel} is excluded by the repo's include or "
                          "exclude patterns, or is not markdown", file=sys.stderr)
                    bad = 1
        return bad

    if a.cmd == "thread":
        root, rows = thread_of(o, a.comment_id)
        if root.get("anchorText"):
            print(f'on "{root["anchorText"]}"')
        for x in rows:
            print(f"[{x['id']}] {x['createdBy']['name']}: {comment_text(x.get('data'))}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
