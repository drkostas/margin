"""Turn "the document is X, it should be Y" into the smallest patches Outline can apply.

Outline's patch edit replaces the FIRST occurrence of a piece of markdown and keeps every comment
anchored outside it. A comment anchored inside the replaced span loses its anchor (the thread stays,
pointing at nothing). So the patches must be as small as possible, and each one must be unique in
the document at the moment it is applied, or it lands on the wrong occurrence.

The planner works on lines. Hunks are applied bottom-up, so everything below a hunk already
matches the target and can always serve as context. Context above a hunk is taken only from the
unchanged run directly above it. `apply` replays a plan locally, and `plan` checks its own result
with it before returning, so a plan that would not produce the target is never returned.
"""
from __future__ import annotations

from difflib import SequenceMatcher

Patch = tuple[str, str]   # (find, replace)


class CannotPatch(Exception):
    """No sequence of unique patches reaches the target. The caller must not fall back to a full
    replace on a document that may carry comments; it reports the document instead."""


def apply(text: str, patches: list[Patch]) -> str:
    for find, repl in patches:
        if find not in text:
            raise CannotPatch(f"find text not present: {find[:60]!r}")
        text = text.replace(find, repl, 1)
    return text


def _only_at(lines: list[str], lo: int, find: str) -> bool:
    """`find` occurs exactly once in the joined lines, starting at line `lo`. Overlapping matches
    count, because Outline replaces the first occurrence wherever it starts."""
    joined = "\n".join(lines)
    at = sum(len(x) + 1 for x in lines[:lo])
    return joined.find(find) == at and joined.find(find, at + 1) == -1


def plan(current: str, target: str, widen: int = 0) -> list[Patch]:
    """Patches from `current` to `target`. `widen` > 0 makes every patch also cross that many
    blank lines (block boundaries). Outline patches a match inside ONE top-level block by
    re-parsing just that block, and drops anything the replacement adds outside it; a match that
    spans two blocks takes its region path instead, which keeps everything. The syncer widens
    only after it sees a patch come back different from what it expected."""
    if current == target:
        return []
    a, b = current.split("\n"), target.split("\n")
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    work = a[:]
    patches: list[Patch] = []
    k = len(ops) - 1
    while k >= 0:
        tag, i1, i2, j1, j2 = ops[k]
        if tag == "equal":
            k -= 1
            continue
        lo, hi, blo, bhi = i1, i2, j1, j2
        while True:
            # how far up we may borrow context: the unchanged run directly above the hunk
            up_limit = ops[k - 1][1] if k > 0 and ops[k - 1][0] == "equal" else lo
            find = "\n".join(work[lo:hi])
            repl = "\n".join(b[blo:bhi])
            if (find.strip() and (repl or bhi > blo) and _only_at(work, lo, find)
                    and sum(1 for x in work[lo:hi] if not x.strip()) >= widen):
                break
            if hi < len(work):            # below the hunk already matches the target
                hi, bhi = hi + 1, bhi + 1
            elif lo > up_limit:
                lo, blo = lo - 1, blo - 1
            elif k >= 2:                  # swallow the run above and the hunk above it
                k -= 2
                lo, blo = ops[k][1], ops[k][3]
            else:
                raise CannotPatch("no unique context around a change")
        work[lo:hi] = b[blo:bhi]
        patches.append((find, repl))
        k -= 1
    if apply(current, patches) != target:
        raise CannotPatch("plan does not reproduce the target")
    return patches
