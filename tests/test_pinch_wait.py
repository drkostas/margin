"""The wait before a pinch test: which condition held it back, in words, and how often it looks.

Every case here is the one that spent a reserved ten minutes on 24 Nov 2026. The phone was awake and
unlocked both times and the refusal said "the phone is locked, asleep or off adb", so the words were
read as a lock and the real cause, a 13.7 s idle against a 60 s quiet window, took a reading of this
module to find.
"""
from __future__ import annotations

from margin import pinch_check as P

AWAKE = "  mWakefulness=Awake\n  lastUserActivityTime=191390214 (882 ms ago)\n"
ASLEEP = "  mWakefulness=Asleep\n"


def test_why_not_ready_names_the_condition_that_failed():
    assert P.why_not_ready(AWAKE, False, 90.0) is None
    assert P.ready(AWAKE, False, 90.0)

    asleep = P.why_not_ready(ASLEEP, False, 90.0)
    assert asleep is not None and "asleep or off adb" in asleep and "mWakefulness=Awake" in asleep

    locked = P.why_not_ready(AWAKE, True, 90.0)
    assert locked is not None and "lock screen is up" in locked and "never unlocks it" in locked

    unread = P.why_not_ready(AWAKE, False, None)
    assert unread is not None and "last input cannot be read" in unread

    # the case of 24 Nov 2026, with both numbers in the sentence
    touched = P.why_not_ready(AWAKE, False, 13.675)
    assert touched is not None
    assert "13.7 s ago" in touched and "60 s of quiet" in touched
    # ⛔ by the phrase, never by the word: the sentence says "awake and unlocked", and "unlocked"
    # holds "lock", which is the substring trap that has cost this estate several wrong counts
    assert "lock screen is up" not in touched and "asleep" not in touched


def test_a_lock_is_not_reported_as_a_quiet_window_and_the_other_way_round():
    """Each sentence names only its own condition, since the fault was one sentence for all three."""
    assert "quiet" not in (P.why_not_ready(AWAKE, True, 90.0) or "")
    assert "lock screen is up" not in (P.why_not_ready(AWAKE, False, 1.0) or "")
    assert "quiet" not in (P.why_not_ready(ASLEEP, False, 90.0) or "")


def _looker(readings):
    it = iter(readings)
    last = readings[-1]

    def look():
        nonlocal last
        try:
            last = next(it)
        except StopIteration:
            pass
        return last
    return look


def _clock(step: float):
    now = [0.0]

    def clock():
        return now[0]

    def sleep(s: float):
        now[0] += s
    return clock, sleep


def test_wait_ready_returns_none_once_the_phone_is_quiet():
    clock, sleep = _clock(1.0)
    said: list[str] = []
    why = P.wait_ready(_looker([(AWAKE, False, 10.0), (AWAKE, False, 40.0), (AWAKE, False, 70.0)]),
                       budget=600.0, poll=5.0, say=said.append, sleep=sleep, clock=clock)
    assert why is None
    assert said and "13" not in said[0]           # it says the reading it saw, not a fixed sentence
    assert "10.0 s ago" in said[0]


def test_the_refusal_carries_the_last_reading_rather_than_a_fixed_sentence():
    clock, sleep = _clock(1.0)
    why = P.wait_ready(_looker([(AWAKE, False, 13.675)]), budget=120.0, poll=5.0,
                       say=lambda t: None, sleep=sleep, clock=clock)
    assert why is not None and "13.7 s ago" in why and "60 s of quiet" in why


def test_it_looks_more_often_than_the_quiet_window_it_waits_for():
    """A look every 30 s against a 60 s quiet window can miss the window by half a minute."""
    assert P.POLL < P.QUIET / 2
    clock, sleep = _clock(1.0)
    looks: list[float] = []

    def look():
        looks.append(clock())
        return AWAKE, False, 1.0
    P.wait_ready(look, budget=60.0, poll=P.POLL, say=lambda t: None, sleep=sleep, clock=clock)
    assert len(looks) >= 12                        # 60 s of budget at 5 s a look
    assert looks[1] - looks[0] == P.POLL


def test_a_budget_of_zero_still_takes_one_look():
    clock, sleep = _clock(1.0)
    assert P.wait_ready(_looker([(AWAKE, False, 90.0)]), budget=0.0, say=lambda t: None,
                        sleep=sleep, clock=clock) is None
    why = P.wait_ready(_looker([(ASLEEP, False, 90.0)]), budget=0.0, say=lambda t: None,
                       sleep=sleep, clock=clock)
    assert why is not None and "asleep" in why


def test_the_waiting_line_is_said_again_while_it_waits_but_not_every_look():
    clock, sleep = _clock(1.0)
    said: list[str] = []
    P.wait_ready(_looker([(AWAKE, False, 2.0)]), budget=300.0, poll=P.POLL, say=said.append,
                 sleep=sleep, clock=clock)
    assert 2 <= len(said) <= 300 / P.SAY_EVERY + 1
