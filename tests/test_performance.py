"""Guards for the behaviour that keeps ~30 concurrent students usable on Streamlit
Community Cloud, where every session shares one process and roughly one core.

These are behavioural, not wall-clock, assertions: they pin down HOW OFTEN expensive work
happens, which is what actually scales, rather than how fast one machine runs it.
"""

import time

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

import student_store
from aggplan import persistence as P
from aggplan import workflow as W
from aggplan.scenario import NON_MODEL_PARAMS, resolve_scenario
from aggplan.worksheet import EDIT_COLS
from juice_director import encode_cfg
from tests.fixtures import make_pg

SCN = resolve_scenario({}, 7)


def complete_pg():
    pg = make_pg(SCN)
    pg["base_seed"], pg["variant"] = 7, 0
    for k in ("chase", "level"):
        pg["plans"][k]["ws"] = {c: [str(1000 + i) for i in range(12)] for c in EDIT_COLS}
        pg["plans"][k]["sig"] = W.plan_signature(SCN.config_hash, pg["plans"][k])
    return pg


class CountingStore:
    """Stands in for the Dropbox-backed store and counts uploads."""

    def __init__(self):
        self.saves = 0
        self.last = None

    def install(self, monkeypatch):
        monkeypatch.setattr(student_store, "enabled", lambda: True)
        monkeypatch.setattr(student_store, "load", lambda g, s: {})

        def save(g, s, state, **k):
            self.saves += 1
            self.last = state
            return True

        monkeypatch.setattr(student_store, "save", save)
        return self


def app(pg, cfg=None, stage="reflect"):
    at = AppTest.from_file("app.py", default_timeout=180)
    if cfg:
        at.query_params["cfg"] = encode_cfg(cfg)
    at.query_params["game"], at.query_params["sid"] = "g1", "s1"
    at.session_state["pg"] = pg
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = stage
    return at


def ss_keys(at, prefix):
    """Session-state keys with a prefix (AppTest exposes them via filtered_state)."""
    return [k for k in at.session_state.filtered_state if str(k).startswith(prefix)]


def area(at, key="w_recommendation"):
    return [t for t in at.text_area if t.key == key][0]


# --------------------------------------------------------------------- autosave
def test_rapid_edits_coalesce_into_one_upload(monkeypatch):
    """V2 sent one encrypted upload per content change; 30 students editing would saturate
    the store's rate limit and make everyone wait on its backoff sleep."""
    store = CountingStore().install(monkeypatch)
    at = app(complete_pg(), {"autosave_seconds": 30})
    at.run()
    base = store.saves
    txt = "I recommend the level plan because "
    for i in range(25):
        txt += "word%d " % i
        area(at).set_value(txt).run()
    assert store.saves - base == 0, "rapid edits must not each trigger an upload"


def test_pending_changes_are_uploaded_once_the_window_elapses(monkeypatch):
    store = CountingStore().install(monkeypatch)
    at = app(complete_pg(), {"autosave_seconds": 0.3})
    at.run()
    base = store.saves
    area(at).set_value("first edit").run()
    assert store.saves - base == 0
    time.sleep(0.4)
    area(at).set_value("latest edit").run()
    assert store.saves - base == 1
    # the upload carries the newest text, so debouncing never loses the student's work
    assert store.last["pg"]["ans"]["recommendation"] == "latest edit"


def test_milestones_save_immediately_without_waiting(monkeypatch):
    """Navigation and other milestones must not sit in the debounce window."""
    store = CountingStore().install(monkeypatch)
    at = app(complete_pg(), {"autosave_seconds": 300})
    at.run()
    area(at).set_value("some unsaved words").run()
    before = store.saves
    at.session_state["nav_stage"] = "compare"
    at.run()
    assert store.saves - before == 1


def test_unsaved_changes_are_shown_honestly(monkeypatch):
    CountingStore().install(monkeypatch)
    at = app(complete_pg(), {"autosave_seconds": 300})
    at.run()
    area(at).set_value("typed but not yet uploaded").run()
    assert P.SaveStatus.from_dict(at.session_state["_save"]).pending is True
    shown = " ".join(e.value for e in list(at.success) + list(at.info) + list(at.warning))
    assert "Unsaved changes" in shown


def test_should_save_rules():
    s = P.SaveStatus(state="saved", last_ok=1000.0, last_attempt=1000.0)
    assert P.should_save(s, True, now=1006.0, debounce=5.0)
    assert not P.should_save(s, True, now=1001.0, debounce=5.0)
    assert P.should_save(s, True, important=True, now=1001.0, debounce=5.0)
    assert not P.should_save(s, False, now=1001.0)
    # a first-ever save is never delayed
    assert P.should_save(P.SaveStatus(state="pending"), True, now=1.0, debounce=99.0)
    # a failure retries on its own once the retry window passes, with nothing new to send
    f = P.SaveStatus(state="failed", last_ok=1.0, last_attempt=1000.0)
    assert not P.should_save(f, False, now=1000.0 + P.RETRY_SECONDS - 1)
    assert P.should_save(f, False, now=1000.0 + P.RETRY_SECONDS + 1)


def test_milestone_key_tracks_work_but_ignores_typing():
    pg = complete_pg()
    k = P.milestone_key(pg, "reflect")
    pg["ans"]["recommendation"] = "typing more words here"
    assert P.milestone_key(pg, "reflect") == k            # typing is not a milestone
    assert P.milestone_key(pg, "compare") != k            # navigation is
    pg2 = complete_pg()
    pg2["checks"]["capacity"]["passed"] = False
    assert P.milestone_key(pg2, "reflect") != k           # a passed check is
    pg3 = complete_pg()
    pg3["report"] = {"attempt_id": "AA-XYZ"}
    assert P.milestone_key(pg3, "reflect") != k           # a generated report is


def test_no_background_saving_in_practice_mode(monkeypatch):
    """Standalone practice must cost the server nothing at all."""
    seen = {"n": 0}
    monkeypatch.setattr(student_store, "enabled", lambda: False)

    def save(*a, **k):
        seen["n"] += 1
        return True

    monkeypatch.setattr(student_store, "save", save)
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["pg"] = complete_pg()
    at.session_state["_restore_done"] = True
    at.run()
    assert seen["n"] == 0


# --------------------------------------------------------------- lazy heavy work
def test_excel_workbook_is_not_built_until_requested():
    """Building the template costs ~80 ms and tens of KB; most students never open Excel."""
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["pg"] = complete_pg()
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = "chase"
    at.run()
    assert not ss_keys(at, "_xl_tpl_")
    prep = [b for b in at.button if "Prepare my Excel workbook" in b.label]
    assert prep, "the workbook must be offered behind a button"
    prep[0].click().run()
    built = ss_keys(at, "_xl_tpl_")
    assert len(built) == 1 and at.session_state[built[0]][:2] == b"PK"


def test_completed_plans_export_is_not_built_until_requested():
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["pg"] = complete_pg()
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = "compare"
    at.run()
    assert not ss_keys(at, "_xl_exp_")
    btn = [b for b in at.button if "Prepare my completed plans" in b.label]
    assert btn, "the export must be offered behind a button"
    btn[0].click().run()
    assert ss_keys(at, "_xl_exp_")


def test_only_one_workbook_is_kept_per_session():
    """Session memory is multiplied by every concurrent student."""
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["pg"] = complete_pg()
    at.session_state["_restore_done"] = True
    at.session_state["nav_stage"] = "chase"
    at.run()
    [b for b in at.button if "Prepare my Excel workbook" in b.label][0].click().run()
    # switching the worker model builds a different workbook; the stale one must be dropped
    [r for r in at.radio if r.key == "w_chase_wmodel"][0].set_value(
        "Partial workers (worker-equivalents)").run()
    [b for b in at.button if "Prepare my Excel workbook" in b.label][0].click().run()
    assert len(ss_keys(at, "_xl_tpl_")) == 1


# --------------------------------------------------- presentation vs model config
def test_presentation_settings_do_not_invalidate_student_work():
    """Retuning feedback, timing or the assignment name mid-course must not reject saved
    progress or un-verify a plan the student already completed."""
    base = resolve_scenario({}, 7)
    pg = complete_pg()
    assert W.plan_complete(pg, "chase", base.config_hash)
    for k, v in [("autosave_seconds", 30.0), ("autosave_flush_seconds", 0.0),
                 ("assignment_name", "Renamed Assignment"), ("report_timezone", "UTC"),
                 ("feedback_mode", "live"), ("benchmark_reveal", "always"),
                 ("allow_replacement", False)]:
        other = resolve_scenario({k: v}, 7)
        assert other.config_hash == base.config_hash, k
        assert W.plan_complete(pg, "chase", other.config_hash), k
        snap = P.make_snapshot(pg, base, "unverified")
        assert P.check_compatibility(snap, other).ok, k


def test_model_settings_still_invalidate_student_work():
    base = resolve_scenario({}, 7)
    pg = complete_pg()
    for k, v in [("holding_cost", 0.9), ("starting_workforce", 12), ("safety_stock", 5000),
                 ("overtime_pct", 0.5), ("terminal_backlog_max", 999)]:
        other = resolve_scenario({k: v}, 7)
        assert other.config_hash != base.config_hash, k
        assert not W.plan_complete(pg, "chase", other.config_hash), k
        snap = P.make_snapshot(pg, base, "unverified")
        assert not P.check_compatibility(snap, other).ok, k


def test_non_model_params_are_all_real_parameters():
    from manifest import MANIFEST
    assert NON_MODEL_PARAMS <= set(MANIFEST["params"]), "stale name in NON_MODEL_PARAMS"
