"""Turn "the document is X, it should be Y" into the smallest patches Outline can apply.

Outline's patch edit replaces the FIRST occurrence of a piece of markdown and keeps every comment
anchored outside it. A comment anchored inside the replaced span loses its anchor (the thread stays,
pointing at nothing). So the patches must be as small as possible, and each one must be unique in
the document at the moment it is applied, or it lands on the wrong occurrence.

The planner works on lines. Hunks are applied bottom-up, so everything below a hunk already
matches the target and can always serve as context.

⭐ A CHANGED LINE IS PATCHED WORD BY WORD (2026-10-13). A paragraph is one line, so a line-sized
patch replaced a whole paragraph, and a comment anchored on words the edit had kept lost its
highlight (the anchor "Between 23:00 and 08:00" went when only a bracket after it was removed).
Outline patches a match inside one paragraph in place and keeps every mark outside the match, so
a line that changed into one other line is patched as the runs of words that changed, each with
just enough words around it to be unique. Only when that cannot be done does the whole line go. Context above a hunk is taken only from the
unchanged run directly above it. `apply` replays a plan locally, and `plan` checks its own result
with it before returning, so a plan that would not produce the target is never returned.
"""
from __future__ import annotations

import re
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


_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _offsets(tokens: list[str]) -> list[int]:
    out = [0]
    for t in tokens:
        out.append(out[-1] + len(t))
    return out


def _words(lines: list[str], i: int, new: str) -> list[Patch] | None:
    """Patches that turn line `i` of `lines` into `new`, touching only the words that changed. Each
    is unique in the whole document when it is applied, and they go right to left so the rest of
    the line still reads as it did. None when a change cannot be made unique inside its line.
    `lines` is updated as the patches are planned."""
    old_t, new_t = _TOKEN.findall(lines[i]), _TOKEN.findall(new)
    if not old_t or not new_t or "".join(old_t) != lines[i]:
        return None
    out: list[Patch] = []
    ops: list[list] = []
    for op in SequenceMatcher(None, old_t, new_t, autojunk=False).get_opcodes():
        # two changes with only spaces between them are one change ("an hour" to "a day")
        if (len(ops) >= 2 and op[0] != "equal" and ops[-1][0] == "equal" and ops[-2][0] != "equal"
                and not "".join(old_t[ops[-1][1]:ops[-1][2]]).strip()):
            ops.pop()
            ops[-1] = ["replace", ops[-1][1], op[2], ops[-1][3], op[4]]
        else:
            ops.append(list(op))
    for tag, s1, s2, t1, t2 in reversed(ops):
        if tag == "equal":
            continue
        at = _offsets(old_t)
        left, right = s1, s2              # the span of old tokens the patch covers
        while True:
            find = lines[i][at[left]:at[right]]
            if find.strip() and _only_at_col(lines, i, at[left], find):
                break
            # one more word of context, on the side with more room, never past the line
            if left > 0 and (right >= len(old_t) or s1 - left <= right - s2):
                left -= 1
            elif right < len(old_t):
                right += 1
            else:
                return None
        repl = "".join(old_t[left:s1]) + "".join(new_t[t1:t2]) + "".join(old_t[s2:right])
        lines[i] = lines[i][:at[left]] + repl + lines[i][at[right]:]
        out.append((find, repl))
        old_t = old_t[:s1] + new_t[t1:t2] + old_t[s2:]   # right of here now reads as the target
    return out if lines[i] == new else None


def _only_at_col(lines: list[str], i: int, col: int, find: str) -> bool:
    """`find` occurs exactly once in the joined lines, starting at column `col` of line `i`."""
    joined = "\n".join(lines)
    at = sum(len(x) + 1 for x in lines[:i]) + col
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
        if widen == 0 and tag == "replace" and i2 - i1 == j2 - j1:
            trial, fine = work[:], []
            for li in range(i2 - 1, i1 - 1, -1):        # bottom-up, as the hunks are
                if trial[li] == b[j1 + li - i1]:
                    continue
                got = _words(trial, li, b[j1 + li - i1])
                if got is None:
                    fine = None
                    break
                fine += got
            if fine is not None:
                work = trial
                patches += fine
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
