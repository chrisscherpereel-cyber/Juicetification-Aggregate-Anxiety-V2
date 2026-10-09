"""Streamlit smoke checks (streamlit.testing.AppTest): every screen renders, the submission
flow works, and remote-storage / identity / version problems behave as designed."""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

import student_store
from aggplan import identity as ident
from aggplan import persistence as P
from aggplan import workflow as W
from aggplan.scenario import resolve_scenario
from tests.fixtures import make_pg

STAGES = list(W.STAGE_KEYS)


def make_app(pg=None, qp=None):
    at = AppTest.from_file("app.py", default_timeout=120)
    if pg is not None:
        at.session_state["pg"] = pg
        at.session_state["_restore_done"] = True
    for k, v in (qp or {}).items():
        at.query_params[k] = v
    return at


def complete_pg(seed=7):
    scn = resolve_scenario({}, seed)
    pg = make_pg(scn)
    pg["base_seed"], pg["variant"], pg["lineage"] = seed, 0, []
    return pg


def texts(at):
    return " ".join([e.value for e in at.markdown] + [e.value for e in at.error] +
                    [e.value for e in at.warning] + [e.value for e in at.success] +
                    [e.value for e in at.info] + [e.value for e in at.caption])


def test_every_stage_renders_for_a_new_student():
    at = make_app()
    at.run()
    assert not at.exception
    for s in STAGES:
        at.session_state["nav_stage"] = s
        at.run()
        assert not at.exception, (s, [e.value for e in at.exception])


def test_every_stage_renders_for_a_completed_student():
    at = make_app(complete_pg())
    at.run()
    for s in STAGES:
        at.session_state["nav_stage"] = s
        at.run()
        assert not at.exception, (s, [e.value for e in at.exception])


def test_task_card_shows_task_action_progress_and_next_step():
    at = make_app(complete_pg())
    at.session_state["nav_stage"] = "data"
    at.run()
    t = texts(at)
    assert "Your task." in t and "What to do now." in t
    assert any("Next step" in b.label for b in at.button)
    assert any("Check my work" == b.label for b in at.button)
    new = make_app()
    new.session_state["nav_stage"] = "chase"
    new.run()
    assert "Why this isn't complete yet" in texts(new) and "Capacity" in texts(new)


def test_final_report_generates_and_is_downloadable():
    at = make_app(complete_pg())
    at.session_state["nav_stage"] = "submit"
    at.run()
    gen = [b for b in at.button if b.label == "Generate final report"][0]
    assert not gen.disabled
    gen.click().run()
    assert not at.exception
    built = at.session_state["_pdf"]
    assert built["bytes"].startswith(b"%PDF") and built["status"] == "Final"
    assert built["filename"].startswith("Aggregate_Anxiety_") and built["filename"].endswith(".pdf")
    assert at.session_state["pg"]["report"]["final_generated"] is True
    assert "upload the PDF to your LMS assignment" in texts(at)


def test_incomplete_work_cannot_generate_final_but_can_draft():
    scn = resolve_scenario({}, 7)
    pg = make_pg(scn, complete=False)
    pg["base_seed"], pg["variant"] = 7, 0
    at = make_app(pg)
    at.session_state["nav_stage"] = "submit"
    at.run()
    gen = [b for b in at.button if b.label == "Generate final report"][0]
    assert gen.disabled and "Before you can generate the final report" in texts(at)
    [b for b in at.button if b.label.startswith("Prepare a draft")][0].click().run()
    assert at.session_state["_pdf"]["status"] == "Draft"
    assert not at.session_state["pg"].get("report", {}).get("final_generated")


class FailingStore:
    """Remote storage that is 'enabled' but every call fails."""
    saved = 0

    @staticmethod
    def patch(monkeypatch):
        monkeypatch.setattr(student_store, "enabled", lambda: True)
        def boom(*a, **k):
            raise ConnectionError("Connection timed out token=abc")
        monkeypatch.setattr(student_store, "save", boom)
        monkeypatch.setattr(student_store, "load", lambda g, s: {})
        monkeypatch.setattr(student_store, "record_completion", boom)


def test_pdf_download_still_works_when_remote_storage_is_down(monkeypatch):
    FailingStore.patch(monkeypatch)
    pg = complete_pg()
    at = make_app(pg, {"game": "g1", "sid": "student1"})
    at.session_state["nav_stage"] = "submit"
    at.run()
    assert not at.exception
    assert "Not saved" in texts(at) and "token=abc" not in texts(at)          # clear, secret-free
    [b for b in at.button if b.label == "Generate final report"][0].click().run()
    assert not at.exception
    assert at.session_state["_pdf"]["bytes"].startswith(b"%PDF")
    t = texts(at)
    assert "could not be recorded" in t and "PDF is unaffected" in t
    assert any(b.label == "Retry recording" for b in at.button)
    assert at.session_state["pg"]["report"]["final_generated"] is True


def test_successful_remote_recording_after_report(monkeypatch):
    rec = {}
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "save", lambda *a, **k: True)
    monkeypatch.setattr(student_store, "load", lambda g, s: {})
    monkeypatch.setattr(student_store, "record_completion",
                        lambda g, s, completion_code=None, score=None, extra=None: rec.update(
                            code=completion_code, score=score, extra=extra) or True)
    at = make_app(complete_pg(), {"game": "g1", "sid": "student1"})
    at.session_state["nav_stage"] = "submit"
    at.run()
    [b for b in at.button if b.label == "Generate final report"][0].click().run()
    assert rec["code"].startswith("AA-") and rec["score"] is None
    assert rec["extra"]["snapshot_hash"] and "not an academic grade" in rec["extra"]["plan_cost_note"]
    assert rec["extra"]["identity"] == "unverified"


def test_signed_assignment_required_when_key_configured(monkeypatch):
    monkeypatch.setenv("ASSIGNMENT_SIGNING_KEY", "k")
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    at = make_app(None, {"game": "g1", "sid": "student1"})
    at.run()
    assert "needs your personal sign-in link" in texts(at)
    tok = ident.make_token(b"k", "g1", "student1")
    ok = make_app(None, {"game": "g1", "sid": "student1", "tok": tok})
    monkeypatch.setattr(student_store, "load", lambda g, s: {})
    ok.run()
    assert not ok.exception and "needs your personal sign-in link" not in texts(ok)
    bad = make_app(None, {"game": "g1", "sid": "other", "tok": tok})
    bad.run()
    assert "needs your personal sign-in link" in texts(bad)


def test_standalone_practice_needs_no_setup(monkeypatch):
    monkeypatch.delenv("ASSIGNMENT_SIGNING_KEY", raising=False)
    at = make_app()
    at.run()
    assert not at.exception and "Practice mode" in texts(at)


def test_incompatible_saved_progress_is_not_restored_or_overwritten(monkeypatch):
    saved_scn = resolve_scenario({"holding_cost": 0.9}, 7)               # saved under other settings
    saved = P.make_snapshot(make_pg(saved_scn), saved_scn)
    saved["pg"]["base_seed"], saved["pg"]["variant"] = 7, 0
    writes = []
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "load", lambda g, s: saved)
    monkeypatch.setattr(student_store, "save", lambda *a, **k: writes.append(1) or True)
    at = make_app(None, {"game": "g1", "sid": "student1"})
    at.run()
    assert "can't be reused safely" in texts(at) and "assignment settings" in texts(at)
    assert not writes                                                       # nothing overwritten
    assert any(b.label.startswith("Start fresh") for b in at.button)


def test_legacy_unversioned_progress_is_flagged(monkeypatch):
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "load", lambda g, s: {"chase_ws": {"__df__": {}}, "rand_seed": 5})
    monkeypatch.setattr(student_store, "save", lambda *a, **k: True)
    at = make_app(None, {"game": "g1", "sid": "student1"})
    at.run()
    assert "earlier version" in texts(at)


def test_load_failure_pauses_saving(monkeypatch):
    def boom(g, s):
        raise ConnectionError("down")
    writes = []
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "load", boom)
    monkeypatch.setattr(student_store, "save", lambda *a, **k: writes.append(1) or True)
    at = make_app(None, {"game": "g1", "sid": "student1"})
    at.run()
    assert "could not be loaded" in texts(at) and not writes


def test_compatible_saved_progress_is_restored(monkeypatch):
    s = resolve_scenario({}, 7)
    saved = P.make_snapshot(dict(make_pg(s), base_seed=7, variant=0), s)
    monkeypatch.setattr(student_store, "enabled", lambda: True)
    monkeypatch.setattr(student_store, "load", lambda g, sid: saved)
    monkeypatch.setattr(student_store, "save", lambda *a, **k: True)
    at = make_app(None, {"game": "g1", "sid": "student1"})
    at.run()
    assert not at.exception and at.session_state["pg"]["student_name"].startswith("Ada")
    assert "Saved at" in texts(at)


def test_director_manifest_is_served_and_has_schema():
    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["manifest"] = "1"
    at.run()
    assert not at.exception and at.json
    from manifest import MANIFEST
    assert "beginning_inventory" in MANIFEST["params"] and MANIFEST["schema_version"] == 1


def test_director_cfg_flows_into_the_ui_text():
    from juice_director import encode_cfg
    cfg = encode_cfg({"regular_labor_cost": 4321, "hiring_cost": 777,
                      "forecast_demand": [12000, 12500, 15000, 20000, 26000, 31000, 33000, 28000,
                                          22000, 17000, 14000, 11000]})
    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["cfg"] = cfg
    at.run()
    assert not at.exception
    flat = " ".join(d.value.to_string() for d in at.dataframe)
    assert "4,321" in flat and "$777" in flat and "12,000" in flat


def _stuck_replace(at):
    at.session_state["nav_stage"] = "chase"
    at.run()
    [c for c in at.checkbox if c.key == "chase_reveal"][0].check().run()
    [b for b in at.button if b.label.startswith("🎲 Start the replacement")][0].click().run()
    assert not at.exception


def test_replacement_scenario_is_deterministic_cleared_and_recorded():
    pg = complete_pg()
    at = make_app(pg)
    _stuck_replace(at)
    new = at.session_state["pg"]
    assert new["variant"] == 1 and new["plans"]["chase"]["tries"] == 2
    assert new["plans"]["chase"]["stuck"] == 1 and not new["plans"]["chase"]["completed"]
    assert not new["plans"]["level"]["completed"] and new["hybrid"] == {} and "report" not in new
    assert new["lineage"][-1]["variant"] == 1
    again = make_app(complete_pg())
    _stuck_replace(again)
    assert again.session_state["pg"]["variant"] == 1
    # same replacement numbers every time for the same student
    from aggplan.scenario import resolve_scenario as rs
    assert rs({}, 7, 1).demand == rs({}, 7, 1).demand != rs({}, 7, 0).demand


def test_replacement_with_instructor_fixed_demand_gives_a_variant_not_the_same_curve():
    from juice_director import encode_cfg
    fixed = [12000, 12500, 15000, 20000, 26000, 31000, 33000, 28000, 22000, 17000, 14000, 11000]
    at = AppTest.from_file("app.py", default_timeout=120)
    at.query_params["cfg"] = encode_cfg({"forecast_demand": fixed})
    pg = complete_pg()
    at.session_state["pg"], at.session_state["_restore_done"] = pg, True
    at.session_state["nav_stage"] = "chase"
    at.run()
    [c for c in at.checkbox if c.key == "chase_reveal"][0].check().run()
    assert "reproducible variant of your instructor" in texts(at)
    [b for b in at.button if b.label.startswith("🎲 Start the replacement")][0].click().run()
    assert at.session_state["pg"]["variant"] == 1


def test_replacement_hidden_when_instructor_disables_it():
    from juice_director import encode_cfg
    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["cfg"] = encode_cfg({"allow_replacement": False})
    at.session_state["pg"], at.session_state["_restore_done"] = complete_pg(), True
    at.session_state["nav_stage"] = "chase"
    at.run()
    assert not [c for c in at.checkbox if c.key == "chase_reveal"]
