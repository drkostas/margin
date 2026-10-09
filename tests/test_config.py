from margin.config import Repo, load


def test_route_by_longest_prefix(tmp_path):
    r = Repo("drlab", tmp_path, route="core",
             routes={"docs/chats": "chats", "docs/chats/vision.md": "vision"})
    assert r.route_for("docs/chats/vision.md") == "vision"
    assert r.route_for("docs/chats/sessions.md") == "chats"
    assert r.route_for("docs/chatsx.md") == "core"
    assert r.route_for("README.md") == "core"


def test_load_reads_routes_and_notify_cwd(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('''
[outline]
url = "https://x"
api_key_command = ["echo", "k"]
[notify]
command = ["n", "{route}", "{text}"]
cwd = "/tmp"
[[repo]]
name = "a"
path = "/tmp"
routes = { "docs" = "d" }
''')
    c = load(p)
    assert c.notify_cwd.as_posix() == "/tmp" and c.repos[0].route_for("docs/x.md") == "d"
    assert c.repos[0].route_for("x.md") == "a"


def test_notify_substitutes_repo_and_path(tmp_path):
    import json
    from margin.inbox import Inbox
    out = tmp_path / "argv.json"
    cfg = type("C", (), {})()
    cfg.notify_command = ["python3", "-c",
                          f"import sys,json; json.dump(sys.argv[1:], open({str(out)!r},'w'))",
                          "{route}", "{repo}", "{repo_path}", "{text}"]
    cfg.notify_cwd = None
    inbox = Inbox.__new__(Inbox)
    inbox.cfg = cfg
    assert inbox.notify("r", {"text": "hello", "repo": "drlab", "repo_path": "/x/drlab"})
    assert json.loads(out.read_text()) == ["r", "drlab", "/x/drlab", "hello"]


def test_repo_of_picks_the_deepest_repo(tmp_path):
    from margin.config import Config, repo_of
    (tmp_path / "a" / "b" / "docs").mkdir(parents=True)
    outer, inner = Repo("outer", tmp_path / "a"), Repo("inner", tmp_path / "a" / "b")
    cfg = Config.__new__(Config)
    cfg.repos = [outer, inner]
    f = tmp_path / "a" / "b" / "docs" / "x.md"
    assert repo_of(cfg, f) == (inner, "docs/x.md")
    assert repo_of(cfg, tmp_path / "a" / "y.md") == (outer, "y.md")
    assert repo_of(cfg, tmp_path / "z.md") is None


def test_setup_claude_is_idempotent(tmp_path):
    import json
    from margin.claude import setup
    from margin.config import Config
    cfg = Config.__new__(Config)
    cfg.api_key_command = ["echo", "k"]
    cfg.url, cfg.mcp_url = "https://docs.example.com", None
    first = setup(cfg, home=tmp_path)
    assert any("registered" in x for x in first)
    assert setup(cfg, home=tmp_path) == ["already set up"]
    d = json.loads((tmp_path / ".claude.json").read_text())
    assert d["mcpServers"]["outline"]["url"] == "https://docs.example.com/mcp"
    deny = json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "mcp__outline__delete_document" in deny
    assert (tmp_path / ".claude" / "skills" / "margin" / "SKILL.md").exists()
