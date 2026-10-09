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
