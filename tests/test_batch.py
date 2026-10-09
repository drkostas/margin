from margin.batch import due_in, ready

Q, CAP = 180, 900


def row(route, t, mention=False):
    return {"route": route, "received_at": t, "mentions_agent": mention}


def test_a_burst_waits_for_quiet():
    rows = [row("r", 0), row("r", 30), row("r", 60)]
    assert ready(rows, 200, Q, CAP) == {}
    assert len(ready(rows, 240, Q, CAP)["r"]) == 3


def test_non_stop_commenting_is_released_at_the_cap():
    rows = [row("r", t) for t in range(0, 1000, 60)]
    assert ready([r for r in rows if r["received_at"] <= 840], 899, Q, CAP) == {}
    assert "r" in ready([r for r in rows if r["received_at"] <= 900], 900, Q, CAP)


def test_a_mention_sends_at_once():
    rows = [row("r", 0), row("r", 5, mention=True)]
    assert "r" in ready(rows, 6, Q, CAP)


def test_routes_are_independent():
    rows = [row("a", 0), row("b", 150)]
    assert set(ready(rows, 200, Q, CAP)) == {"a"}


def test_due_in():
    assert due_in([], 0, Q, CAP) is None
    assert due_in([row("r", 100)], 100, Q, CAP) == Q
