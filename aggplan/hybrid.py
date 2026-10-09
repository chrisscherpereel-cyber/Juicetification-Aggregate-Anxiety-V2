"""Hybrid design challenge: student-chosen workforce (quarterly or monthly), optional
overtime and subcontracting, evaluated by the same engine and held to the same feasibility
requirements as the reference plans.

Nothing here promises that a hybrid can beat chase or level. When assumptions differ, the
comparison helpers say so instead of declaring a winner."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .engine import (EPS, Policy, check_feasibility, is_feasible, reference_chase,
                     reference_level, requirements_for, run_decision, simulate, summarize,
                     assumption_gaps)
from .scenario import Scenario


@dataclass
class HybridInputs:
    mode: str = "quarterly"                     # "quarterly" | "monthly"
    quarter_workers: Sequence[float] = (0, 0, 0, 0)
    month_workers: Sequence[float] = ()
    regular: Sequence[Optional[float]] = ()     # None/blank => run at full capacity
    overtime: Sequence[Optional[float]] = ()    # explicit student quantities
    subcontract: Sequence[Optional[float]] = ()
    use_overtime: bool = False                  # advanced decision enabled
    use_subcontract: bool = False
    auto_overtime: bool = False                 # ASSISTANCE: fill shortages automatically
    auto_subcontract: bool = False
    worker_mode: str = "whole"
    maintain_safety: bool = False

    def to_json(self) -> dict:
        return {"mode": self.mode, "quarter_workers": list(self.quarter_workers),
                "month_workers": list(self.month_workers),
                "regular": [None if x is None else float(x) for x in self.regular],
                "overtime": [None if x is None else float(x) for x in self.overtime],
                "subcontract": [None if x is None else float(x) for x in self.subcontract],
                "use_overtime": self.use_overtime, "use_subcontract": self.use_subcontract,
                "auto_overtime": self.auto_overtime, "auto_subcontract": self.auto_subcontract,
                "worker_mode": self.worker_mode, "maintain_safety": self.maintain_safety}

    @staticmethod
    def from_json(d: dict) -> "HybridInputs":
        return HybridInputs(
            mode=d.get("mode", "quarterly"), quarter_workers=tuple(d.get("quarter_workers", (0,) * 4)),
            month_workers=tuple(d.get("month_workers", ())), regular=tuple(d.get("regular", ())),
            overtime=tuple(d.get("overtime", ())), subcontract=tuple(d.get("subcontract", ())),
            use_overtime=bool(d.get("use_overtime")), use_subcontract=bool(d.get("use_subcontract")),
            auto_overtime=bool(d.get("auto_overtime")), auto_subcontract=bool(d.get("auto_subcontract")),
            worker_mode=d.get("worker_mode", "whole"), maintain_safety=bool(d.get("maintain_safety")))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class HybridResult:
    inputs: HybridInputs
    policy: Policy
    df: pd.DataFrame
    summary: dict
    checks: List[dict]
    feasible: bool
    overtime: List[float]
    subcontract: List[float]
    auto_notes: List[str] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return self.summary["Total cost"]

    def failed_checks(self) -> List[dict]:
        return [c for c in self.checks if not c["ok"]]


def expand_quarterly(q: Sequence[float], n: int = 12) -> List[float]:
    return [float(q[min(3, i // (n // 4))]) for i in range(n)]


def default_workers(scn: Scenario, whole: bool = True) -> float:
    """A sensible starting crew: the level-plan crew (the student then adjusts)."""
    return reference_level(scn, whole, False).workers[0]


def hybrid_policy(scn: Scenario, inp: HybridInputs) -> Policy:
    return Policy(worker_mode=inp.worker_mode,
                  safety_target=float(scn["safety_stock"]) if inp.maintain_safety else 0.0,
                  start_workers=float(scn["starting_workforce"]))


def _monthly_workers(inp: HybridInputs, n: int) -> List[float]:
    if inp.mode == "monthly" and len(inp.month_workers) == n:
        return [float(x) for x in inp.month_workers]
    return expand_quarterly(inp.quarter_workers, n)


def auto_assist(scn: Scenario, workers: Sequence[float], regular: Sequence[float],
                policy: Policy, use_ot: bool, use_sub: bool,
                fixed_ot: Sequence[float] = (), fixed_sub: Sequence[float] = ()):
    """ASSISTANCE option: month by month, add overtime (within the configured limit and only
    when regular capacity is fully used) and then subcontracting (within its capacity) to
    cover shortages — i.e. to ship this month's demand and clear backlog. It never targets
    safety stock or minimises cost; it is a convenience, not a recommendation."""
    n = len(workers)
    ot = [0.0] * n
    sub = [0.0] * n
    inv, bk = float(scn["beginning_inventory"]), 0.0
    for i in range(n):
        d = float(scn.demand[i])
        cap = workers[i] * scn.cap_month
        reg = regular[i]
        avail = inv + reg
        need = max(0.0, bk + d - avail)
        if use_ot and need > EPS and reg >= cap - EPS:
            ot[i] = min(need, scn.ot_limit_per_worker * workers[i])
            need -= ot[i]
        if use_sub and need > EPS:
            sub[i] = min(need, float(scn["subcontract_capacity"]))
        avail += ot[i] + sub[i]
        cleared = min(bk, avail)
        rem = avail - cleared
        ontime = min(d, rem)
        inv, bk = rem - ontime, bk - cleared + d - ontime
    return ot, sub


def resolve(scn: Scenario, inp: HybridInputs) -> HybridResult:
    n = len(scn.months)
    pol = hybrid_policy(scn, inp)
    workers = _monthly_workers(inp, n)
    cap = [w * scn.cap_month for w in workers]
    reg = []
    for i in range(n):
        v = inp.regular[i] if i < len(inp.regular) else None
        reg.append(cap[i] if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
    notes: List[str] = []
    ot = [0.0] * n
    sub = [0.0] * n
    if inp.use_overtime:
        for i in range(n):
            v = inp.overtime[i] if i < len(inp.overtime) else None
            ot[i] = 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    if inp.use_subcontract:
        for i in range(n):
            v = inp.subcontract[i] if i < len(inp.subcontract) else None
            sub[i] = 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    if (inp.use_overtime and inp.auto_overtime) or (inp.use_subcontract and inp.auto_subcontract):
        aot, asub = auto_assist(scn, workers, reg, pol,
                                inp.use_overtime and inp.auto_overtime,
                                inp.use_subcontract and inp.auto_subcontract)
        if inp.use_overtime and inp.auto_overtime:
            ot = [max(a, b) for a, b in zip(ot, aot)]
            notes.append("Overtime quantities were filled in automatically (assistance option): "
                         f"{sum(aot):,.0f} bottles.")
        if inp.use_subcontract and inp.auto_subcontract:
            sub = [max(a, b) for a, b in zip(sub, asub)]
            notes.append("Subcontract quantities were filled in automatically (assistance "
                         f"option): {sum(asub):,.0f} bottles.")
    df = simulate(scn, workers, reg, ot, sub, pol)
    req = requirements_for(scn, pol, overtime_allowed=inp.use_overtime,
                           subcontract_allowed=inp.use_subcontract)
    checks = check_feasibility(df, req)
    return HybridResult(inp, pol, df, summarize(df, scn, pol), checks, is_feasible(checks),
                        ot, sub, notes)


# --------------------------------------------------------------------------- #
# Attempt tracking: best FEASIBLE plan is stored separately from cheaper infeasible ones
# --------------------------------------------------------------------------- #
def _record(res: HybridResult, label: str = "") -> dict:
    return {"cost": res.total_cost, "feasible": res.feasible, "inputs": res.inputs.to_json(),
            "fingerprint": res.inputs.fingerprint(),
            "on_time": res.summary["On-time fulfillment"],
            "ending_backlog": res.summary["Ending backlog"],
            "failed": [c["label"] for c in res.failed_checks()], "label": label}


def register_attempt(state: dict, res: HybridResult) -> dict:
    """Update the persisted hybrid state with one explicit attempt. `state` is a plain dict:
        attempts (count), history (last 20 records), best_feasible, lowest_cost_infeasible.
    A cheaper infeasible plan never displaces the best feasible plan."""
    rec = _record(res)
    state["attempts"] = int(state.get("attempts", 0)) + 1
    hist = list(state.get("history", []))
    if not any(h["fingerprint"] == rec["fingerprint"] for h in hist):
        hist.append(rec)
    state["history"] = hist[-20:]
    if res.feasible:
        best = state.get("best_feasible")
        if not best or rec["cost"] < best["cost"] - 1e-9:
            state["best_feasible"] = rec
    else:
        low = state.get("lowest_cost_infeasible")
        if not low or rec["cost"] < low["cost"] - 1e-9:
            state["lowest_cost_infeasible"] = rec
    return state


# --------------------------------------------------------------------------- #
# Like-for-like comparison
# --------------------------------------------------------------------------- #
def reference_under_basis(scn: Scenario, whole: bool, maintain: bool) -> Dict[str, dict]:
    """Chase and level re-run under ONE shared basis (worker model, safety policy,
    terminal requirement) — the fair benchmark for a hybrid built on that basis."""
    out = {}
    for label, dec in (("Chase", reference_chase(scn, "maintain" if maintain else "use_first", whole)),
                       ("Level", reference_level(scn, whole, maintain))):
        df = run_decision(scn, dec)
        req = requirements_for(scn, dec.policy, overtime_allowed=False, subcontract_allowed=False)
        checks = check_feasibility(df, req)
        out[label] = {"df": df, "policy": dec.policy, "summary": summarize(df, scn, dec.policy),
                      "checks": checks, "feasible": is_feasible(checks)}
    return out


def compare_hybrid(scn: Scenario, res: HybridResult) -> dict:
    """Compare the hybrid with chase/level built under the hybrid's own assumptions."""
    refs = reference_under_basis(scn, res.policy.whole, res.policy.maintains_safety)
    rows = []
    for label, r in refs.items():
        rows.append({"plan": label, "cost": r["summary"]["Total cost"], "feasible": r["feasible"],
                     "on_time": r["summary"]["On-time fulfillment"]})
    rows.append({"plan": "Hybrid", "cost": res.total_cost, "feasible": res.feasible,
                 "on_time": res.summary["On-time fulfillment"]})
    feas_refs = [r["cost"] for r in rows[:2] if r["feasible"]]
    verdict = ""
    if res.feasible and feas_refs:
        best_ref = min(feas_refs)
        diff = best_ref - res.total_cost
        verdict = (f"Your hybrid costs {abs(diff):,.0f} {'less' if diff > 0 else 'more'} than the "
                   "cheaper feasible reference plan under the same assumptions."
                   if abs(diff) > 0.5 else "Your hybrid costs the same as the cheaper feasible reference plan.")
    elif res.feasible:
        verdict = "Neither reference plan meets every requirement under these assumptions; compare them on the metrics, not just cost."
    else:
        verdict = "Your hybrid does not yet meet every requirement, so its cost is not comparable yet."
    return {"rows": rows, "refs": refs, "verdict": verdict}


def explain_basis_mismatch(scn: Scenario, hybrid_policy_: Policy, chase_policy_: Policy,
                           level_policy_: Policy) -> List[str]:
    return assumption_gaps({"Chase": chase_policy_, "Level": level_policy_, "Hybrid": hybrid_policy_}, scn)
