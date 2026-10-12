"""Starting the simulation over.

This is the only control in the app that destroys a student's work, so most of what is
tested here is what must NOT happen: no single click can wipe anything, Cancel must leave
everything intact, and the new scenario must not inherit typed values from the old one.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

import student_store
from aggplan import workflow as W
from aggplan.scenario import resolve_scenario
from aggplan.worksheet import EDIT_COLS
from juice_director import encode_cfg
from tests.fixtures import make_pg

SCN = resolve_scenario({}, 7)
H = SCN.config_hash


def done_pg():
    pg = make_pg(SCN)
    pg["base_seed"], pg["variant"] = 7, 0
    for k in ("chase", "level"):
        pg["plans"][k]["ws"] = {c: [str(1000 + i) for i in range(12)] for c in EDIT_COLS}
        pg["plans"][k]["sig"] = W.plan_signature(H, pg["plans"][k])
    return pg


def app(pg=None, cfg=None, stage="brief", qp=None):
    at = AppTest.from_file("app.py", default_timeout=180)
    if cfg:
        at.query_params["cfg"] = encode_cfg(cfg)
    for k, v in (qp or {}).items():
        at.query_params[k] = v
    at.session_state["pg"] = pg if pg is not None else done_pg()
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = stage
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def labels(at):
    return [b.label for b in at.button]


def arm(at):
    [b for b in at.button if b.label == "Start over…"][0].click().run()
    return at


# ------------------------------------------------------- the confirmation
def test_no_single_click_can_destroy_work():
    at = app()
    assert "Start over…" in labels(at)
    assert "Yes, start over" not in labels(at)


def test_confirmation_warns_and_says_it_cannot_be_undone():
    at = arm(app())
    warning = " ".join(w.value for w in at.warning)
    assert "Are you sure" in warning
    assert "cannot be undone" in warning
    assert {"Yes, start over", "Cancel"} <= set(labels(at))


def test_cancel_changes_nothing():
    at = arm(app())
    [b for b in at.button if b.label == "Cancel"][0].click().run()
    pg = at.session_state["pg"]
    assert W.plan_complete(pg, "chase", H)
    assert pg["variant"] == 0 and "resets" not in pg
    assert "Yes, start over" not in labels(at)          # disarmed again


def test_a_copy_of_the_work_is_offered_before_resetting():
    """The only protection against an accidental reset is a file taken beforehand."""
    at = app()
    offered = [getattr(d, "label", "") for d in at.get("download_button")]
    assert any("copy of my work first" in l for l in offered), offered


def test_warning_mentions_an_already_submitted_report():
    pg = done_pg()
    pg["report"] = {"final_generated": True, "attempt_id": "AA-OLD", "fp": "x"}
    at = arm(app(pg))
    warning = " ".join(w.value for w in at.warning)
    assert "already downloaded and submitted still counts" in warning
    assert "new attempt" in warning


# ------------------------------------------------------- what reset does
def test_reset_clears_the_work_and_moves_to_a_new_scenario():
    at = arm(app())
    [b for b in at.button if b.label == "Yes, start over"][0].click().run()
    pg = at.session_state["pg"]
    assert pg["plans"] == {} and pg["checks"] == {} and pg["ans"] == {}
    assert pg["variant"] == 1 and pg["resets"] == 1
    assert at.session_state["nav_stage"] == "brief"
    new = resolve_scenario({}, pg["base_seed"], pg["variant"])
    assert list(new.demand) != list(SCN.demand)
    assert len(W.missing_requirements(pg, new.config_hash)) == 8


def test_identity_survives_but_work_does_not():
    at = arm(app())
    [b for b in at.button if b.label == "Yes, start over"][0].click().run()
    pg = at.session_state["pg"]
    assert pg["student_name"].startswith("Ada") and pg["section"] == "OPS 301-02"
    assert not pg["practice"]
    # 'visited' is emptied too; it only holds {'brief': True} again because the reset lands
    # the student on the brief and rendering it marks the visit
    assert set(pg["visited"]) <= {"brief"}


def test_no_typed_value_leaks_into_the_new_scenario():
    """Streamlit keeps widget state by key; without clearing it the new scenario's grid would
    come up pre-filled with the old scenario's numbers."""
    at = app(stage="chase")
    assert at.session_state["pg"]["plans"]["chase"]["ws"]["Hires"][0] == "1000"
    at.session_state["nav_stage"] = "brief"
    at.run()
    arm(at)
    [b for b in at.button if b.label == "Yes, start over"][0].click().run()
    at.session_state["nav_stage"] = "chase"
    at.run()
    ws = (at.session_state["pg"]["plans"].get("chase") or {}).get("ws") or {}
    assert not [c for c, v in ws.items() if any(x not in ("", None) for x in v)]


def test_the_replacement_scenario_is_reproducible_not_random():
    """Same student, same reset number, same numbers — so an instructor can reconstruct it."""
    seeds = []
    for _ in range(2):
        at = arm(app())
        [b for b in at.button if b.label == "Yes, start over"][0].click().run()
        pg = at.session_state["pg"]
        seeds.append(list(resolve_scenario({}, pg["base_seed"], pg["variant"]).demand))
    assert seeds[0] == seeds[1]


def test_reset_is_recorded_in_the_scenario_lineage():
    at = arm(app())
    [b for b in at.button if b.label == "Yes, start over"][0].click().run()
    last = at.session_state["pg"]["lineage"][-1]
    assert last["variant"] == 1 and "restarted" in last["reason"]


def test_an_instructor_fixed_demand_curve_still_yields_a_variant_of_that_curve():
    fixed = [12000, 12500, 15000, 20000, 26000, 31000, 33000, 28000, 22000, 17000, 14000, 11000]
    a = resolve_scenario({"forecast_demand": fixed}, 7, 0)
    b = resolve_scenario({"forecast_demand": fixed}, 7, 1)
    assert b.fixed_demand and list(b.demand) != list(a.demand)
    assert sum(b.demand) == pytest.approx(sum(fixed), abs=500)


def test_reset_reaches_the_server_immediately(monkeypatch):
    """A reset is a milestone: it must not sit in the autosave debounce window."""
    seen = {"n": 0, "last": None}
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "load", lambda g, s: {})

    def save(g, s, state, **k):
        seen["n"] += 1
        seen["last"] = state
        return True

    monkeypatch.setattr(student_store, "save", save)
    at = app(cfg={"autosave_seconds": 300}, qp={"game": "g1", "sid": "s1"})
    arm(at)
    before = seen["n"]
    [b for b in at.button if b.label == "Yes, start over"][0].click().run()
    assert seen["n"] > before
    assert seen["last"]["pg"]["variant"] == 1 and seen["last"]["pg"]["plans"] == {}


# ------------------------------------------------------- instructor control
def test_an_instructor_can_switch_reset_off():
    at = app(cfg={"allow_reset": False})
    assert "Start over…" not in labels(at)


def test_the_switch_is_not_part_of_the_scenario_hash():
    """Turning reset off mid-course must not invalidate anyone's saved work."""
    from aggplan import persistence as P
    base = resolve_scenario({}, 7)
    other = resolve_scenario({"allow_reset": False}, 7)
    assert other.config_hash == base.config_hash
    assert P.check_compatibility(P.make_snapshot(done_pg(), base, "unverified"), other).ok


def test_reset_pg_is_pure_and_leaves_the_original_alone():
    pg = done_pg()
    before = dict(pg)
    fresh = W.reset_pg(pg)
    assert pg["plans"] == before["plans"] and pg["variant"] == 0
    assert fresh is not pg and fresh["plans"] == {}
