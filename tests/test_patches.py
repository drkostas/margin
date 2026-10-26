import random

import pytest

from margin.patches import CannotPatch, apply, plan

DOC = "## Alpha\n\nThe cache holds entries for ten minutes.\n\n## Beta\n\n* one\n* two\n\n## Gamma\n\nLast."


def test_no_change_is_no_patch():
    assert plan(DOC, DOC) == []


def test_one_line_change_patches_only_that_line():
    target = DOC.replace("Last.", "Last, edited.")
    p = plan(DOC, target)
    assert p == [("Last", "Last, edited")]       # only the words that changed, unique as they are
    assert apply(DOC, p) == target


QUIET = ("**Quiet nights.** Between 23:00 and 08:00 (you can change the hours in the app › Settings) "
         "nothing is pushed except a critical problem. An approval asked at night waits.")


def test_a_comment_on_unchanged_words_of_a_changed_paragraph_keeps_its_anchor():
    """2026-10-13: removing a bracket after "Between 23:00 and 08:00" replaced the whole paragraph,
    and the comment anchored on those words lost its highlight. Only the changed words are patched
    now, so the anchored words are in no find."""
    doc = "## Notifications\n\n" + QUIET + "\n\n## Next\n\nText."
    target = doc.replace(" (you can change the hours in the app › Settings)", "")
    p = plan(doc, target)
    assert apply(doc, p) == target
    assert p and all("23:00 and 08:00" not in find for find, _ in p), p
    assert all("nothing is pushed" not in find for find, _ in p), p


def test_several_changes_in_one_paragraph_each_patch_their_own_words():
    doc = "Intro.\n\nThe cache holds entries for ten minutes and the queue keeps them for an hour.\n\nEnd."
    target = "Intro.\n\nThe store holds entries for ten minutes and the queue keeps them for a day.\n\nEnd."
    p = plan(doc, target)
    assert apply(doc, p) == target
    assert len(p) == 2 and all("ten minutes" not in f for f, _ in p), p


def test_a_changed_word_found_elsewhere_takes_context_until_unique():
    doc = "the cat sat on the mat.\n\nthe dog sat on the mat."
    target = "the cat sat on the mat.\n\nthe dog lay on the mat."
    p = plan(doc, target)
    assert apply(doc, p) == target
    find = p[0][0]
    assert doc.count(find) == 1 and "dog" in find and "\n" not in find, p


def test_random_word_edits_inside_lines_reproduce_the_target():
    rnd = random.Random(11)
    vocab = ["the", "cache", "holds", "ten", "minutes", "a", "queue", "(", ")", "›", "same", "same"]
    for _ in range(400):
        lines = [" ".join(rnd.choice(vocab) for _ in range(rnd.randint(1, 9))) for _ in range(rnd.randint(1, 6))]
        new = lines[:]
        for _ in range(rnd.randint(1, 3)):
            li = rnd.randrange(len(new))
            w = new[li].split(" ")
            op, pos = rnd.choice("ird"), rnd.randrange(len(w))
            if op == "i":
                w.insert(pos, rnd.choice(vocab) + str(rnd.randint(0, 9)))
            elif op == "r":
                w[pos] = "changed" + str(rnd.randint(0, 99))
            elif len(w) > 1:
                w.pop(pos)
            new[li] = " ".join(w)
        cur, tgt = "\n".join(lines), "\n".join(new)
        try:
            p = plan(cur, tgt)
        except CannotPatch:
            continue
        assert apply(cur, p) == tgt


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


def test_outlines_two_spellings_of_escaped_asterisks_and_line_breaks_are_alike():
    # 2026-10-16: saved whole Outline writes `\**` and plain newlines, saved through a patch `\*\*`
    # and a backslash at each line's end; fifteen documents never matched their file for it
    from margin.patches import spelled_alike
    assert spelled_alike("a \\*\\* b\nstop\\\nnext", "a \\** b\nstop\nnext")
    assert spelled_alike("\\*\\*\\*", "\\***")


def test_only_those_spellings_are_alike():
    from margin.patches import spelled_alike
    assert not spelled_alike("\\*a\\*", "*a*")              # literal asterisks against italics
    assert not spelled_alike("a \\** b", "a ** b")
    assert not spelled_alike("x\\\\", "x")                  # an escaped backslash is not a line break
    assert not spelled_alike("one", "two")


def test_adopt_keeps_outlines_spelling_of_lines_that_are_alike():
    from margin.patches import adopt
    current = "Price \\*\\* 3\nstop\\\nthe old line"
    target = "Price \\** 3\nstop\nthe new line"
    assert adopt(current, target) == "Price \\*\\* 3\nstop\\\nthe new line"


def test_a_word_patch_never_holds_one_half_of_a_marker():
    # 2026-10-16: a patch "**Sweep" -> "**Sweep (gone" made Outline escape the lone `**`, and the
    # document read `\**Sweep ...:\**`, the bold gone; such a change patches its whole line
    from margin.patches import _cuts_markup, apply, plan
    cur = "Intro.\n\n**Sweep:** runs every flow.\n"
    tgt = "Intro.\n\n**Sweep (gone):** runs every flow.\n"
    patches = plan(cur, tgt)
    assert apply(cur, patches) == tgt
    assert not any(_cuts_markup(f) or _cuts_markup(r) for f, r in patches), patches
    assert _cuts_markup("**Sweep") and _cuts_markup("a `b") and not _cuts_markup("**a** and `b`")
    assert not _cuts_markup("\\** literal")              # an escaped marker is not a marker


def test_the_spaces_before_an_inline_picture_are_outlines_spelling():
    # 2026-10-16: through a patch Outline writes `[  ![` and three spaces between two pictures
    from margin.patches import spelled_alike
    assert spelled_alike("[ ![badge](/a)](https://x) and ![b](/b)", "[  ![badge](/a)](https://x) and   ![b](/b)")
    assert not spelled_alike("    ![a](/a)", " ![a](/a)")          # indentation at a line's start counts


def test_a_change_to_a_table_patches_the_whole_table_across_its_blank_lines():
    # 2026-10-16: Outline took no patch that began inside a table, and rows added at its end came
    # back as a paragraph after it with the second row lost
    from margin.patches import apply, plan
    cur = "Intro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Next\n\nText."
    tgt = "Intro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n\n## Next\n\nText."
    patches = plan(cur, tgt)
    assert apply(cur, patches) == tgt
    [(find, repl)] = patches
    assert find.startswith("\n| a | b |") and find.endswith("| 1 | 2 |\n"), find
    word = plan("Intro.\n\n| a | b |\n|---|---|\n| 1 | old |\n", "Intro.\n\n| a | b |\n|---|---|\n| 1 | new |\n")
    assert all("| a | b |" in f for f, _ in word), word        # a changed cell too, never one word alone
