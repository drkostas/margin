"""margin's configuration: one TOML file, by default ~/.config/margin/config.toml.

    [outline]
    url = "https://docs.example.ts.net:8453"
    api_key_command = ["security", "find-generic-password", "-s", "outline-api-claude", "-w"]
    pin_ip_command = ["tailscale", "ip", "-4"]      # optional: resolve the url's host to this IP
    admin_key_command = [...]   # optional: an admin's key, used ONLY to create the webhook

    [intake]
    listen = "127.0.0.1:8771"
    webhook_url = "http://host.docker.internal:8771/outline"   # what Outline posts to
    secret_command = ["security", "find-generic-password", "-s", "margin-webhook-secret", "-w"]
    poll_seconds = 600

    [batch]
    quiet_seconds = 180
    cap_seconds = 900

    [notify]
    command = ["my-notifier", "{route}"]     # gets the batch as JSON on stdin; {text} is the
    cwd = "~/somewhere"                       # rendered message. cwd is optional.

    [[repo]]
    name = "drlab"                            # becomes the Outline collection
    path = "~/Projects/drlab"
    include = ["**/*.md"]
    exclude = ["**/node_modules/**"]
    route = "drlab"                           # passed to the notifier as {route}
    routes = { "docs/chats/vision.md" = "vision" }   # optional: by path prefix, longest wins

Secrets are never written in the file. Each one is the output of a command.
"""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("MARGIN_CONFIG", "~/.config/margin/config.toml")).expanduser()
DEFAULT_STATE = Path(os.environ.get("MARGIN_STATE", "~/.local/state/margin/margin.db")).expanduser()

ALWAYS_EXCLUDE = ["**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
                  "**/__pycache__/**", "**/.playwright-mcp/**", "**/dist/**", "**/build/**"]


@dataclass
class Repo:
    name: str
    path: Path
    include: list[str] = field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = field(default_factory=list)
    route: str = ""
    routes: dict[str, str] = field(default_factory=dict)

    def route_for(self, path: str) -> str:
        """The route for a repo-relative path: the longest matching prefix, else the repo's."""
        best = ""
        for prefix in self.routes:
            if (path == prefix or path.startswith(prefix.rstrip("/") + "/")) and len(prefix) > len(best):
                best = prefix
        return self.routes[best] if best else self.route


@dataclass
class Config:
    url: str
    api_key_command: list[str]
    admin_key_command: list[str] | None
    pin_ip_command: list[str] | None
    listen: str
    webhook_url: str
    secret_command: list[str] | None
    poll_seconds: int
    quiet_seconds: int
    cap_seconds: int
    notify_command: list[str] | None
    notify_cwd: Path | None
    repos: list[Repo]
    state: Path

    def run(self, argv: list[str]) -> str:
        return subprocess.check_output(argv, text=True).strip()

    def api_key(self) -> str:
        return self.run(self.api_key_command)

    def secret(self) -> str | None:
        return self.run(self.secret_command) if self.secret_command else None

    def repo(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise KeyError(f"no repo named {name!r} in the config")


def load(path: Path = DEFAULT_PATH) -> Config:
    raw = tomllib.loads(path.read_text())
    o, i, b, n = (raw.get(k, {}) for k in ("outline", "intake", "batch", "notify"))
    repos = [Repo(name=r["name"], path=Path(r["path"]).expanduser(),
                  include=r.get("include", ["**/*.md"]), exclude=r.get("exclude", []),
                  route=r.get("route", r["name"]), routes=r.get("routes", {}))
             for r in raw.get("repo", [])]
    names = [r.name for r in repos]
    if len(names) != len(set(names)):
        raise ValueError("two [[repo]] entries share a name")
    return Config(
        url=o["url"], api_key_command=o["api_key_command"],
        admin_key_command=o.get("admin_key_command"), pin_ip_command=o.get("pin_ip_command"),
        listen=i.get("listen", "127.0.0.1:8771"), webhook_url=i.get("webhook_url", ""),
        secret_command=i.get("secret_command"), poll_seconds=int(i.get("poll_seconds", 600)),
        quiet_seconds=int(b.get("quiet_seconds", 180)), cap_seconds=int(b.get("cap_seconds", 900)),
        notify_command=n.get("command"),
        notify_cwd=Path(n["cwd"]).expanduser() if n.get("cwd") else None, repos=repos,
        state=Path(raw.get("state", DEFAULT_STATE)).expanduser())
