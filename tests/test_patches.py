import random

import pytest

from margin.patches import CannotPatch, apply, plan

DOC = "## Alpha\n\nThe cache holds entries for ten minutes.\n\n## Beta\n\n* one\n* two\n\n## Gamma\n\nLast."


def test_no_change_is_no_patch():
    assert plan(DOC, DOC) == []


def test_one_line_change_patches_only_that_line():
    target = DOC.replace("Last.", "Last, edited.")
    p = plan(DOC, target)
    assert p == [("Last.", "Last, edited.")]
    assert apply(DOC, p) == target


def test_untouched_sections_are_never_inside_a_patch():
    target = DOC.replace("* two", "* two\n* three")
    p = plan(DOC, target)
    assert apply(DOC, p) == target
    for find, _ in p:
        assert "ten minutes" not in find      # the commented sentence is left alone


def test_duplicate_line_gets_context_so_the_right_one_changes():
    doc = "a\n\nsame\n\nb\n\nsame\n\nc"
    target = "a\n\nsame\n\nb\n\nchanged\n\nc"
    p = plan(doc, target)
    assert apply(doc, p) == target
    find = p[0][0]
    assert doc.count(find) == 1


def test_insert_and_delete():
    target = DOC.replace("## Beta\n\n* one\n* two\n\n", "") + "\n\n## Delta\n\nNew."
    assert apply(DOC, plan(DOC, target)) == target


def test_apply_refuses_missing_text():
    with pytest.raises(CannotPatch):
        apply("abc", [("zzz", "y")])


def test_random_edits_always_reproduce_the_target():
    rnd = random.Random(7)
    words = ["alpha", "beta", "", "## Head", "* item", "same", "same", "text line"]
    for _ in range(400):
        a = [rnd.choice(words) for _ in range(rnd.randint(1, 14))]
        b = a[:]
        for _ in range(rnd.randint(1, 4)):
            op = rnd.choice("ird")
            pos = rnd.randint(0, len(b))
            if op == "i":
                b.insert(pos, rnd.choice(words) + str(rnd.randint(0, 9)))
            elif b and op == "r":
                b[min(pos, len(b) - 1)] = "changed" + str(rnd.randint(0, 99))
            elif len(b) > 1:
                b.pop(min(pos, len(b) - 1))
        cur, tgt = "\n".join(a), "\n".join(b)
        try:
            p = plan(cur, tgt)
        except CannotPatch:
            continue          # allowed to refuse; never allowed to produce a wrong document
        assert apply(cur, p) == tgt


def test_widen_makes_each_patch_cross_a_block_boundary():
    doc = "## A\n\n* one\n* two\n\n## B\n\nend"
    target = "## A\n\n* one\n* two\n\nNew paragraph.\n\n## B\n\nend"
    for w in (1, 2):
        p = plan(doc, target, widen=w)
        assert apply(doc, p) == target
        assert all(sum(1 for x in f.split("\n") if not x.strip()) >= w for f, _ in p)
