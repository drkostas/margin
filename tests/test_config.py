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


def test_a_document_no_route_names_goes_to_whoever_linked_it(tmp_path):
    """2026-10-13: a chat's new spec had no line in `routes`, so the owner's comments on it went to
    the default route, and another chat had to forward them. `margin link` now records who ran it
    (author_command), and that is where the document's comments go unless `routes` names one."""
    import sys
    from margin.config import Config
    from margin.inbox import Inbox
    from margin.store import Store

    def cfg(cmd):
        return Config(url="u", api_key_command=["true"], admin_key_command=None, pin_ip_command=None,
                      listen="", webhook_url="", secret_command=None, poll_seconds=1,
                      quiet_seconds=1, cap_seconds=1, notify_command=None, notify_cwd=None,
                      mcp_url=None, repos=[Repo("drlab", tmp_path, route="core",
                                                routes={"docs/chats/vision.md": "vision"})],
                      state=tmp_path / "s.db", author_command=cmd)

    assert cfg([sys.executable, "-c", "print('sessions-chat')"]).author() == "sessions-chat"
    assert cfg([sys.executable, "-c", "import sys; sys.exit(3)"]).author() is None
    assert cfg(None).author() is None and cfg(["/no/such/command"]).author() is None

    class FakeOutline:
        def me(self):
            return {"id": "u-agent"}

    store = Store(tmp_path / "s.db")
    store.save_doc("drlab", "docs/specs/new.md", "d-new", "c1", "", "")
    store.save_doc("drlab", "docs/chats/vision.md", "d-vision", "c1", "", "")
    store.save_doc("drlab", "docs/old.md", "d-old", "c1", "", "")
    store.put("author:d-new", "sessions-chat")
    store.put("author:d-vision", "sessions-chat")
    inbox = Inbox(cfg(None), FakeOutline(), store)
    assert inbox.route_of("d-new") == "sessions-chat"      # linked by that chat
    assert inbox.route_of("d-vision") == "vision"          # a named route still wins
    assert inbox.route_of("d-old") == "core"               # nobody recorded: the repo's route


def test_a_comment_on_a_picture_names_the_picture(tmp_path):
    """Outline gives a comment on a picture an empty anchor, so margin reads the document's
    structure and names the picture by its caption, else by the heading above it."""
    from margin.inbox import Inbox
    from margin.store import Store

    image = lambda alt, cid: {"type": "image", "attrs": {"alt": alt, "marks": (  # noqa: E731
        [{"type": "comment", "attrs": {"id": cid}}] if cid else [])}}

    class FakeOutline:
        def me(self):
            return {"id": "u-agent"}

        def structure(self, doc_id):
            return {"type": "doc", "content": [
                {"type": "heading", "content": [{"type": "text", "text": "Living room TV"}]},
                {"type": "paragraph", "content": [image("Living room TV", None)]},
                {"type": "heading", "content": [{"type": "text", "text": "Bedroom TV"}]},
                {"type": "paragraph", "content": [image("Bedroom TV", "c-pic")]},
                {"type": "heading", "content": [{"type": "text", "text": "Robot"}]},
                {"type": "paragraph", "content": [image("", "c-noalt")]}]}

    from margin.config import Config
    cfg = Config(url="u", api_key_command=["true"], admin_key_command=None, pin_ip_command=None,
                 listen="", webhook_url="", secret_command=None, poll_seconds=1, quiet_seconds=1,
                 cap_seconds=1, notify_command=None, notify_cwd=None, mcp_url=None,
                 repos=[Repo("drlab", tmp_path, route="core")], state=tmp_path / "s.db",
                 author_command=None)
    inbox = Inbox(cfg, FakeOutline(), Store(tmp_path / "s.db"))
    assert inbox.picture_of("d1", "c-pic") == "the picture Bedroom TV"
    assert inbox.picture_of("d1", "c-noalt") == "the picture Robot"      # no caption: its heading
    assert inbox.picture_of("d1", "c-text") is None                      # not on a picture
