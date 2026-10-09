"""Optional optimization benchmark (mixed-integer linear program) that matches the student
model's constraints. It needs SciPy >= 1.9 (`scipy.optimize.milp`); without SciPy the
benchmark is simply unavailable.

Model (12 months; every constraint mirrors engine.check_feasibility):
  W_t   workforce (INTEGER in whole-worker mode, continuous in partial mode)
  H_t, L_t   hires / layoffs   W_t - W_{t-1} = H_t - L_t
  P_t   regular production <= capacity*W_t
  O_t   overtime <= ot_pct*capacity*W_t, allowed only when regular is at capacity (binary y_t)
  S_t   subcontract <= subcontract capacity
  I_t, B_t  physical inventory / backlog; c_t backlog cleared; u_t on-time shipments
        I_t = I_{t-1} + P_t+O_t+S_t - c_t - u_t        B_t = B_{t-1} - c_t + D_t - u_t
        c_t + u_t <= I_{t-1} + P_t+O_t+S_t,  u_t <= D_t,  c_t <= B_{t-1}
        oldest-first rule: u_t > 0 only if all old backlog is cleared (binary z_t)
        no stock while backlog exists (binary w_t) — the engine always ships what it can
  storage I_t <= max inventory; safety stock I_t >= S when the plan maintains it;
  on-time fulfillment sum(u) >= service% * sum(D); terminal I_12 >= target, B_12 <= max.
  Minimise labor + hiring + layoff + overtime + subcontract + holding + backorder cost.

The result is then RE-SIMULATED with the student's own engine, and that simulation (not the
solver's objective) is reported, so the benchmark is a plan the student's model accepts.

Status wording (important for teaching):
  'optimal'     -> the solver proved no cheaper plan exists within the stated tolerance;
  'feasible'    -> a good plan was found but optimality was NOT proven (time/node limit);
  'infeasible'  -> no plan satisfies all constraints;
  'unavailable' -> SciPy missing or the exercise uses features the model does not cover
                   (perishability)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .engine import (Policy, check_feasibility, is_feasible, requirements_for, simulate,
                     summarize)
from .scenario import Scenario

TIME_LIMIT = 20.0


@dataclass
class BenchmarkResult:
    status: str                      # optimal | feasible | infeasible | unavailable | error
    message: str
    cost: Optional[float] = None
    gap: Optional[float] = None
    workers: List[float] = field(default_factory=list)
    regular: List[float] = field(default_factory=list)
    overtime: List[float] = field(default_factory=list)
    subcontract: List[float] = field(default_factory=list)
    summary: Optional[dict] = None
    checks: Optional[list] = None
    df: Optional[object] = None
    solver_objective: Optional[float] = None


def available() -> bool:
    try:
        from scipy.optimize import milp  # noqa: F401
        return True
    except Exception:
        return False


def solve(scn: Scenario, *, whole: bool = True, maintain: bool = False,
          use_overtime: bool = True, use_subcontract: bool = False,
          time_limit: float = TIME_LIMIT) -> BenchmarkResult:
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import lil_matrix
    except Exception as e:                                   # pragma: no cover
        return BenchmarkResult("unavailable", f"SciPy is not installed ({e}).")

    p = scn.params
    n = len(scn.months)
    D = [float(x) for x in scn.demand]
    cap = scn.cap_month
    big_w = max(60.0, float(np.ceil(sum(D) / cap)) + 5, float(p["starting_workforce"]) + 5)
    big_b = sum(D) + float(p["beginning_inventory"]) + 1.0
    big_p = cap * big_w
    target = float(p["safety_stock"]) if maintain else 0.0
    otp = float(p["overtime_pct"]) if use_overtime else 0.0
    subcap = float(p["subcontract_capacity"]) if use_subcontract else 0.0
    svc = float(p["service_requirement_pct"]) / 100.0

    names = ["W", "H", "L", "P", "O", "S", "I", "B", "c", "u", "y", "z", "w"]
    idx = {nm: [k * n + t for t in range(n)] for k, nm in enumerate(names)}
    nv = len(names) * n
    lb = np.zeros(nv)
    ub = np.full(nv, np.inf)
    integ = np.zeros(nv)
    for t in range(n):
        ub[idx["W"][t]] = big_w
        ub[idx["P"][t]] = big_p
        ub[idx["O"][t]] = big_p * otp
        ub[idx["S"][t]] = subcap
        ub[idx["I"][t]] = float(p["max_inventory"])
        ub[idx["u"][t]] = D[t]
        ub[idx["y"][t]] = 1
        ub[idx["z"][t]] = 1
        ub[idx["w"][t]] = 1
        integ[idx["w"][t]] = 1
        integ[idx["y"][t]] = 1
        integ[idx["z"][t]] = 1
        if whole:
            integ[idx["W"][t]] = 1
        if target > 0:
            lb[idx["I"][t]] = target
    ub[idx["B"][n - 1]] = float(p["terminal_backlog_max"])
    lb[idx["I"][n - 1]] = max(lb[idx["I"][n - 1]], target)

    rows, lo, hi = [], [], []

    def add(coefs, low, high):
        rows.append(coefs)
        lo.append(low)
        hi.append(high)

    w0 = float(p["starting_workforce"])
    b0 = float(p["beginning_inventory"])
    big_o = big_p * otp
    for t in range(n):
        first = t == 0
        Wp = {} if first else {idx["W"][t - 1]: -1.0}
        Ip = {} if first else {idx["I"][t - 1]: -1.0}
        Bp = {} if first else {idx["B"][t - 1]: -1.0}
        prod = {idx["P"][t]: -1.0, idx["O"][t]: -1.0, idx["S"][t]: -1.0}
        # workforce flow: W_t - W_{t-1} = H_t - L_t   (W_{-1} = starting workforce)
        add({idx["W"][t]: 1.0, idx["H"][t]: -1.0, idx["L"][t]: 1.0, **Wp}, w0 if first else 0.0,
            w0 if first else 0.0)
        # regular production within capacity
        add({idx["P"][t]: 1.0, idx["W"][t]: -cap}, -np.inf, 0.0)
        # overtime within its limit, and only when regular capacity is fully used (y_t = 1)
        add({idx["O"][t]: 1.0, idx["W"][t]: -cap * otp}, -np.inf, 0.0)
        add({idx["O"][t]: 1.0, idx["y"][t]: -big_o}, -np.inf, 0.0)
        add({idx["P"][t]: 1.0, idx["W"][t]: -cap, idx["y"][t]: -big_p}, -big_p, np.inf)
        # inventory and backlog balance
        add({idx["I"][t]: 1.0, **Ip, **prod, idx["c"][t]: 1.0, idx["u"][t]: 1.0},
            b0 if first else 0.0, b0 if first else 0.0)
        add({idx["B"][t]: 1.0, **Bp, idx["c"][t]: 1.0, idx["u"][t]: 1.0}, D[t], D[t])
        # backlog cleared <= backlog carried in
        add({idx["c"][t]: 1.0, **({} if first else {idx["B"][t - 1]: -1.0})}, -np.inf, 0.0)
        # shipments cannot exceed what is available: c + u <= I_{t-1} + P + O + S
        add({idx["c"][t]: 1.0, idx["u"][t]: 1.0, **Ip, **prod}, -np.inf, b0 if first else 0.0)
        # never hold stock while customers wait (the engine always ships what it can)
        add({idx["I"][t]: 1.0, idx["w"][t]: float(p["max_inventory"])}, -np.inf,
            float(p["max_inventory"]))
        add({idx["B"][t]: 1.0, idx["w"][t]: -big_b}, -np.inf, 0.0)
        # oldest orders first: on-time shipments only after ALL old backlog is cleared
        add({idx["u"][t]: 1.0, idx["z"][t]: -D[t]}, -np.inf, 0.0)
        if not first:
            add({idx["B"][t - 1]: 1.0, idx["c"][t]: -1.0, idx["z"][t]: big_b}, -np.inf, big_b)
    # service: sum(u) >= svc * sum(D)
    add({idx["u"][t]: 1.0 for t in range(n)}, svc * sum(D), np.inf)

    A = lil_matrix((len(rows), nv))
    for r, coefs in enumerate(rows):
        for j, v in coefs.items():
            A[r, j] = A[r, j] + v
    cost = np.zeros(nv)
    hire = float(p["hiring_cost"])
    lay = float(p["layoff_cost"])
    for t in range(n):
        cost[idx["W"][t]] = p["regular_labor_cost"]
        cost[idx["H"][t]] = hire
        cost[idx["L"][t]] = lay
        cost[idx["O"][t]] = p["overtime_cost"]
        cost[idx["S"][t]] = p["subcontract_cost"]
        cost[idx["I"][t]] = p["holding_cost"]
        cost[idx["B"][t]] = p["backorder_cost"]
    try:
        res = milp(c=cost, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=integ,
                   bounds=Bounds(lb, ub), options={"time_limit": float(time_limit),
                                                   "mip_rel_gap": 1e-9, "disp": False})
    except Exception as e:                                   # pragma: no cover
        return BenchmarkResult("error", f"The solver failed: {e}")
    if res.x is None:
        if res.status == 2:
            return BenchmarkResult("infeasible", "No plan satisfies every requirement "
                                   "(storage, service, safety stock, terminal rules, limits).")
        return BenchmarkResult("error", f"No solution found (solver status {res.status}: "
                               f"{res.message}).")
    x = res.x
    workers = [float(round(x[idx["W"][t]])) if whole else float(x[idx["W"][t]]) for t in range(n)]
    regular = [max(0.0, float(x[idx["P"][t]])) for t in range(n)]
    over = [max(0.0, float(x[idx["O"][t]])) for t in range(n)]
    sub = [max(0.0, float(x[idx["S"][t]])) for t in range(n)]
    pol = Policy(worker_mode="whole" if whole else "partial", safety_target=target,
                 start_workers=w0)
    df = simulate(scn, workers, regular, over, sub, pol)
    checks = check_feasibility(df, requirements_for(scn, pol, use_overtime, use_subcontract))
    gap = getattr(res, "mip_gap", None)
    proven = res.status == 0 and (gap is None or gap <= 1e-6)
    status = "optimal" if proven else "feasible"
    if not is_feasible(checks):
        tol_fail = [c["label"] for c in checks if not c["ok"]]
        return BenchmarkResult("error", "Solver output failed the student-model checks "
                               f"({'; '.join(tol_fail)}). Please report this.", df=df, checks=checks)
    msg = ("Proven optimal for this model (no cheaper plan satisfies the same constraints)."
           if proven else
           "A feasible benchmark plan — optimality was NOT proven"
           + (f" (solver gap {gap:.2%})." if gap is not None else "."))
    return BenchmarkResult(status, msg, cost=float(df["Total Monthly Cost"].sum()), gap=gap,
                           workers=workers, regular=regular, overtime=over, subcontract=sub,
                           summary=summarize(df, scn, pol), checks=checks, df=df,
                           solver_objective=float(res.fun))
