"""The formula-share nudge on the plan stages.

The course objective for this version is that students learn basic Excel functions, but
checking compares calculated values, so a hand-calculated plan passes exactly like a linked
model. These tests pin the middle ground: always SHOW how the worksheet was built, encourage
formulas, and only block completion when an instructor has set a minimum.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

from aggplan.engine import reference_plan
from aggplan.scenario import resolve_scenario
from aggplan.worksheet import EDIT_COLS, authoring_stats, truth_worksheet
from juice_director import encode_cfg
from tests.fixtures import make_pg

SCN = resolve_scenario({}, 7)
_DEC, _PLAN = reference_plan(SCN, "chase", policy_key="use_first", whole=True)
TRUTH = truth_worksheet(_PLAN)
TOTAL = float(TRUTH["Total Monthly Cost"].sum())

FORMULAS = {"Regular Labor Cost": "=F{r}*LABOR", "Hiring Cost": "=H{r}*HIRE",
            "Layoff Cost": "=I{r}*LAYOFF", "Holding Cost": "=J{r}*HOLD",
            "Backorder Cost": "=K{r}*BACKORDER", "Total Monthly Cost": "=SUM(L{r}:P{r})",
            "Ending Inventory": "=IF(C{r}+E{r}-B{r}>0,C{r}+E{r}-B{r},0)"}


def worksheet(use_formulas):
    return {c: [(FORMULAS[c].format(r=i + 2) if (use_formulas and c in FORMULAS)
                 else "%g" % float(TRUTH[c].iloc[i])) for i in range(12)]
            for c in EDIT_COLS}


def run(use_formulas, cfg=None):
    pg = make_pg(SCN)
    pg["base_seed"], pg["variant"] = 7, 0
    pg["plans"]["chase"]["ws"] = worksheet(use_formulas)
    at = AppTest.from_file("app.py", default_timeout=180)
    if cfg:
        at.query_params["cfg"] = encode_cfg(cfg)
    at.session_state["pg"] = pg
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = "chase"
    at.run()
    at.session_state["w_chase_totalcost"] = TOTAL
    at.run()
    [b for b in at.button if b.label == "Check my work"][0].click().run()
    assert not at.exception
    text = " ".join(e.value for e in list(at.markdown) + list(at.success) + list(at.info)
                    + list(at.warning) + list(at.caption))
    return at.session_state["pg"]["plans"]["chase"]["completed"], text


# the worksheet used here fills 7 of 15 columns with formulas
SHARE = authoring_stats(worksheet(True), SCN.named_constants())["formula_share_pct"]


def test_fixture_share_is_what_the_thresholds_are_chosen_around():
    assert 40 < SHARE < 50


def test_typed_numbers_still_complete_when_no_minimum_is_set():
    """Hand-calculating is a legitimate way to learn the planning logic; by default the app
    encourages formulas rather than demanding them."""
    done, text = run(False)
    assert done is True
    assert "Every value here is typed in" in text          # the nudge
    assert "not a grade" in text


def test_the_share_is_always_reported():
    done, text = run(True)
    assert done is True
    assert "use a formula" in text
    assert "LABOR" in text                                  # named values are listed


def test_instructor_minimum_blocks_and_explains():
    done, text = run(False, {"min_formula_share_pct": 50})
    assert done is False
    low = text.lower()
    assert "at least" in low and "50" in text
    assert "formula" in low


def test_instructor_minimum_passes_when_met():
    assert run(True, {"min_formula_share_pct": 40})[0] is True
    assert run(True, {"min_formula_share_pct": 50})[0] is False      # 46.7% is below 50


def test_minimum_is_not_part_of_the_scenario_hash():
    """Raising the bar mid-course must not invalidate saved progress or verified plans."""
    from aggplan import persistence as P
    base = resolve_scenario({}, 7)
    other = resolve_scenario({"min_formula_share_pct": 60}, 7)
    assert other.config_hash == base.config_hash
    snap = P.make_snapshot(make_pg(base), base, "unverified")
    assert P.check_compatibility(snap, other).ok
