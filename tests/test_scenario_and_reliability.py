import time

import pytest

from aggplan import identity as ident
from aggplan import persistence as P
from aggplan import workflow as W
from aggplan.scenario import (DEFAULTS, PRESETS, generate_forecast, resolve_scenario, stable_int,
                              variant_of_fixed_demand)
from tests.fixtures import make_pg
from tests.helpers import SEASONAL, scn


# ------------------------------------------------------------------ scenario
def test_defaults_reproduce_v2_scenario_for_same_seed():
    # V2 generator reference values for seed 7 (computed with the legacy algorithm)
    assert generate_forecast(7) == [7000, 6000, 7000, 11500, 13000, 16500, 18500, 14000, 10500, 9000, 7000, 6000]
    s = resolve_scenario({}, 7)
    assert list(s.demand) == generate_forecast(7) and s.total_demand % 6000 == 0


def test_bad_forecast_override_does_not_crash():
    for bad in ([1, 2, 3], ["a"] * 12, [-5] * 12, [float("nan")] * 12):
        s = resolve_scenario({"forecast_demand": bad}, 3)
        assert len(s.demand) == 12 and not s.fixed_demand
        assert any("12 non-negative numbers" in n for n in s.notices)


def test_inconsistent_capacity_is_derived_and_explained():
    s = resolve_scenario({"bottles_per_worker": 900}, 3)
    assert s["bottles_per_worker"] == 1000 and s.cap_month == 1000
    assert any("does not equal" in n for n in s.notices)


def test_presets_change_only_default_parameters():
    s = resolve_scenario({"scenario_preset": "stability_favored", "hiring_cost": 650}, 3)
    assert s["hiring_cost"] == 650                      # instructor value kept
    assert s["layoff_cost"] == PRESETS["stability_favored"]["layoff_cost"]
    ss = resolve_scenario({"scenario_preset": "strong_seasonal"}, 5)
    base = resolve_scenario({}, 5)
    assert max(ss.demand) / min(ss.demand) > max(base.demand) / min(base.demand)
    assert resolve_scenario({"scenario_preset": "nope"}, 1).preset == "standard"


def test_replacement_variants_are_reproducible_and_differ():
    a1, a2 = resolve_scenario({}, 11, 1), resolve_scenario({}, 11, 1)
    assert a1.demand == a2.demand and a1.seed == a2.seed
    assert resolve_scenario({}, 11, 2).demand != a1.demand
    assert a1.base_seed == 11 and a1.variant == 1


def test_replacement_with_fixed_demand_changes_numbers_but_keeps_shape_and_total():
    fixed = SEASONAL
    v0 = resolve_scenario({"forecast_demand": fixed}, 5, 0)
    v1 = resolve_scenario({"forecast_demand": fixed}, 5, 1)
    assert list(v0.demand) == fixed and v1.demand != v0.demand
    assert v1.fixed_demand and sum(v1.demand) == pytest.approx(sum(fixed), abs=500)
    assert v1.demand == resolve_scenario({"forecast_demand": fixed}, 5, 1).demand
    assert all(d % 500 == 0 for d in variant_of_fixed_demand(fixed, 9))


def test_stable_int_is_process_independent():
    assert stable_int("a", 1, lo=0, hi=1000) == stable_int("a", 1, lo=0, hi=1000)
    assert 0 <= stable_int("shuffle", 7, "k", "x") <= 10 ** 9


def test_config_hash_tracks_parameters_and_demand_but_instructor_hash_ignores_seed():
    a, b = resolve_scenario({}, 1), resolve_scenario({}, 2)
    assert a.config_hash != b.config_hash and a.instructor_config_hash == b.instructor_config_hash
    c = resolve_scenario({"holding_cost": 0.5}, 1)
    assert c.instructor_config_hash != a.instructor_config_hash
    f1, f2 = scn(SEASONAL), scn(SEASONAL, seed=99)
    assert f1.instructor_config_hash == f2.instructor_config_hash


def test_generated_labels_come_from_scenario():
    s = scn(SEASONAL)
    assert s.quarter_labels()[0] == "Q1 (Jan–Mar)" and s.quarter_labels()[3] == "Q4 (Oct–Dec)"


# ------------------------------------------------------------------ text from config
def test_instructional_text_uses_active_configuration():
    from aggplan import text as T
    s = scn(SEASONAL, regular_labor_cost=4100, hiring_cost=777, layoff_cost=1234, starting_workforce=5,
            beginning_inventory=1200, safety_stock=3000, overtime_pct=0.05)
    blob = "\n".join(str(x) for x in (T.param_rows(s), T.formula_reference(s, "chase", True),
                                      T.diagnostics(s, "chase", True), T.chase_how_to(s, "maintain", True),
                                      T.level_how_to(s, True, True), T.stage4_formulas(s),
                                      T.policy_caption(s, "maintain"), T.column_reasons(s)))
    for expect in ("4,100", "777", "1,234", "1,200", "3,000", "5%"):
        assert expect in blob
    for stale in ("3,200", "$600", "2,400", "$900"):
        assert stale not in blob, stale


# ------------------------------------------------------------------ persistence
def test_snapshot_roundtrip_and_recovery_file_checksum():
    s = scn(SEASONAL)
    pg = make_pg(s)
    snap = P.make_snapshot(pg, s, "practice")
    data = P.recovery_bytes(snap)
    got, err = P.parse_recovery(data)
    assert err == "" and got["pg"]["student_name"] == pg["student_name"]
    bad = data.replace(b"Lovelace", b"Hacked  ")
    assert P.parse_recovery(bad)[0] is None
    assert P.parse_recovery(b"not json")[0] is None
    assert b"token" not in data.lower() and b"password" not in data.lower()


def test_incompatible_progress_is_never_restored_silently():
    s = scn(SEASONAL)
    snap = P.make_snapshot(make_pg(s), s)
    assert P.check_compatibility(snap, s).ok
    # legacy record (no schema/meta)
    assert not P.check_compatibility({"chase_ws": {}}, s).ok
    # configuration changed since saving
    other = scn(SEASONAL, holding_cost=0.9)
    c = P.check_compatibility(snap, other)
    assert not c.ok and "assignment settings" in c.reason
    # model version changed
    snap2 = P.make_snapshot(make_pg(s), s)
    snap2["_meta"]["model_version"] = "2.9.0"
    assert not P.check_compatibility(snap2, s).ok
    snap3 = P.make_snapshot(make_pg(s), s)
    snap3["_meta"]["model_version"] = "3.0.9"                       # patch difference is fine
    assert P.check_compatibility(snap3, s).ok


def test_snapshot_records_seed_config_and_model_version():
    s = resolve_scenario({"holding_cost": 0.4}, 12, 1)
    m = P.make_snapshot({}, s)["_meta"]
    assert m["base_seed"] == 12 and m["variant"] == 1 and m["seed"] == s.seed
    assert m["resolved_params"]["holding_cost"] == 0.4 and m["model_version"]
    assert m["instructor_config_hash"] == s.instructor_config_hash


class FakeStore:
    def __init__(self, enabled=True, fail=None):
        self._enabled, self.fail, self.saved, self.completions = enabled, fail, [], []

    def enabled(self):
        return self._enabled

    def save(self, game, sid, state, progress=None, step=None, score=None):
        if self.fail:
            raise self.fail
        self.saved.append((game, sid, state, progress, step))
        return True

    def record_completion(self, game, sid, completion_code=None, score=None, extra=None):
        if self.fail:
            raise self.fail
        self.completions.append((completion_code, score, extra))
        return True

    def load(self, game, sid):
        if self.fail:
            raise self.fail
        return self.saved[-1][2] if self.saved else {}


def test_save_status_success_failure_retry_and_messages():
    st_ = P.SaveStatus()
    ok = FakeStore()
    P.save_remote(ok, "g", "sid", {"a": 1}, st_, now=1000.0)
    assert st_.state == "saved" and st_.last_ok == 1000.0 and "Saved at" in st_.label()
    bad = FakeStore(fail=ConnectionError("Connection timed out"))
    P.save_remote(bad, "g", "sid", {"a": 1}, st_, now=1010.0)
    assert st_.state == "failed" and "network" in st_.last_error.lower()
    assert st_.last_ok == 1000.0 and "Last successful save" in st_.label()
    # throttled retry
    n_before = st_.failures
    P.save_remote(bad, "g", "sid", {"a": 1}, st_, now=1011.0)
    assert st_.failures == n_before
    P.save_remote(ok, "g", "sid", {"a": 1}, st_, now=1011.0, force=True)
    assert st_.state == "saved"
    off = P.SaveStatus()
    P.save_remote(FakeStore(enabled=False), "g", "sid", {}, off)
    assert off.state == "off" and "this browser tab" in off.label()


def test_error_classification_never_leaks_secrets():
    e = RuntimeError("AuthError token=sk-SECRET123 invalid_access_token")
    msg = P.classify_error(e)
    assert "SECRET" not in msg and "credentials" in msg


def test_record_completion_safe_reports_failures_and_uses_attempt_id_not_grade():
    ok = FakeStore()
    done, msg = P.record_completion_safe(ok, "g", "sid", "AA-1", {"plan_costs_usd": {"chase": 5}})
    assert done and ok.completions[0][0] == "AA-1" and ok.completions[0][1] is None
    bad = FakeStore(fail=TimeoutError("timed out"))
    done, msg = P.record_completion_safe(bad, "g", "sid", "AA-1", {})
    assert not done and msg
    assert not P.record_completion_safe(FakeStore(enabled=False), "g", "sid", "AA-1", {})[0]
    assert not P.record_completion_safe(ok, "g", None, "AA-1", {})[0]


# ------------------------------------------------------------------ identity
def test_signed_links_verify_and_reject_tampering():
    key = b"test-key"
    tok = ident.make_token(key, "OPS301", "s123")
    assert ident.verify_token(key, tok, "OPS301", "s123").valid
    assert not ident.verify_token(key, tok, "OPS301", "s124").valid        # other student
    assert not ident.verify_token(key, tok, "OTHER", "s123").valid         # other assignment
    assert not ident.verify_token(b"wrong", tok, "OPS301", "s123").valid
    assert not ident.verify_token(key, tok + "x", "OPS301", "s123").valid
    assert not ident.verify_token(key, None, "OPS301", "s123").valid
    assert not ident.verify_token(key, "garbage", "OPS301", "s123").valid
    exp = ident.make_token(key, "g", "s", expires_at=100)
    assert not ident.verify_token(key, exp, "g", "s", now=101).valid
    assert ident.verify_token(key, exp, "g", "s", now=99).valid


def test_identity_modes(monkeypatch):
    monkeypatch.delenv("ASSIGNMENT_SIGNING_KEY", raising=False)
    assert ident.resolve_identity(None, None, None, False).mode == "practice"      # standalone: no setup
    i = ident.resolve_identity("g", "sid1", None, True)
    assert i.mode == "unverified" and i.recorded and "not verified" in i.label
    monkeypatch.setenv("ASSIGNMENT_SIGNING_KEY", "k")
    assert ident.resolve_identity("g", "sid1", None, True).mode == "blocked"       # sid in URL alone is not enough
    tok = ident.make_token(b"k", "g", "sid1")
    assert ident.resolve_identity("g", "sid1", tok, True).mode == "signed"
    assert ident.resolve_identity("g", "sid2", tok, True).mode == "blocked"
    assert ident.resolve_identity("g", "sid2", tok, True, practice=True).recorded is False
    assert ident.resolve_identity(None, None, None, False).mode == "practice"      # no game: practice


def test_seal_requires_key():
    assert ident.seal(None, "h", "a") == ""
    s = ident.seal(b"k", "hash", "AA-1")
    assert ident.verify_seal(b"k", "hash", "AA-1", s) and not ident.verify_seal(b"k", "hash2", "AA-1", s)


# ------------------------------------------------------------------ workflow / completion
def test_workflow_requirements_and_reasons():
    s = scn(SEASONAL)
    pg = make_pg(s, complete=False)
    miss = W.missing_requirements(pg, s.config_hash)
    assert len(miss) >= 8 and any("chase plan" in m.lower() for m in miss)
    assert W.progress_fraction(pg, s.config_hash) == 0.0
    full = make_pg(s)
    assert W.missing_requirements(full, s.config_hash) == []
    assert W.progress_fraction(full, s.config_hash) == 1.0
    full["student_name"] = ""
    assert any("Student name" in m for m in W.missing_requirements(full, s.config_hash))


def test_stale_plan_completion_after_edit_or_scenario_change():
    s = scn(SEASONAL)
    pg = make_pg(s)
    assert W.plan_complete(pg, "chase", s.config_hash)
    pg["plans"]["chase"]["ws"]["Regular Production"][0] = "999"          # edited after the check
    assert not W.plan_complete(pg, "chase", s.config_hash) and W.plan_stale(pg, "chase", s.config_hash)
    assert "changed the worksheet" in " ".join(W.stage_status(pg, s.config_hash, "chase").reasons)
    pg2 = make_pg(s)
    other = scn(SEASONAL, holding_cost=0.9)
    assert not W.plan_complete(pg2, "chase", other.config_hash)


def test_short_writing_blocks_completion_with_specific_reason():
    s = scn(SEASONAL)
    pg = make_pg(s, recommendation="Too short.")
    st = W.stage_status(pg, s.config_hash, "reflect")
    assert not st.done and any("at least 20 words" in r for r in st.reasons)


def test_next_stage_goes_to_first_unfinished_required():
    s = scn(SEASONAL)
    pg = make_pg(s, complete=False)
    assert W.next_stage(pg, s.config_hash, "brief") == "data"
    assert W.next_stage(make_pg(s), s.config_hash, "brief") == "submit"
    assert W.next_stage(make_pg(s), s.config_hash, "submit") is None


def test_json_safe_roundtrip_dataframes_and_nan():
    import pandas as pd
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]}, index=[2, 3])
    out = P.restore_dataframes(P.json_safe({"k": df}))
    assert list(out["k"].index) == [2, 3] and out["k"]["b"].tolist() == ["x", "y"]
