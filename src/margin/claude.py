"""`margin setup-claude`: wire margin and Outline into Claude Code, idempotently.

Three things, each safe to run again:

1. the margin skill, linked into ~/.claude/skills/margin from this package, so it updates with it;
2. Outline's own MCP server registered in ~/.claude.json, with the agent's key supplied at connect
   time by a small headers script that runs `api_key_command`, so the key is never written into
   a config file;
3. Outline's destructive MCP tools denied in ~/.claude/settings.json. An agent that deletes or
   moves a document, or changes a collection, would break the folder structure margin mirrors.

Both JSON files are backed up once per run before they are rewritten, atomically.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from .config import Config

DENY = ["delete_document", "delete_collection", "update_collection", "create_collection",
        "move_document"]


def _rewrite(path: Path, change) -> bool:
    data = json.loads(path.read_text()) if path.exists() else {}
    before = json.dumps(data, sort_keys=True)
    change(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak-margin-{time.strftime('%Y%m%d%H%M%S')}"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return True


def setup(cfg: Config, home: Path | None = None, name: str = "outline") -> list[str]:
    home = home or Path.home()
    done = []

    # 1. the skill
    skill_src = Path(__file__).parent / "skills" / "margin"
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    link = skills / "margin"
    if link.is_symlink() or not link.exists():
        if not (link.is_symlink() and Path(os.readlink(link)) == skill_src):
            if link.is_symlink():
                link.unlink()
            link.symlink_to(skill_src)
            done.append(f"skill linked: {link} -> {skill_src}")
    else:
        done.append(f"skill left alone: {link} exists and is not a link")

    # 2. the MCP server and its headers script
    helper = home / ".config" / "margin" / "outline-mcp-headers.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    body = ("#!/bin/sh\n# written by margin setup-claude: the agent's Outline key, read at connect time\n"
            f"k=$({shlex.join(cfg.api_key_command)}) || exit 1\n"
            "printf '{\"Authorization\": \"Bearer %s\"}\\n' \"$k\"\n")
    if not helper.exists() or helper.read_text() != body:
        helper.write_text(body)
        done.append(f"headers script written: {helper}")
    helper.chmod(0o700)
    url = cfg.mcp_url or cfg.url.rstrip("/") + "/mcp"

    def add_server(d):
        d.setdefault("mcpServers", {})[name] = {"type": "http", "url": url,
                                               "headersHelper": str(helper)}
    if _rewrite(home / ".claude.json", add_server):
        done.append(f"MCP server '{name}' registered at {url}")

    # 3. deny the destructive tools
    def deny(d):
        lst = d.setdefault("permissions", {}).setdefault("deny", [])
        for t in DENY:
            if f"mcp__{name}__{t}" not in lst:
                lst.append(f"mcp__{name}__{t}")
    if _rewrite(home / ".claude" / "settings.json", deny):
        done.append("destructive Outline tools denied: " + ", ".join(DENY))
    return done or ["already set up"]
