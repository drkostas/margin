"""The fold: parsing, the checks against planted faults, and the retry and fallback with a fake model."""
from __future__ import annotations

import json

from margin import fold as F

DOC = """---
title: ignored front matter
---
# Backups on the Mac

Every night at 03:30 the backup job dumps 14 databases to pi1. It keeps fourteen nights.

## Why it failed

The job did not run on 2026-10-12 because the disk was full. Nothing alerted, so nobody knew for
two days. The `db-backup.sh` script now refuses to start below 10% free disk.

- A dump smaller than half of the night before is flagged.
- Old dumps are pruned after 14 nights.

| set | size |
|---|---|
| drlab | 3.2 GB |

```sh
launchctl kickstart gui/501/dev.gkos.db-backup
```
"""


def unit(text):
    return F.Unit("s.p0", "paragraph", text, F.split_sentences(text))


GOOD_UNIT = unit("The job did not run on 2026-10-12 because the disk was full. Nothing alerted, so "
                 "nobody knew for two days. The `db-backup.sh` script now refuses to start below 10% "
                 "free disk.")
GOOD = {"id": "s.p0", "evidence": [0, 2],
        "point": "The job did not run on 2026-10-12 because the disk was full.",
        "reason": "The `db-backup.sh` script now refuses to start below 10% free disk.",
        "topic": "disk was full"}


# ---- parsing --------------------------------------------------------------------------------
def test_parse_sections_units_and_context():
    d = F.parse(DOC)
    assert d.title == "Backups on the Mac"
    assert [s.id for s in d.sections] == ["intro", "why-it-failed"]
    why = d.sections[1]
    assert [u.kind for u in why.units] == ["paragraph", "item", "item"]
    assert why.units[0].sentences[1] == "Nothing alerted, so nobody knew for two days."
    assert len(why.blocks) == 2 and "3.2 GB" in why.blocks[0] and "kickstart" in why.blocks[1]


def test_sentences_are_the_authors_text():
    t = 'He said "stop." Then 1.10.1 shipped, e.g. today. See `a.b. C` and [x](http://a.b/c. D).'
    got = F.split_sentences(t)
    assert got == ['He said "stop."', "Then 1.10.1 shipped, e.g. today.",
                   "See `a.b. C` and [x](http://a.b/c. D)."]
    assert all(s in t for s in got)


def test_a_long_paragraph_is_folded_in_parts():
    long = " ".join(f"Sentence number {k} is here." for k in range(15))
    d = F.parse(f"# T\n\n## S\n\n{long}\n")
    assert [u.id for u in d.sections[0].units] == ["s.p0~0", "s.p0~1", "s.p0~2"]


# ---- the checks, against faults planted in a good answer --------------------------------------
def test_a_good_answer_passes():
    assert F.check_unit(GOOD_UNIT, GOOD) == []


def planted(**change):
    return F.check_unit(GOOD_UNIT, {**GOOD, **change})


def test_a_changed_number_is_caught():
    assert any("2026-10-13" in p for p in planted(point="The job did not run on 2026-10-13 because the disk was full."))
    assert any("15%" in p for p in planted(reason="The `db-backup.sh` script now refuses to start below 15% free disk."))


def test_a_dropped_not_is_caught():
    assert any("drops it" in p for p in planted(point="The job ran on 2026-10-12 because the disk was full."))


def test_an_added_not_is_caught():
    u = unit("The backup ran every night in October.")
    a = {"id": "s.p0", "evidence": [0], "point": "The backup never ran in October.", "reason": "",
         "topic": "backup"}
    assert any("negate" in p for p in F.check_unit(u, a))


def test_a_swapped_name_is_caught():
    assert any("pi2" in p for p in planted(point="The job did not run on 2026-10-12 because pi2 was full."))


def test_a_code_word_must_be_exact():
    assert any("db_backup.sh" in p for p in planted(
        reason="The `db_backup.sh` script now refuses to start below 10% free disk."))


def test_a_line_from_another_paragraph_is_caught():
    probs = planted(point="Tickets for the concert sold out within minutes yesterday evening.")
    assert any("paragraph's own" in p for p in probs)


def test_evidence_out_of_range_or_missing_is_caught():
    assert planted(evidence=[7])[0].startswith("evidence names sentence 7")
    assert planted(evidence=[])[0] == "evidence names no sentences"
    assert planted(evidence=["0"])[0] == "evidence names no sentences"


def test_lengths_are_kept():
    long = ("The job did not run on 2026-10-12 because the disk was full and the job did not run "
            "because the disk was full.")
    assert any(f"more than {F.POINT_LIMIT}" in p for p in planted(point=long))
    assert any(f"more than {F.TOPIC_LIMIT}" in p for p in planted(topic="disk was full on the night"))
    assert not any("more than" in p for p in planted(
        point="The job did not run on 2026-10-12 because the disk was full, nobody knew."))


def test_a_fact_found_only_elsewhere_in_the_paragraph_is_named_as_such():
    probs = planted(evidence=[0], point="The job did not run on 2026-10-12 because `db-backup.sh` was full.")
    assert any("it is in the paragraph" in p for p in probs)


# ---- the flow, with a fake model ----------------------------------------------------------------
def fake(answers):
    """Answers each call with the next item of `answers` (a function of the prompt or a dict)."""
    calls = []

    def ask(prompt):
        calls.append(prompt)
        a = answers[len(calls) - 1]
        return a(prompt) if callable(a) else a
    return ask, calls


def stated(prompt):
    """A judge that finds every line stated, and names a real sentence for it."""
    ids = [line.split(">")[0][1:] for line in prompt.splitlines() if line.startswith("<")]
    unit = next((line.split("]")[0][1:] for line in prompt.splitlines() if line.startswith("[")), "")
    return {"lines": [{"id": i, "stated": True, "problem": "",
                       "support": [f"{unit}:0"] if i.startswith("section/") else [f"{i.split('/')[0]}:0"]}
                      for i in ids]}


def refusing(field):
    """A judge that refuses every line of one field, the first time it is asked."""
    seen = []

    def judge(prompt):
        seen.append(prompt)
        ids = [line.split(">")[0][1:] for line in prompt.splitlines() if line.startswith("<")]
        return {"lines": [{**x, "stated": not (x["id"].endswith("/" + field) and len(seen) == 1),
                           "problem": "the sentences do not say so"} for x in stated(prompt)["lines"]]}
    return judge, seen


def section_doc():
    return F.parse("# T\n\n## Why it failed\n\n" + GOOD_UNIT.text + "\n")


def test_a_good_fold_is_claudes_and_is_cached(tmp_path):
    d = section_doc()
    sec = d.sections[0]
    good = {"units": [{**GOOD, "id": sec.units[0].id}],
            "section": {"takeaway": "The job did not run on 2026-10-12 because the disk was full.",
                        "topic": "disk was full", "rests_on": [sec.units[0].id]}}
    ask, calls = fake([good])
    out = F.fold_section(d, sec, ask, cache=tmp_path, judge=stated)
    u = out["units"][0]
    assert u["source"] == "claude" and u["evidence"] == [0, 2] and out["section"]["source"] == "claude"
    assert u["key"].startswith("The job did not run") and len(calls) == 1 and out["calls"] == 2
    again = F.fold_section(d, sec, ask, cache=tmp_path, judge=stated)
    assert again["cached"] and len(calls) == 1


def test_a_refused_line_is_asked_again_with_the_reason():
    d = section_doc()
    uid = d.sections[0].units[0].id
    wrong = {"units": [{**GOOD, "id": uid, "point": "The job did not run on 2026-10-13 because the disk was full."}],
             "section": {"takeaway": "x", "topic": "x", "rests_on": [uid]}}
    right = {"units": [{**GOOD, "id": uid}], "section": {}}
    ask, calls = fake([wrong, right])
    out = F.fold_section(d, d.sections[0], ask, judge=stated)
    assert len(calls) == 2 and "2026-10-13" in calls[1] and "refused" in calls[1]
    assert out["units"][0]["source"] == "claude"


def test_two_refusals_leave_the_authors_words_marked():
    d = section_doc()
    uid = d.sections[0].units[0].id
    wrong = {"units": [{**GOOD, "id": uid, "point": "The job ran on 2026-10-12 because pi2 was full."}],
             "section": {"takeaway": "x", "topic": "x", "rests_on": [uid]}}
    ask, calls = fake([wrong, wrong])
    out = F.fold_section(d, d.sections[0], ask, judge=stated)
    u = out["units"][0]
    assert len(calls) == 2 and u["source"] == "author" and u["refused"]
    assert u["takeaway"] == GOOD_UNIT.sentences[0]
    assert u["key"] == GOOD_UNIT.sentences[0] + " " + GOOD_UNIT.sentences[2]


def test_a_failed_call_never_becomes_claudes_words():
    d = section_doc()

    def boom(prompt):
        raise RuntimeError("no network")
    out = F.fold_section(d, d.sections[0], boom, judge=stated)
    assert out["units"][0]["source"] == "author" and "the call failed" in out["units"][0]["refused"][0]
    assert out["section"]["source"] == "author"


def test_the_whole_document_and_its_page():
    def answer(prompt):
        ids = [line.split("]")[0][1:] for line in prompt.splitlines() if line.startswith("[")]
        return {"units": [{"id": i, "evidence": [0], "point": "", "reason": "", "topic": ""} for i in ids],
                "section": {}}
    folded = F.fold(DOC, ask=answer, jobs=1, judge=stated)
    assert folded["format"] == "margin-fold" and folded["counts"]["units"] == 4
    assert folded["counts"]["author"] == 4          # empty lines are refused, never shown as Claude's
    page = F.page(folded)
    assert "Backups on the Mac" in page and 'data-l="5"' in page
    json.dumps(folded)


# ---- the judge ---------------------------------------------------------------------------------
def good_answer(d):
    uid = d.sections[0].units[0].id
    return {"units": [{**GOOD, "id": uid}],
            "section": {"takeaway": "The job did not run on 2026-10-12 because the disk was full.",
                        "topic": "disk was full", "rests_on": [uid]}}


def test_a_line_the_judge_refuses_is_asked_again_and_then_kept():
    d = section_doc()
    ask, calls = fake([good_answer(d), good_answer(d)])
    judge, seen = refusing("point")
    out = F.fold_section(d, d.sections[0], ask, judge=judge)
    assert len(calls) == 2 and "the judge refused the point" in calls[1]
    assert out["units"][0]["source"] == "claude" and len(seen) == 2


def test_a_line_the_judge_refuses_twice_is_the_authors():
    d = section_doc()
    ask, calls = fake([good_answer(d), good_answer(d)])

    def never(prompt):
        ids = [line.split(">")[0][1:] for line in prompt.splitlines() if line.startswith("<")]
        return {"lines": [{**x, "stated": not x["id"].endswith("/reason"),
                           "problem": "the sentences give no reason"} for x in stated(prompt)["lines"]]}
    out = F.fold_section(d, d.sections[0], ask, judge=never)
    u = out["units"][0]
    assert u["source"] == "author" and "the judge refused the reason" in u["refused"][0]
    assert out["section"]["source"] == "claude"


def test_a_judge_that_cannot_answer_lets_nothing_through():
    d = section_doc()
    ask, _ = fake([good_answer(d), good_answer(d)])

    def broken(prompt):
        raise RuntimeError("timed out")
    out = F.fold_section(d, d.sections[0], ask, judge=broken)
    assert out["units"][0]["source"] == "author" and out["section"]["source"] == "author"
    assert "the judge could not check it" in out["units"][0]["refused"][0]


def test_a_verdict_left_out_is_a_refusal():
    d = section_doc()
    ask, _ = fake([good_answer(d), good_answer(d)])
    out = F.fold_section(d, d.sections[0], ask, judge=lambda p: {"lines": []})
    assert out["units"][0]["source"] == "author" and "no verdict" in out["units"][0]["refused"][0]


def test_the_judge_sees_the_sentences_and_every_line():
    d = section_doc()
    seen = []

    def judge(p):
        seen.append(p)
        return stated(p)
    ask, _ = fake([good_answer(d)])
    F.fold_section(d, d.sections[0], ask, judge=judge)
    uid = d.sections[0].units[0].id
    for lid in (f"<{uid}/point>", f"<{uid}/reason>", f"<{uid}/topic>", "<section/takeaway>",
                "<section/topic>"):
        assert lid in seen[0]
    assert "0: The job did not run on 2026-10-12" in seen[0]


def test_a_stated_line_needs_real_support():
    d = section_doc()
    for support in ([], ["why-it-failed.p0:9"], ["other.p0:0"]):
        ask, _ = fake([good_answer(d), good_answer(d)])

        def judge(prompt, support=support):
            return {"lines": [{**x, "support": support} if x["id"].endswith("/point") else x
                              for x in stated(prompt)["lines"]]}
        u = F.fold_section(d, d.sections[0], ask, judge=judge)["units"][0]
        assert u["source"] == "author" and "named no sentences" in u["refused"][0], support
