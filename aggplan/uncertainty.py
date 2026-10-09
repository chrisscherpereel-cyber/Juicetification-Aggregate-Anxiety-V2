"""Forecast-error exercise: a plan is COMMITTED against the forecast, then evaluated across
reproducible realized-demand draws that the student never sees while deciding.

Design
  * The forecast shown to the student is `scn.demand`.
  * Realized demand comes from a separate seeded stream (seed + config hash + a salt), so
    it is reproducible (same student, same draws) but cannot be inferred from the forecast
    page. The UI exposes only aggregates after the plan is committed.
  * The committed plan does not react: workforce, production, overtime and subcontract
    quantities are fixed, exactly like a real aggregate plan.
  * Each month's error is  e_t = sigma * ( sqrt(rho) * z_common + sqrt(1-rho) * z_t ),
    truncated at +-3 sigma, so a draw can be a "good year" or a "bad year" overall (rho)
    as well as month-specific noise."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .engine import Decision, run_decision, simulate, summarize
from .scenario import Scenario, stable_int

CORRELATION = 0.3
DEFAULT_DRAWS = 200


def realized_demands(scn: Scenario, n_draws: int = DEFAULT_DRAWS,
                     sigma: Optional[float] = None) -> List[List[float]]:
    sigma = float(scn["forecast_error_pct"]) if sigma is None else float(sigma)
    rng = random.Random(stable_int("realized-demand", scn.base_seed, scn.variant,
                                   scn.config_hash, lo=1, hi=2 ** 31))
    out = []
    for _ in range(n_draws):
        common = max(-3.0, min(3.0, rng.gauss(0, 1)))
        row = []
        for d in scn.demand:
            z = max(-3.0, min(3.0, rng.gauss(0, 1)))
            e = sigma * ((CORRELATION ** 0.5) * common + ((1 - CORRELATION) ** 0.5) * z)
            e = max(-3 * sigma, min(3 * sigma, e))
            row.append(max(0.0, round(d * (1 + e))))
        out.append(row)
    return out


def _pct(sorted_vals: Sequence[float], q: float) -> float:
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass
class CommittedEvaluation:
    n_draws: int
    forecast_cost: float
    forecast_on_time: float
    mean_cost: float
    p10_cost: float
    p90_cost: float
    mean_on_time: float
    p10_on_time: float
    prob_any_shortage: float
    prob_meets_service: Optional[float]
    mean_ending_backlog: float
    mean_ending_inventory: float
    month_shortage_prob: List[float]
    service_requirement: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def evaluate_committed(scn: Scenario, dec: Decision, n_draws: int = DEFAULT_DRAWS,
                       draws: Optional[List[List[float]]] = None) -> CommittedEvaluation:
    """Run the committed decisions against every realized-demand draw."""
    draws = draws if draws is not None else realized_demands(scn, n_draws)
    base = summarize(run_decision(scn, dec), scn, dec.policy)
    costs, ontimes, backlogs, invs, any_short = [], [], [], [], 0
    month_short = [0] * len(scn.months)
    req = float(scn["service_requirement_pct"])
    meets = 0
    for d in draws:
        df = simulate(scn, dec.workers, dec.regular, dec.overtime, dec.subcontract, dec.policy,
                      demand=d)
        s = summarize(df, scn, dec.policy)
        costs.append(s["Total cost"])
        ontimes.append(s["On-time fulfillment"])
        backlogs.append(s["Ending backlog"])
        invs.append(s["Ending inventory"])
        if s["Months with shortages"] > 0:
            any_short += 1
        for i, v in enumerate(df["New Shortage"]):
            if v > 1e-6:
                month_short[i] += 1
        if req > 0 and s["On-time fulfillment"] + 1e-9 >= req:
            meets += 1
    n = len(draws)
    cs, os_ = sorted(costs), sorted(ontimes)
    return CommittedEvaluation(
        n_draws=n, forecast_cost=base["Total cost"], forecast_on_time=base["On-time fulfillment"],
        mean_cost=sum(costs) / n, p10_cost=_pct(cs, 0.10), p90_cost=_pct(cs, 0.90),
        mean_on_time=sum(ontimes) / n, p10_on_time=_pct(os_, 0.10),
        prob_any_shortage=any_short / n, prob_meets_service=(meets / n if req > 0 else None),
        mean_ending_backlog=sum(backlogs) / n, mean_ending_inventory=sum(invs) / n,
        month_shortage_prob=[m / n for m in month_short], service_requirement=req)
