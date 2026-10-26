"""pinch_check.judge: a pinch on a fold page passes only when all four limits hold."""
from margin.pinch_check import Page, expected, judge, plan, ready, table


def reading(**kw):
    base = {"before": 2, "level": 3, "y": 400.0, "anchor": {"id": "a1", "top": 380.0, "height": 100.0, "frac": 0.2},
            "after": {"top": 390.0, "height": 50.0}, "scale": 1.0, "max_scale": 1.0, "max_frame_ms": 18.0}
    base.update(kw)
    return base


def test_one_level_in_place_no_zoom_smooth_passes():
    # the point at 20% of the paragraph: 390 + 0.2 * 50 = 400, where the fingers were
    assert judge(reading(), "in") == []


def test_the_level_must_move_by_exactly_one_in_the_pinchs_direction():
    assert judge(reading(level=4), "in") == ["level 2 went to 4, not 3"]
    assert judge(reading(level=2, after=None), "in") == ["level 2 went to 2, not 3"]
    assert judge(reading(level=3), "out")[0] == "level 2 went to 3, not 1"


def test_at_the_end_of_the_scale_nothing_changing_is_right():
    assert expected(1, "out") == 1 and expected(5, "in") == 5 and expected(3, "in") == 4
    assert judge(reading(before=1, level=1, anchor=None, after=None), "out") == []
    assert judge(reading(before=5, level=5, anchor=None, after=None), "in") == []


def test_the_paragraph_under_the_fingers_must_stay_within_24_px():
    assert judge(reading(after={"top": 413.0, "height": 50.0}), "in") == []          # 23 px
    assert judge(reading(after={"top": 430.0, "height": 50.0}), "in") == ["the paragraph under the fingers moved 40 px"]
    assert judge(reading(after=None), "in") == ["the paragraph under the fingers could not be found after the change"]


def test_the_browser_must_not_zoom_even_for_a_moment():
    assert judge(reading(max_scale=1.4), "in") == ["the browser zoomed to 1.40"]
    assert judge(reading(scale=1.2, max_scale=1.2), "in") == ["the browser zoomed to 1.20"]


def test_no_frame_over_50_ms():
    assert judge(reading(max_frame_ms=50.0), "in") == []
    assert judge(reading(max_frame_ms=120.0), "in") == ["a frame took 120 ms"]
    assert judge(reading(max_frame_ms=None), "in") == ["no frame was drawn while it changed"]


def test_no_reading_is_a_failure():
    assert judge(None, "in") == ["the page reported no two-finger gesture"]


def test_the_plan_checks_the_bound_then_three_places_quick_and_slow():
    p = plan()
    assert p[0] == (0.1, "out", False) and len(p) == 19
    assert {place for place, _d, _s in p} == {0.1, 0.5, 0.85}
    assert sum(1 for _p, _d, slow in p if slow) == 6


def test_the_table_has_a_row_a_pinch_with_its_result():
    rows = [{"place": 0.1, "direction": "in", "slow": False, "reading": reading(), "why": []},
            {"place": 0.5, "direction": "out", "slow": True, "reading": None, "why": ["not pinched: the phone is locked"]}]
    t = table(rows).splitlines()
    assert len(t) == 4 and "| 1 | 10% | in | quick | 2 to 3 | 0 | 1.00 | 18 | pass |" == t[2]
    assert t[3].endswith("FAIL not pinched: the phone is locked |")


def test_the_test_starts_only_on_an_awake_unlocked_phone():
    assert ready("  mWakefulness=Awake\n", False)
    assert not ready("  mWakefulness=Awake\n", True)
    assert not ready("  mWakefulness=Dozing\n", False) and not ready("", False)


class FakeBrowser:
    """claude_human.android_browser as Page uses it: the first port stops answering after one call."""

    class BrowserError(RuntimeError):
        pass

    def __init__(self):
        self.ports, self.calls, self.removed = iter([5001, 5002]), [], []

    def forward(self, serial, package, run):
        return next(self.ports)

    def unforward(self, serial, port, run):
        self.removed.append(port)

    def evaluate(self, port, url, js):
        self.calls.append((port, js))
        if port == 5001 and len(self.calls) > 1:
            raise self.BrowserError("the browser did not answer /json/list: connection refused")
        if js == "throw":
            raise self.BrowserError("the script threw: ReferenceError")
        return js


def test_a_port_that_stops_answering_is_forwarded_again_once():
    ab = FakeBrowser()
    page = Page(ab, "dev", "com.example.browser", "http://127.0.0.1:1/p.html", run_adb=None)
    assert page.eval("a") == "a" and page.eval("b") == "b"
    assert ab.removed == [5001] and page.port == 5002 and ab.calls[-1] == (5002, "b")


def test_a_script_error_is_not_retried():
    ab = FakeBrowser()
    page = Page(ab, "dev", "com.example.browser", "http://127.0.0.1:1/p.html", run_adb=None)
    page.port = 5002
    try:
        page.eval("throw")
    except ab.BrowserError as e:
        assert "threw" in str(e) and ab.removed == []
    else:
        raise AssertionError("a script error was swallowed")
