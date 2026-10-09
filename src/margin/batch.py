"""When to tell the agent about comments: a settle window, so a burst becomes one message.

A route (one repo's chat) has a batch open from its first undelivered comment. The batch is ready
when the person has been quiet for `quiet` seconds, or `cap` seconds after its first comment so a
long reading session still gets answers, or at once when a comment mentions the agent.

Times are this machine's receive times (seconds since the epoch), never the server's timestamps,
so a clock or timezone difference between the two cannot hold a batch back or release it early.
"""
from __future__ import annotations

from collections import defaultdict


def ready(rows, now: float, quiet: float, cap: float) -> dict[str, list]:
    by_route: dict[str, list] = defaultdict(list)
    for r in rows:
        by_route[r["route"]].append(r)
    out = {}
    for route, rs in by_route.items():
        first = min(r["received_at"] for r in rs)
        last = max(r["received_at"] for r in rs)
        if any(r["mentions_agent"] for r in rs) or now - last >= quiet or now - first >= cap:
            out[route] = sorted(rs, key=lambda r: r["received_at"])
    return out


def due_in(rows, now: float, quiet: float, cap: float) -> float | None:
    """Seconds until the next batch becomes ready, for a status line. None when nothing waits."""
    if not rows:
        return None
    best = None
    by_route: dict[str, list] = defaultdict(list)
    for r in rows:
        by_route[r["route"]].append(r)
    for rs in by_route.values():
        first = min(r["received_at"] for r in rs)
        last = max(r["received_at"] for r in rs)
        t = max(0.0, min(last + quiet, first + cap) - now)
        best = t if best is None else min(best, t)
    return best
