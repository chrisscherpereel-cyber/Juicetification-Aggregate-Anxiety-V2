import math

import pytest

from aggplan.engine import (simulate, summarize, reference_chase, reference_level, level_rate,
                            run_decision, reference_plan, Policy, COST_COLUMNS,
                            check_feasibility, requirements_for, is_feasible, assumption_gaps)
from tests.helpers import scn, SEASONAL, FLAT

TOL = 1e-6


def conservation(df, scn_):
    """Per-month physical-inventory and backlog conservation."""
    inv_prev = scn_["beginning_inventory"]
    bk_prev = 0.0
    for _, r in df.iterrows():
        assert r["Beginning Inventory"] == pytest.approx(inv_prev, abs=TOL)
        assert r["Beginning Backlog"] == pytest.approx(bk_prev, abs=TOL)
        # stock identity
        assert (r["Beginning Inventory"] + r["Total Production"]
                == pytest.approx(r["On-Time Shipments"] + r["Backlog Cleared"]
                                 + r["Ending Inventory"] + r["Disposed"], abs=TOL))
        # backlog identity
        assert (r["Backorders"] == pytest.approx(r["Beginning Backlog"] - r["Backlog Cleared"]
                                                  + r["New Shortage"], abs=TOL))
        # demand identity
        assert r["Forecast Demand"] == pytest.approx(r["On-Time Shipments"] + r["New Shortage"],
                                                     abs=TOL)
        assert r["Ending Inventory"] >= -TOL and r["Backorders"] >= -TOL
        # never hold stock while customers wait
        assert not (r["Ending Inventory"] > TOL and r["Backorders"] > TOL)
        inv_prev, bk_prev = r["Ending Inventory"], r["Backorders"]


@pytest.mark.parametrize("kind,kw", [("chase", {}), ("chase", {"policy_key": "maintain"}),
                                     ("chase", {"policy_key": "no_fire_first"}),
                                     ("chase", {"whole": False}), ("level", {}),
                                     ("level", {"maintain": True}), ("level", {"whole": False})])
def test_inventory_and_backlog_conservation(kind, kw):
    s = scn(SEASONAL)
    dec, df = reference_plan(s, kind, **kw)
    conservation(df, s)
    net = s["beginning_inventory"] + df["Total Production"].sum() - df["Forecast Demand"].sum()
    assert net == pytest.approx(df.iloc[-1]["Ending Inventory"] - df.iloc[-1]["Backorders"],
                                abs=TOL)


def test_backlog_carries_forward_and_is_cleared_by_later_production():
    s = scn([10000, 5000] + [0] * 10, beginning_inventory=0)
    df = simulate(s, workers=[0, 20] + [0] * 10, regular=[0, 20000] + [0] * 10,
                  policy=Policy(start_workers=0))
    assert df.loc[0, "Backorders"] == 10000 and df.loc[0, "New Shortage"] == 10000
    assert df.loc[1, "Beginning Backlog"] == 10000
    assert df.loc[1, "Backlog Cleared"] == 10000        # old orders first
    assert df.loc[1, "On-Time Shipments"] == 5000       # then this month's demand
    assert df.loc[1, "Backorders"] == 0 and df.loc[1, "Ending Inventory"] == 5000
    conservation(df, s)


def test_backlog_clears_oldest_first_before_current_demand():
    s = scn([100, 100] + [0] * 10, beginning_inventory=0)
    df = simulate(s, [1] * 12, [0, 150] + [0] * 10)
    assert df.loc[1, "Backlog Cleared"] == 100
    assert df.loc[1, "On-Time Shipments"] == 50
    assert df.loc[1, "Backorders"] == 50
    assert summarize(df, s)["On-time fulfillment"] == pytest.approx(25.0)


def test_beginning_inventory_different_from_safety_stock():
    """B != S: a maintain plan builds to S (B<S) or spends the excess (B>S), and the level
    plan ends at S in either case. Legacy code held B all year."""
    for B, S in [(2400, 3600), (5000, 2400), (0, 3000)]:
        s = scn(SEASONAL, beginning_inventory=B, safety_stock=S)
        dec = reference_chase(s, "maintain", True)
        df = run_decision(s, dec)
        assert dec.policy.safety_target == S
        assert df["Ending Inventory"].round(6).eq(S).all()
        assert df.iloc[0]["Regular Production"] == pytest.approx(SEASONAL[0] + S - B)
        conservation(df, s)
        lvl = reference_level(s, True, True)
        ldf = run_decision(s, lvl)
        assert ldf.iloc[-1]["Ending Inventory"] == pytest.approx(S, abs=TOL)
        assert ldf.iloc[-1]["Backorders"] == 0
        conservation(ldf, s)
        assert level_rate(s, True) == pytest.approx((sum(SEASONAL) + S - B) / 12)


def test_chase_without_safety_ends_at_zero_with_no_backlog():
    s = scn(SEASONAL)
    for key in ("use_first", "no_fire_first"):
        df = run_decision(s, reference_chase(s, key, True))
        assert df.iloc[-1]["Ending Inventory"] == 0 and df["Backorders"].eq(0).all()
        assert summarize(df, s)["On-time fulfillment"] == 100.0


def test_whole_workers_leave_paid_idle_capacity():
    s = scn(SEASONAL)
    df = run_decision(s, reference_chase(s, "use_first", True))
    assert all(float(w).is_integer() for w in df["Workers"])
    assert (df["Regular Capacity"] >= df["Regular Production"] - TOL).all()
    assert df["Idle Capacity"].sum() > 0
    assert df["Regular Labor Cost"].sum() == pytest.approx(
        df["Workers"].sum() * s["regular_labor_cost"])


def test_fractional_workforce_uses_worker_equivalents_and_hour_rates():
    s = scn(SEASONAL)
    df = run_decision(s, reference_chase(s, "use_first", False))
    assert (df["Idle Capacity"] < TOL).all()
    hours = df["Workers"] * s.hpd
    prev = [s["starting_workforce"] * s.hpd] + list(hours[:-1])
    exp_hires = sum(max(0, h - p) for h, p in zip(hours, prev))
    assert df["Hires"].sum() == pytest.approx(exp_hires)
    assert df["Hiring Cost"].sum() == pytest.approx(exp_hires * s["hiring_cost"] / s.hpd)
    assert df["Paid Labor Hours/Day"].iloc[3] == pytest.approx(df["Workers"].iloc[3] * s.hpd)


def test_whole_vs_partial_level_rate_rounding():
    s = scn(SEASONAL)
    w = reference_level(s, True, False)
    p = reference_level(s, False, False)
    assert w.workers[0] == math.ceil(p.workers[0] - 1e-9)
    assert w.regular[0] == p.regular[0]


def test_zero_demand():
    s = scn([0] * 12)
    for kind, kw in [("chase", {}), ("chase", {"policy_key": "maintain"}), ("level", {}),
                     ("level", {"maintain": True}), ("level", {"whole": False})]:
        dec, df = reference_plan(s, kind, **kw)
        conservation(df, s)
        sm = summarize(df, s, dec.policy)
        assert sm["On-time fulfillment"] == 100.0 and sm["Ending backlog"] == 0
        assert math.isfinite(sm["Total cost"])
    df = run_decision(s, reference_chase(s, "use_first", True))
    assert df.loc[0, "Layoffs"] == s["starting_workforce"]


def test_cost_reconciliation_monthly_and_total():
    for kind, kw in [("chase", {}), ("level", {"maintain": True}), ("chase", {"whole": False})]:
        s = scn(SEASONAL)
        dec, df = reference_plan(s, kind, **kw)
        monthly = df[COST_COLUMNS].sum(axis=1)
        assert monthly.tolist() == pytest.approx(df["Total Monthly Cost"].tolist(), abs=1e-6)
        sm = summarize(df, s, dec.policy)
        cats = sum(sm[k] for k in ("Total regular labor cost", "Total hiring cost",
                                    "Total layoff cost", "Total overtime cost",
                                    "Total subcontract cost", "Total holding cost",
                                    "Total backorder cost", "Total disposal cost"))
        assert cats == pytest.approx(sm["Total cost"], abs=0.01)
        assert sm["Total cost"] == pytest.approx(df["Total Monthly Cost"].sum(), abs=1e-6)


def test_cost_units_hand_calculation():
    s = scn([400] + [0] * 11, beginning_inventory=0, starting_workforce=0)
    df = simulate(s, [1] * 12, [1000] + [0] * 11, policy=Policy(start_workers=0))
    r = df.iloc[0]
    assert r["Regular Labor Cost"] == 3200 and r["Hiring Cost"] == 600
    assert r["Holding Cost"] == pytest.approx(600 * 0.25)
    assert df.iloc[1]["Layoffs"] == 0


def test_overtime_and_subcontract_costs_in_monthly_rows_and_total():
    s = scn([20000] + [0] * 11, beginning_inventory=0, starting_workforce=10)
    df = simulate(s, [10] * 12, [10000] + [0] * 11, overtime=[2000] + [0] * 11,
                  subcontract=[3000] + [0] * 11, policy=Policy(start_workers=10))
    r = df.iloc[0]
    assert r["Overtime Cost"] == 2000 * 4.5 and r["Subcontract Cost"] == 3000 * 5.25
    assert r["Regular Production"] == 10000 and r["Total Production"] == 15000
    assert r["Total Monthly Cost"] == pytest.approx(
        r["Regular Labor Cost"] + r["Overtime Cost"] + r["Subcontract Cost"] + r["Holding Cost"]
        + r["Backorder Cost"] + r["Hiring Cost"] + r["Layoff Cost"])
    assert summarize(df, s)["Total overtime bottles"] == 2000


def test_summary_metric_definitions_and_terminal_fields():
    s = scn(SEASONAL)
    dec, df = reference_plan(s, "level")
    sm = summarize(df, s, dec.policy)
    assert sm["Months with shortages"] == 5
    assert sm["On-time fulfillment"] == pytest.approx(
        100 * df["On-Time Shipments"].sum() / sum(SEASONAL))
    assert sm["Ending backlog"] == 0 and sm["Demand fulfilled by year end"] == 100.0
    assert "Service level" not in sm


def test_feasibility_checks_terminal_storage_and_service():
    s = scn(SEASONAL, max_inventory=12000)
    dec, df = reference_plan(s, "level")
    chk = {c["key"]: c for c in check_feasibility(df, requirements_for(s, dec.policy))}
    assert not chk["storage"]["ok"]
    assert not chk["service"]["ok"]
    assert chk["terminal_backlog"]["ok"] and chk["terminal_inventory"]["ok"]
    dec, df = reference_plan(s, "chase")
    assert is_feasible(check_feasibility(df, requirements_for(s, dec.policy)))
    df2 = simulate(s, [0] * 12, [0] * 12)
    chk = {c["key"]: c for c in check_feasibility(df2, requirements_for(s, Policy()))}
    assert not chk["terminal_backlog"]["ok"]


def test_overtime_rules_in_feasibility():
    s = scn([30000] * 12, overtime_pct=0.2)
    base = dict(workers=[10] * 12, regular=[10000] * 12)

    def keys(df):
        return {c["key"]: c["ok"] for c in check_feasibility(df, requirements_for(s, Policy()))}

    ok = keys(simulate(s, overtime=[2000] * 12, **base))
    assert ok["overtime_limit"] and ok["overtime_after_regular"]
    assert not keys(simulate(s, overtime=[2500] * 12, **base))["overtime_limit"]
    idle = keys(simulate(s, workers=[10] * 12, regular=[5000] * 12, overtime=[1000] * 12))
    assert not idle["overtime_after_regular"]


def test_configured_overtime_pct_is_used_not_hardcoded():
    assert scn(SEASONAL, overtime_pct=0.05).ot_limit_per_worker == pytest.approx(50)
    assert scn(SEASONAL, overtime_pct=0.20).ot_limit_per_worker == pytest.approx(200)


def test_assumption_gap_notes():
    s = scn(SEASONAL)
    c = reference_chase(s, "use_first", True).policy
    l = reference_level(s, False, True).policy
    notes = assumption_gaps({"Chase": c, "Level": l}, s)
    assert any("Worker models differ" in n for n in notes)
    assert any("Safety-stock" in n for n in notes)
    assert assumption_gaps({"A": c, "B": c}, s) == []


def test_perishability_cohorts_dispose_and_conserve():
    s = scn([1000] * 12, beginning_inventory=0, starting_workforce=0)
    df = simulate(s, [3] + [0] * 11, [3000] + [0] * 11, policy=Policy(start_workers=0),
                  shelf_life=2)
    assert df.loc[0, "On-Time Shipments"] == 1000 and df.loc[1, "On-Time Shipments"] == 1000
    assert df.loc[1, "Disposed"] == 1000
    assert df.loc[1, "Ending Inventory"] == 0
    assert df.loc[2, "New Shortage"] == 1000
    assert df.loc[1, "Disposal Cost"] == 1000 * s["disposal_cost"]
    conservation(df, s)
    df0 = simulate(s, [3] + [0] * 11, [3000] + [0] * 11, policy=Policy(start_workers=0))
    assert df0.loc[2, "On-Time Shipments"] == 1000 and df0["Disposed"].sum() == 0


def test_simulate_rejects_wrong_length():
    with pytest.raises(ValueError):
        simulate(scn(SEASONAL), [1] * 11, [1] * 12)


def test_explicit_scenarios_do_not_share_state():
    a, b = scn(SEASONAL), scn(FLAT)
    ta = summarize(run_decision(a, reference_chase(a)), a)["Total cost"]
    summarize(run_decision(b, reference_chase(b)), b)
    assert summarize(run_decision(a, reference_chase(a)), a)["Total cost"] == ta
