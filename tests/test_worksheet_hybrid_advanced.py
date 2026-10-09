import random

import pytest

from aggplan import benchmark
from aggplan import report as R
from aggplan.engine import (Policy, check_feasibility, is_feasible, reference_plan, requirements_for,
                            run_decision, simulate, summarize)
from aggplan.hybrid import (HybridInputs, auto_assist, compare_hybrid, default_workers,
                            expand_quarterly, reference_under_basis, register_attempt, resolve)
from aggplan.uncertainty import evaluate_committed, realized_demands
from aggplan.worksheet import (EDIT_COLS, WS_COLS, ROW_BASE, blank_worksheet, grade_worksheet,
                               truth_worksheet, col_letter)
from tests.fixtures import make_draft_snapshot, make_pg, make_snapshot
from tests.helpers import SEASONAL, scn


# ------------------------------------------------------------------ worksheet grading
def filled(s, kind, **kw):
    dec, plan = reference_plan(s, kind, **kw)
    truth = truth_worksheet(plan)
    df = blank_worksheet(s)
    for c in EDIT_COLS:
        df[c] = [f"{v:.6f}".rstrip("0").rstrip(".") for v in truth[c]]
    return df, truth


@pytest.mark.parametrize("kind,kw", [("chase", {}), ("chase", {"policy_key": "maintain"}),
                                     ("chase", {"whole": False}), ("level", {}),
                                     ("level", {"maintain": True, "whole": False})])
def test_correct_worksheet_grades_all_ok(kind, kw):
    s = scn(SEASONAL)
    df, truth = filled(s, kind, **kw)
    g = grade_worksheet(df, s, truth, kind=kind, whole=kw.get("whole", True),
                        chase_key=kw.get("policy_key", "use_first"))
    assert g.complete and g.n_wrong == 0 and g.n_ok == g.total


def test_blank_wrong_and_error_statuses_with_symbols():
    s = scn(SEASONAL)
    df, truth = filled(s, "chase")
    df.at[ROW_BASE, "Hires"] = "999"
    df.at[ROW_BASE + 1, "Holding Cost"] = "=B2/0"
    df.at[ROW_BASE + 2, "Layoffs"] = ""
    g = grade_worksheet(df, s, truth, kind="chase", whole=True)
    assert g.status["Hires"][0] == "wrong"
    assert g.status["Holding Cost"][1] == "error" and ("Holding Cost", 1) in g.result.errors
    assert g.status["Layoffs"][2] == "blank" and not g.complete


def test_students_can_use_formulas_including_max_and_roundup():
    s = scn(SEASONAL)
    df, truth = filled(s, "chase")
    r = ROW_BASE
    df.at[r + 4, "Workers"] = f"=ROUNDUP({col_letter('Regular Production')}{r + 4}/RATE)"
    df.at[r + 4, "Ending Inventory"] = (f"=MAX(0,C{r + 4}+E{r + 4}-D{r + 4}-B{r + 4})")
    df.at[r + 4, "Holding Cost"] = f"=J{r + 4}*HOLD"
    g = grade_worksheet(df, s, truth, kind="chase", whole=True)
    assert g.status["Workers"][4] == "ok" and g.status["Ending Inventory"][4] == "ok"
    assert g.status["Holding Cost"][4] == "ok"


def test_chase_workers_graded_against_students_own_production():
    s = scn(SEASONAL)
    df, truth = filled(s, "chase")
    df.at[ROW_BASE + 5, "Regular Production"] = "20000"         # wrong production…
    df.at[ROW_BASE + 5, "Workers"] = "20"                       # …but consistent workers
    g = grade_worksheet(df, s, truth, kind="chase", whole=True)
    assert g.status["Regular Production"][5] == "wrong" and g.status["Workers"][5] == "ok"
    assert not g.complete


def test_worksheet_layout_matches_excel_addressing():
    assert WS_COLS[0] == "Month" and len(WS_COLS) == 17 and col_letter("Total Monthly Cost") == "Q"
    assert list(blank_worksheet(scn(SEASONAL)).index) == list(range(2, 14))


# ------------------------------------------------------------------ hybrid
def test_hybrid_uses_configured_overtime_limit_and_separates_regular_overtime():
    s = scn(SEASONAL, overtime_pct=0.05)
    inp = HybridInputs(quarter_workers=[6, 6, 14, 6], use_overtime=True, auto_overtime=True)
    res = resolve(s, inp)
    assert (res.df["Overtime Production"] <= res.df["Regular Capacity"] * 0.05 + 1e-6).all()
    assert (res.df["Overtime Production"] > 0).any()
    assert res.df["Regular Production"].tolist() == (res.df["Workers"] * 1000).tolist()
    assert res.summary["Total overtime cost"] == pytest.approx(res.df["Overtime Production"].sum() * 4.5)
    assert res.df["Total Monthly Cost"].sum() == pytest.approx(res.summary["Total cost"])
    big = scn(SEASONAL, overtime_pct=0.5)
    assert resolve(big, inp).df["Overtime Production"].sum() > res.df["Overtime Production"].sum()


def test_explicit_overtime_is_what_the_student_typed_and_checked():
    s = scn(SEASONAL)
    inp = HybridInputs(mode="monthly", month_workers=[10] * 12, overtime=[3000] + [0] * 11,
                       use_overtime=True)
    res = resolve(s, inp)
    assert res.df.loc[0, "Overtime Production"] == 3000
    assert not [c for c in res.checks if c["key"] == "overtime_limit"][0]["ok"]       # 3000 > 20% of 10,000
    assert not res.feasible


def test_overtime_ignored_when_not_enabled_and_labelled_assistance_notes():
    s = scn(SEASONAL)
    res = resolve(s, HybridInputs(quarter_workers=[8] * 4, overtime=[500] * 12, use_overtime=False))
    assert res.df["Overtime Production"].sum() == 0
    res2 = resolve(s, HybridInputs(quarter_workers=[8] * 4, use_overtime=True, auto_overtime=True))
    assert any("automatically" in n and "assistance" in n for n in res2.auto_notes)


def test_subcontracting_uses_cost_and_explicit_capacity_limit():
    s = scn(SEASONAL, subcontract_capacity=2000)
    inp = HybridInputs(mode="monthly", month_workers=[8] * 12, subcontract=[2500] * 12, use_subcontract=True)
    res = resolve(s, inp)
    assert res.df["Subcontract Cost"].sum() == pytest.approx(2500 * 12 * 5.25)
    assert not [c for c in res.checks if c["key"] == "subcontract_limit"][0]["ok"]
    auto = resolve(s, HybridInputs(quarter_workers=[6, 6, 6, 6], use_subcontract=True, auto_subcontract=True))
    assert (auto.df["Subcontract Production"] <= 2000 + 1e-6).all() and auto.df["Subcontract Production"].sum() > 0


def test_auto_assist_only_fills_shortages_and_respects_overtime_precondition():
    s = scn(SEASONAL)
    w = [5.0] * 12
    reg = [w_ * 1000 for w_ in w]
    ot, sub = auto_assist(s, w, reg, Policy(), True, False)
    assert max(ot) <= 1000 + 1e-6                          # 20% of 5,000
    idle_reg = [3000.0] * 12                               # idle capacity -> no overtime allowed
    ot2, _ = auto_assist(s, w, idle_reg, Policy(), True, False)
    assert sum(ot2) == 0


def test_feasible_best_is_kept_separate_from_cheaper_infeasible():
    s = scn(SEASONAL)
    state = {}
    cheap = resolve(s, HybridInputs(quarter_workers=[6, 11, 14, 7]))     # found by search: cheaper but infeasible
    good = resolve(s, HybridInputs(quarter_workers=[9, 9, 14, 12], use_overtime=True, auto_overtime=True,
                                   use_subcontract=True, auto_subcontract=True))
    assert good.feasible, [c["label"] for c in good.failed_checks()]
    register_attempt(state, good)
    assert cheap.total_cost < good.total_cost and not cheap.feasible
    register_attempt(state, cheap)
    assert state["best_feasible"]["cost"] == pytest.approx(good.total_cost)
    assert state["lowest_cost_infeasible"]["cost"] == pytest.approx(cheap.total_cost)
    assert state["attempts"] == 2
    again = resolve(s, HybridInputs(quarter_workers=[6, 10, 14, 7]))
    register_attempt(state, again)
    assert state["best_feasible"]["cost"] == pytest.approx(good.total_cost)      # never displaced


def test_hybrid_comparison_uses_identical_assumptions_and_does_not_promise_a_win():
    s = scn(SEASONAL)
    inp = HybridInputs(quarter_workers=[8] * 4, worker_mode="partial", maintain_safety=True)
    res = resolve(s, inp)
    cmp_ = compare_hybrid(s, res)
    refs = reference_under_basis(s, False, True)
    assert refs["Chase"]["policy"] == res.policy.__class__(worker_mode="partial", safety_target=2400.0,
                                                          start_workers=8.0)
    assert refs["Level"]["policy"].worker_mode == "partial" and refs["Level"]["policy"].safety_target == 2400
    assert {r["plan"] for r in cmp_["rows"]} == {"Chase", "Level", "Hybrid"}
    assert "beat" not in cmp_["verdict"].lower() or "than" in cmp_["verdict"].lower()
    assert cmp_["verdict"]


def test_hybrid_monthly_inputs_roundtrip_json():
    inp = HybridInputs(mode="monthly", month_workers=[1.5] * 12, regular=[None] * 11 + [500.0],
                       use_overtime=True, auto_overtime=True)
    back = HybridInputs.from_json(inp.to_json())
    assert back.fingerprint() == inp.fingerprint() and back.regular[-1] == 500.0


def test_expand_quarterly_and_default_workers():
    assert expand_quarterly([1, 2, 3, 4]) == [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]
    assert default_workers(scn(SEASONAL)) == 11


# ------------------------------------------------------------------ uncertainty
def test_realized_demand_is_reproducible_and_separate_from_forecast():
    s = scn(SEASONAL, forecast_error_pct=0.2)
    a, b = realized_demands(s, 50), realized_demands(s, 50)
    assert a == b and a[0] != list(s.demand)
    other = scn(SEASONAL, forecast_error_pct=0.2, seed=99)
    assert realized_demands(other, 50) != a            # different student => different draws
    mean = [sum(col) / 50 for col in zip(*a)]
    assert all(abs(m - d) / d < 0.2 for m, d in zip(mean, s.demand))


def test_committed_plan_is_evaluated_without_reacting_and_is_riskier_than_forecast():
    s = scn(SEASONAL, forecast_error_pct=0.25)
    dec, _ = reference_plan(s, "chase", whole=True)
    ev = evaluate_committed(s, dec, n_draws=100)
    assert ev.n_draws == 100 and 0 <= ev.prob_any_shortage <= 1
    assert ev.prob_any_shortage > 0.5                      # chase has zero cushion: demand above forecast = shortage
    assert ev.p90_cost >= ev.p10_cost
    assert ev.forecast_on_time == 100.0 and ev.mean_on_time < 100.0
    safe, _ = reference_plan(s, "chase", policy_key="maintain")
    ev2 = evaluate_committed(s, safe, n_draws=100)
    assert len(ev2.month_shortage_prob) == 12


# ------------------------------------------------------------------ benchmark
@pytest.mark.skipif(not benchmark.available(), reason="SciPy not installed")
def test_benchmark_matches_student_model_and_is_not_beaten_by_random_feasible_plans():
    s = scn(SEASONAL)
    r = benchmark.solve(s, whole=True, use_overtime=True, use_subcontract=True)
    assert r.status == "optimal" and r.cost is not None
    req = requirements_for(s, Policy(), True, True)
    assert is_feasible(check_feasibility(r.df, req))                 # accepted by the student's checker
    assert r.cost <= r.solver_objective + 1e-3
    for kind, kw in (("chase", {}), ("chase", {"whole": False})):
        _, df = reference_plan(s, kind, **kw)
        assert r.cost <= df["Total Monthly Cost"].sum() + 1e-6 or kw.get("whole") is False
    rng = random.Random(3)
    tried = feas = 0
    while feas < 40 and tried < 3000:
        tried += 1
        q = [rng.randint(5, 16) for _ in range(4)]
        res = resolve(s, HybridInputs(quarter_workers=q, use_overtime=True, auto_overtime=True,
                                      use_subcontract=True, auto_subcontract=True))
        if res.feasible:
            feas += 1
            assert res.total_cost >= r.cost - 1e-6, (q, res.total_cost, r.cost)
    assert feas >= 5


@pytest.mark.skipif(not benchmark.available(), reason="SciPy not installed")
def test_benchmark_partial_workers_is_no_worse_than_whole_and_respects_safety_and_infeasibility():
    s = scn(SEASONAL)
    whole = benchmark.solve(s, whole=True)
    part = benchmark.solve(s, whole=False)
    assert part.cost <= whole.cost + 1e-6
    m = benchmark.solve(s, whole=True, maintain=True)
    assert m.status in ("optimal", "feasible") and m.df["Ending Inventory"].min() >= 2400 - 1e-6
    assert m.cost >= whole.cost - 1e-6
    impossible = benchmark.solve(scn(SEASONAL, max_inventory=500), whole=True, maintain=True)
    assert impossible.status == "infeasible" and impossible.cost is None
    limited = benchmark.solve(scn(SEASONAL, overtime_pct=0.0), whole=True, use_overtime=True)
    assert limited.df["Overtime Production"].sum() == 0


# ------------------------------------------------------------------ report snapshot
def test_final_snapshot_is_consistent_with_engine_totals():
    s = scn(SEASONAL)
    snap = make_snapshot(s)
    assert snap["status"] == "Final" and snap["missing"] == []
    for kind in ("chase", "level"):
        st = snap["plans"][kind]
        dec, df = reference_plan(s, kind, policy_key="use_first", whole=True, maintain=False)
        assert st["summary"]["Total cost"] == pytest.approx(df["Total Monthly Cost"].sum())
        assert sum(r["Total Monthly Cost"] for r in st["monthly"]) == pytest.approx(st["summary"]["Total cost"])
    assert snap["strategy"]["justification"].startswith("I recommend")           # verbatim
    assert snap["integrity"]["snapshot_hash"] == R.snapshot_hash(snap)
    assert "grade" not in snap["plan_cost_note"].lower().replace("not an academic grade", "")


def test_draft_snapshot_never_contains_unverified_plan_data():
    snap = make_draft_snapshot()
    assert snap["status"] == "Draft" and snap["missing"]
    assert snap["plans"]["chase"] is None and snap["plans"]["level"] is None
    assert snap["hybrid"] is None


def test_snapshot_hash_changes_when_content_changes_and_has_no_secrets():
    a, b = make_snapshot(), make_snapshot(reflections=["x " * 20, "y " * 20, "z " * 20])
    assert a["integrity"]["snapshot_hash"] != b["integrity"]["snapshot_hash"]
    blob = str(a).lower()
    assert "sid=" not in blob and "token" not in blob and "secret" not in blob


def test_safe_filename_pattern_and_sanitising():
    assert R.safe_filename("Ada Lovelace", "AA-1234ABCD") == "Aggregate_Anxiety_Lovelace_Ada_AA1234ABCD.pdf"
    assert R.safe_filename("Lovelace, Ada", "AA-1") == "Aggregate_Anxiety_Lovelace_Ada_AA1.pdf"
    f = R.safe_filename("../../etc/passwd <b>Zoë</b> O'Brien", "AA-9")
    assert f.endswith(".pdf") and "/" not in f and "\\" not in f and "<" not in f and ".." not in f
    assert R.safe_filename("", "AA-1") == "Aggregate_Anxiety_Student_Unnamed_AA1.pdf"


def test_timezone_label_is_aware():
    t = R.now_in_zone("America/Phoenix")
    assert t.tzinfo is not None and "UTC-07:00" in R.format_time(t)
    assert R.now_in_zone("Not/AZone").tzinfo is not None
