"""Test support for this package's own suite.

One hook, a pinned wall clock. With PIN_CLOCK set to an ISO moment, every test reads that moment as now, so
a check whose answer depends on the time of day gives the same answer on every run. The suite runs twice in
CI, once at the machine's own clock and once pinned to a deliberately awkward moment, because a check that
reads the clock is green or red by when it happened to run, and one green run measures nothing about it.

The case that asked for this is in tests/test_pinch_wait.py. An assertion there read "13" in a line that
opens with the local time, so it was red for 83 of the 1440 minutes of a day, being hour 13 entire and
minute 13 of every other hour, and it was green in CI only because of the hour the run started.
"""
from __future__ import annotations

import os

import pytest

#: an ISO moment every test reads as now, for example 2024-02-29T13:13:13, or empty for the real clock
PIN_CLOCK = os.environ.get("PIN_CLOCK", "").strip()


@pytest.fixture(autouse=True)
def pinned_clock():
    """Read PIN_CLOCK as now for the whole test, or leave the clock alone when it is unset."""
    if not PIN_CLOCK:
        yield
        return
    from freezegun import freeze_time

    with freeze_time(PIN_CLOCK):
        yield
