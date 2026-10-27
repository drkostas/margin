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
import os
import json
import sys
import time

from . import batch
from .config import DEFAULT_PATH, load, locate
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
    p = sub.add_parser("accept-file", help="let the file win a conflict no person made")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("accept-merged",
                       help="publish a file that now carries a person's Outline edit")
    p.add_argument("files", nargs="+")
    p.add_argument("--undo", action="append", default=[],
                   help="text from a part of their edit the person agreed to undo (repeatable)")
    sub.add_parser("setup-claude")
    sub.add_parser("anchors", help="record where each older comment's passage sits in its document")
    p = sub.add_parser("pinch-check", help="pinch a fold page on an Android phone over adb and judge each pinch")
    p.add_argument("page", help="the fold page (margin fold --html)")
    p.add_argument("--serial", required=True, help="the phone, as adb names it")
    p.add_argument("--package", default="com.brave.browser", help="the phone's Chromium browser")
    p.add_argument("--out", help="the folder for the table, the readings and the recording")
    p.add_argument("--wait-unlock", type=float, default=0.0,
                   help="seconds to wait for the phone to be awake and unlocked (never unlocked for you)")
    p = sub.add_parser("fold", help="fold a markdown file into five levels of detail, each checked")
    p.add_argument("file")
    p.add_argument("--out", help="write the fold as JSON here")
    p.add_argument("--html", help="write a page to read the fold here")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--judge-model", default="opus")
    a = ap.parse_args(argv)

    if a.cmd == "pinch-check":
        from pathlib import Path as P
        from .pinch_check import run as pinch_run
        return pinch_run(P(a.page).expanduser().resolve(), a.serial, package=a.package,
                         out=P(a.out).expanduser() if a.out else None, wait_unlock=a.wait_unlock)

    if a.cmd == "fold":
        from functools import partial
        from pathlib import Path as P
        from .fold import ask_claude, ask_judge, fold_file
        cache = P(os.environ.get("MARGIN_FOLD_CACHE", "~/.cache/margin/folds")).expanduser()
        f = fold_file(P(a.file), out=P(a.out) if a.out else None, html_out=P(a.html) if a.html else None,
                      cache=cache, ask=partial(ask_claude, model=a.model),
                      judge=partial(ask_judge, model=a.judge_model))
        c = f["counts"]
        print(f"folded {a.file}: {c['units']} paragraphs, {c['claude']} by Claude, "
              f"{c['author']} kept in the author's words, {c['calls']} calls")
        for s in f["sections"]:
            for u in s["units"]:
                if u["source"] == "author":
                    print(f"  author's words: {u['id']}: {'; '.join(u['refused'])}")
            if s["section"]["source"] == "author":
                print(f"  section in the author's words: {s['id']}: {'; '.join(s['section']['refused'])}")
        return 0

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
                # a changed text moves its passages, and a rewritten one loses its mark
                row = store.doc(repo, r.path) if r.action in ("patched", "outline-edited") else None
                if row:
                    inbox.place(row["doc_id"])

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

    if a.cmd == "anchors":
        inbox = Inbox(cfg, o, store)
        done = gone = 0
        for doc_id in dict.fromkeys(r["document_id"] for r in store.without_anchor_before()):
            p, g = inbox.place(doc_id)
            done, gone = done + p, gone + g
        print(f"{done} passage(s) placed, {gone} with no mark left in their document")
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
            hit = locate(cfg, Path(f))
            if not hit:
                print(f"not published: {f} is in no configured repo (nor in a git worktree of "
                      "one). Put it in a repo margin publishes, or add its repo to the config.",
                      file=sys.stderr)
                bad = 1
                continue
            by_repo.setdefault((hit[0].name, hit[2]), (hit[0], []))[1].append(hit[1])
        author = cfg.author()            # the comments on what you link come back to you
        for (name, root), (repo, rels) in by_repo.items():
            got = {rel: (d, st, det)
                   for rel, d, st, det in Syncer(o, store, repo).publish_status(rels, root)}
            for rel, (d, _, _) in got.items():
                if d and author:
                    store.put(f"author:{d['id']}", author)
            for rel in rels:
                d, status, detail = got.get(rel, (None, "", ""))
                if d and status == "ok":
                    print(f"[{d['title']}]({doc_url(cfg.url, d)})")
                elif d:
                    # The document exists but does not say what the file says. A link printed here
                    # read as "published" and a chat reported a section that never reached Outline.
                    # ⭐ which kind it is, so a chat neither stops to ask about an edit nobody made
                    # nor forces its file over one a person did make (2026-10-16)
                    why = (" Nobody edited it: margin could not patch it into the file's text. "
                           "`margin accept-file` publishes the file when only margin has edited it."
                           if status in ("cannot-patch", "drift") else
                           " Someone may have edited it in Outline: `margin accept-file` publishes the "
                           "file only if nobody did, and otherwise names who did.")
                    print(f"not published: {name}/{rel} is {status}"
                          + (f" ({detail})" if detail else "")
                          + f". The document still has the older text: [{d['title']}]"
                          f"({doc_url(cfg.url, d)}).{why}", file=sys.stderr)
                    bad = 1
                else:
                    print(f"not published: {name}/{rel} is excluded by the repo's include or "
                          "exclude patterns, or is not markdown", file=sys.stderr)
                    bad = 1
        return bad

    if a.cmd in ("accept-file", "accept-merged"):
        from .outline import doc_url
        bad = 0
        for f in a.files:
            hit = locate(cfg, Path(f))
            if not hit:
                print(f"refused: {f} is in no configured repo", file=sys.stderr)
                bad = 1
                continue
            s = Syncer(o, store, hit[0])
            if a.cmd == "accept-file":
                r = s.accept_file(hit[1], hit[2])
            else:
                r = s.accept_merged(hit[1], hit[2], tuple(a.undo))
            row = store.doc(hit[0].name, r.path)
            if r.action in ("patched", "unchanged") and row and row["status"] == "ok":
                d = o.document(row["doc_id"])
                print(f"accepted the file: [{d['title']}]({doc_url(cfg.url, d)})")
            else:
                print(f"refused: {hit[0].name}/{hit[1]} ({r.action}"
                      + (f": {r.detail}" if r.detail else "") + ")", file=sys.stderr)
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
