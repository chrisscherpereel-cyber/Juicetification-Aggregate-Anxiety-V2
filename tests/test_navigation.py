"""Navigation a student can follow.

Measured at 1366x768 before this work: the sidebar put "Your path" 530 px down the page, so
four of the five groups sat below the fold and the stages inside a collapsed group were not
rendered at all — a student had to scroll and expand to discover that building the plans was
part of the assignment. These tests pin the structure that fixed it; the pixel measurement
itself lives in the PR, since AppTest has no layout.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

from aggplan import workflow as W
from aggplan.scenario import resolve_scenario
from aggplan.worksheet import EDIT_COLS
from tests.fixtures import make_pg

SCN = resolve_scenario({}, 7)
H = SCN.config_hash
STEP_WORDS = ("Understand", "Prepare", "Build", "Compare", "Submit")


def pg_at(complete=False):
    pg = make_pg(SCN, complete=complete)
    pg["base_seed"], pg["variant"] = 7, 0
    if complete:
        for k in ("chase", "level"):
            pg["plans"][k]["ws"] = {c: [str(1000 + i) for i in range(12)] for c in EDIT_COLS}
            pg["plans"][k]["sig"] = W.plan_signature(H, pg["plans"][k])
    return pg


def app(pg, stage="brief"):
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["pg"] = pg
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = stage
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def labels(at):
    return [b.label for b in at.button]


# ------------------------------------------------- 1 & 2: the sidebar
def test_every_stage_is_one_click_with_nothing_to_expand():
    """Previously only the current group's stages existed in the page; the rest needed an
    expander click first."""
    at = app(pg_at())
    shown = labels(at)
    for s in W.STAGES:
        assert any(s.title in l for l in shown), s.title


def test_identity_fields_are_not_in_the_sidebar_anymore():
    """They are needed only to name the report, and they were pushing navigation off screen."""
    at = app(pg_at())
    assert not [t for t in at.text_input if t.key in ("in_name", "in_section")]


def test_identity_fields_live_on_the_stage_that_needs_them():
    at = app(pg_at(complete=True), stage="submit")
    keys = [t.key for t in at.text_input]
    assert "in_name" in keys and "in_section" in keys
    name = [t for t in at.text_input if t.key == "in_name"][0]
    name.set_value("Ada Lovelace").run()
    assert at.session_state["pg"]["student_name"] == "Ada Lovelace"


def test_sidebar_says_where_to_set_a_missing_name():
    pg = pg_at()
    pg["student_name"] = ""
    at = app(pg)
    text = " ".join(c.value for c in at.caption)
    assert "Name not set" in text and "Submit and reflect" in text


# ------------------------------------------------- 3: the stepper
def test_stepper_shows_all_five_groups_with_progress_on_every_stage():
    for stage in ("brief", "capacity", "chase", "compare", "submit"):
        at = app(pg_at(), stage=stage)
        shown = labels(at)
        for word in STEP_WORDS:
            assert any(word in l and "/" in l for l in shown), (stage, word)


def test_stepper_marks_the_current_group_with_a_symbol_not_only_colour():
    at = app(pg_at(), stage="chase")
    current = [l for l in labels(at) if l.startswith("▸")]
    assert len(current) == 1 and "Build" in current[0]


def test_stepper_navigates_to_the_first_unfinished_stage_of_its_group():
    at = app(pg_at())
    step = [b for b in at.button if b.label.strip().startswith("Prepare")][0]
    step.click().run()
    assert at.session_state["nav_stage"] == "data"      # first unfinished required in group 2


def test_stepper_progress_tracks_real_completion():
    at = app(pg_at(complete=True), stage="compare")
    build = [l for l in labels(at) if "Build" in l][0]
    assert "✓" in build or "2/2" in build


# ------------------------------------------------- 4: Back and Next
def test_back_and_next_both_name_their_destination():
    at = app(pg_at(), stage="capacity")
    shown = labels(at)
    assert any(l.startswith("← Back to ") for l in shown), shown
    assert any(l.startswith("Next: ") and l.endswith(" →") for l in shown), shown


def test_back_skips_an_optional_stage_the_student_passed_over():
    """Next sends a student from 'formulas' straight to 'capacity', skipping the optional
    practice drills; Back must not then drop them into those drills."""
    pg = pg_at()
    assert W.next_stage(pg, H, "formulas") == "capacity"
    assert W.prev_stage(pg, H, "capacity") == "formulas"
    at = app(pg, stage="capacity")
    back = [b for b in at.button if b.label.startswith("← Back to ")][0]
    assert "Practice" not in back.label
    back.click().run()
    assert at.session_state["nav_stage"] == "formulas"


def test_back_is_absent_on_the_first_stage():
    at = app(pg_at(), stage="brief")
    assert not [l for l in labels(at) if l.startswith("← Back to ")]


# ------------------------------------------------- 5: resume
def test_resume_offers_the_next_stage_to_a_returning_student():
    pg = pg_at()
    pg["checks"]["data"] = {"n": 1, "passed": True}          # some progress exists
    at = app(pg, stage="brief")
    resume = [b for b in at.button if "Continue where I left off" in b.label]
    assert resume, labels(at)
    target = W.next_stage(pg, H, "brief")
    assert W.STAGE_BY_KEY[target].title in resume[0].label
    resume[0].click().run()
    assert at.session_state["nav_stage"] == target


def test_no_resume_prompt_for_a_brand_new_student():
    at = app(pg_at(), stage="brief")
    assert not [b for b in at.button if "Continue where I left off" in b.label]


def test_a_finished_student_is_pointed_at_the_report():
    at = app(pg_at(complete=True), stage="brief")
    btn = [b for b in at.button if "Download your PDF report" in b.label]
    assert btn
    btn[0].click().run()
    assert at.session_state["nav_stage"] == "submit"


# ------------------------------------------------- the helpers themselves
def test_prev_stage_never_returns_the_current_stage_or_runs_off_the_start():
    pg = pg_at()
    for key in W.STAGE_KEYS:
        p = W.prev_stage(pg, H, key)
        assert p != key
        if key == W.STAGE_KEYS[0]:
            assert p is None
        else:
            assert p in W.STAGE_KEYS


def test_group_progress_covers_every_group_once():
    rows = W.group_progress(pg_at(), H)
    assert [r[0] for r in rows] == [g for g, _ in W.GROUPS]
    assert all(0 <= done <= total and total > 0 for _, _, done, total in rows)
