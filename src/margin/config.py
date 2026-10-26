"""margin's configuration: one TOML file, by default ~/.config/margin/config.toml.

    [outline]
    url = "https://docs.example.com"
    api_key_command = ["security", "find-generic-password", "-s", "outline-api-key", "-w"]
    pin_ip_command = ["tailscale", "ip", "-4"]      # optional: resolve the url's host to this IP
    admin_key_command = [...]   # optional: an admin's key, used ONLY to create the webhook
    mcp_url = "http://127.0.0.1:3100/mcp"           # optional: Outline's MCP, default <url>/mcp

    [intake]
    listen = "127.0.0.1:8771"
    webhook_url = "http://host.docker.internal:8771/outline"   # what Outline posts to
    secret_command = ["security", "find-generic-password", "-s", "margin-webhook-secret", "-w"]
    poll_seconds = 600

    [batch]
    quiet_seconds = 180
    cap_seconds = 900

    [notify]
    command = ["my-notifier", "{route}"]     # gets the batch as JSON on stdin. {text} is the
    cwd = "~/somewhere"                       # rendered message, {repo} and {repo_path} name the
                                              # repo of the batch. cwd is optional.
    author_command = ["who-am-i"]             # optional: prints the route of whoever runs
                                              # `margin link`; their documents' comments go there

    [[repo]]
    name = "my-project"                       # becomes the Outline collection
    path = "~/code/my-project"
    include = ["**/*.md"]
    exclude = ["**/node_modules/**"]
    route = "my-project"                      # passed to the notifier as {route}
    routes = { "docs/design/" = "design" }    # optional: by path prefix, longest wins
                                              # A document no route names goes to whoever last
                                              # linked it (author_command), else to `route`.

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
                  "**/__pycache__/**", "**/.playwright-mcp/**", "**/dist/**", "**/build/**",
                  "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**", "**/.tox/**",
                  "**/*.egg-info/**", "**/.eggs/**", "**/.history/**", "**/site-packages/**"]


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
        return self.named_route(path) or self.route

    def named_route(self, path: str) -> str | None:
        """The route `routes` names for a path (the longest matching prefix), or None."""
        best = ""
        for prefix in self.routes:
            if (path == prefix or path.startswith(prefix.rstrip("/") + "/")) and len(prefix) > len(best):
                best = prefix
        return self.routes[best] if best else None


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
    mcp_url: str | None
    author_command: list[str] | None
    repos: list[Repo]
    state: Path

    def run(self, argv: list[str]) -> str:
        return subprocess.check_output(argv, text=True).strip()

    def api_key(self) -> str:
        return self.run(self.api_key_command)

    def secret(self) -> str | None:
        return self.run(self.secret_command) if self.secret_command else None

    def author(self) -> str | None:
        """The route of whoever runs margin now (author_command), or None when there is no such
        command or it does not answer. Never raises: publishing does not depend on it."""
        if not self.author_command:
            return None
        try:
            out = subprocess.run(self.author_command, capture_output=True, text=True, timeout=20,
                                 cwd=str(self.notify_cwd) if self.notify_cwd else None)
        except (OSError, subprocess.SubprocessError):
            return None
        name = (out.stdout or "").strip().splitlines()
        return name[0].strip() if out.returncode == 0 and name and name[0].strip() else None

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
        notify_cwd=Path(n["cwd"]).expanduser() if n.get("cwd") else None,
        mcp_url=o.get("mcp_url"), repos=repos, author_command=n.get("author_command"),
        state=Path(raw.get("state", DEFAULT_STATE)).expanduser())


def locate(cfg: Config, file: Path) -> tuple[Repo, str, Path] | None:
    """repo_of, and also a file in another git worktree of a configured repo: (repo, the file's
    repo-relative path, the folder that path is relative to). Every chat works in its own worktree,
    so a file there is found through git's common directory, which names the main checkout."""
    hit = repo_of(cfg, file)
    if hit:
        return hit[0], hit[1], hit[0].path.resolve()
    import subprocess
    f = file.expanduser().resolve()
    try:
        r = subprocess.run(["git", "-C", str(f.parent), "rev-parse", "--path-format=absolute",
                            "--show-toplevel", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.splitlines()
    if r.returncode != 0 or len(lines) < 2:
        return None
    top, main = Path(lines[0]).resolve(), Path(lines[1]).resolve().parent
    best = None
    for repo in cfg.repos:
        root = repo.path.resolve()
        if root != main and main not in root.parents:
            continue                       # not this git repository
        here = top / root.relative_to(main)  # the configured folder, inside the worktree
        if f == here or here in f.parents:
            if best is None or len(here.parts) > len(best[2].parts):
                best = (repo, f.relative_to(here).as_posix(), here)
    return best


def repo_of(cfg: Config, file: Path) -> tuple[Repo, str] | None:
    """The configured repo a file lives in, and its repo-relative path. The deepest repo wins."""
    f = file.expanduser().resolve()
    best = None
    for r in cfg.repos:
        root = r.path.resolve()
        if f == root or root in f.parents:
            if best is None or len(root.parts) > len(best[0].path.resolve().parts):
                best = (r, f.relative_to(root).as_posix())
    return best
