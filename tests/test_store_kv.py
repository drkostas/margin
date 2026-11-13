"""The key-value table stays small and is read by its key index."""
from margin import sync
from margin.store import Store


def test_prefix_read_uses_the_key_index(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(50):
        s.put(f"canon:{i}", "x")
    s.put("pin:r:a.md", "/w/a.md")
    s.put("pin:r:b.md", "/w/b.md")
    s.put("pin:s:c.md", "/w/c.md")
    assert [r["k"] for r in s.with_prefix("pin:r:")] == ["pin:r:a.md", "pin:r:b.md"]
    plan = s.db.execute("EXPLAIN QUERY PLAN SELECT k, v FROM kv WHERE k >= ? AND k < ?",
                        ("pin:r:", "pin:r;")).fetchall()
    assert "SCAN" not in " ".join(str(tuple(r)) for r in plan).replace("SEARCH", "")
    assert "SEARCH" in " ".join(str(tuple(r)) for r in plan)


def test_trim_keeps_the_newest_rows_of_one_prefix(tmp_path):
    s = Store(tmp_path / "m.db")
    for i in range(10):
        s.put(f"canon:{i}", "x")
    s.put("pin:r:a.md", "/w/a.md")
    assert s.trim("canon:", 3) == 7
    assert [r["k"] for r in s.with_prefix("canon:")] == ["canon:7", "canon:8", "canon:9"]
    assert s.get("pin:r:a.md") == "/w/a.md"


def test_canonical_texts_are_capped(tmp_path, monkeypatch):
    from test_sync import FakeOutline
    monkeypatch.setattr(sync, "CANON_KEEP", 4)
    s = Store(tmp_path / "m.db")
    syn = sync.Syncer(FakeOutline(), s, type("R", (), {"name": "r"})())
    for i in range(12):
        syn.canonical(f"text {i}")
    assert len(s.with_prefix("canon:")) == 4
