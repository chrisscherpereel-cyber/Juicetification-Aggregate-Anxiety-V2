"""Calculation engine for the aggregate-planning model (pure functions, no Streamlit).

MODEL RULES (shown to students in the app, the Excel workbook and the PDF report)
--------------------------------------------------------------------------------
Inventory vs. backlog
    Physical inventory is never negative. Unfilled demand is a separate BACKLOG that
    carries to the next month. Net position = inventory - backlog.
    Each month:  available = beginning inventory + production
                 1) old backlog is cleared first (oldest orders first),
                 2) then this month's demand is shipped,
                 3) whatever demand cannot be shipped becomes new backlog,
                 4) leftover stock is ending inventory.
    Conservation:  beg inv + production = shipments + end inv + disposed
                   end backlog = beg backlog - cleared + new shortage

Safety stock = a PLANNING TARGET, not a hard floor
    When a plan "maintains safety stock" it AIMS to end every month with at least the
    safety-stock quantity (S) on hand: required production = demand + S - net position.
    Customers are served first, so stock below S is still shipped; months ending below S
    are reported as "months below safety stock" instead of being forced into backlog.
    Beginning inventory (B) and safety stock (S) are independent inputs: with B < S a
    "maintain" plan builds up to S, with B > S it may spend the excess.

Terminal requirements (explicit)
    December ending backlog must be <= terminal_backlog_max (default 0) and December
    ending inventory must be >= the policy target (S if the plan maintains safety stock,
    otherwise 0).

Workforce
    Workers are paid for CAPACITY (workers x bottles/worker/month) whether or not it is
    fully used; production can be below capacity (idle, paid capacity). Whole-worker mode
    counts integer workers; partial mode treats the workforce as worker-equivalents /
    paid labor hours (workers x paid hours per shift), hired and laid off per hour of daily
    capacity. "Paid labor hours/day" is the total across ALL workers, not the operating
    hours of a single line.

Costs ($) and rounding
    Quantities are carried at full precision; money is shown/exported to the cent and every
    total is computed from the unrounded monthly values (so totals reconcile to < $0.01).
    labor = workers x labor_cost            holding = ending inventory x holding_cost
    hiring/layoff = measure change x rate    backorder = ending backlog x backorder_cost
    overtime = OT bottles x overtime_cost   subcontract = bottles x subcontract_cost
    disposal = disposed bottles x disposal_cost
    (whole: rate = hiring_cost / layoff_cost per worker;
     partial: hiring_cost / hours_per_day per hour-of-daily-capacity.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .scenario import Scenario

EPS = 1e-6

PLAN_COLUMNS = [
    "Month", "Forecast Demand", "Beginning Inventory", "Beginning Backlog", "Workers",
    "Paid Labor Hours/Day", "Regular Capacity", "Regular Production", "Idle Capacity",
    "Overtime Production", "Subcontract Production", "Total Production", "Hires", "Layoffs",
    "On-Time Shipments", "Backlog Cleared", "New Shortage", "Ending Inventory", "Backorders",
    "Disposed", "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Overtime Cost",
    "Subcontract Cost", "Holding Cost", "Backorder Cost", "Disposal Cost",
    "Total Monthly Cost", "Storage Heuristic",
]
COST_COLUMNS = ["Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Overtime Cost",
                "Subcontract Cost", "Holding Cost", "Backorder Cost", "Disposal Cost"]


@dataclass(frozen=True)
class Policy:
    worker_mode: str = "whole"            # "whole" | "partial"
    safety_target: float = 0.0            # ending-inventory planning target (0 = none)
    start_workers: Optional[float] = None  # None -> scenario starting_workforce

    @property
    def whole(self) -> bool:
        return self.worker_mode == "whole"

    @property
    def maintains_safety(self) -> bool:
        return self.safety_target > 0


@dataclass(frozen=True)
class Decision:
    """The decisions that define a plan (everything else is calculated)."""
    workers: Sequence[float]
    regular: Sequence[float]
    overtime: Sequence[float] = ()
    subcontract: Sequence[float] = ()
    policy: Policy = field(default_factory=Policy)


def _z(seq, n):
    return [float(x) for x in seq] if len(seq) else [0.0] * n


def storage_heuristic_limit(scn: Scenario, i: int) -> float:
    """Heuristic only: demand of the next two months. It is NOT actual inventory age."""
    return float(sum(scn.demand[j] for j in (i + 1, i + 2) if j < len(scn.demand)))


def simulate(scn: Scenario, workers: Sequence[float], regular: Sequence[float],
             overtime: Sequence[float] = (), subcontract: Sequence[float] = (),
             policy: Optional[Policy] = None, demand: Optional[Sequence[float]] = None,
             shelf_life: int = 0) -> pd.DataFrame:
    """Month-by-month plan for explicit decisions. `demand` overrides the forecast (used
    to evaluate a committed plan against realized demand). shelf_life > 0 tracks inventory
    age cohorts (FIFO shipping; stock that reaches its last shippable month unsold is
    disposed). Beginning inventory is treated as fresh at the start of January."""
    p = scn.params
    policy = policy or Policy()
    n = len(scn.months)
    D = [float(x) for x in (demand if demand is not None else scn.demand)]
    W = [float(x) for x in workers]
    R = [float(x) for x in regular]
    O = _z(overtime, n)
    S = _z(subcontract, n)
    if not (len(D) == len(W) == len(R) == len(O) == len(S) == n):
        raise ValueError(f"Every decision list must have {n} entries (one per month).")

    hpd = scn.hpd
    partial = not policy.whole
    hire_rate = p["hiring_cost"] / hpd if partial else p["hiring_cost"]
    layoff_rate = p["layoff_cost"] / hpd if partial else p["layoff_cost"]
    prev_w = float(p["starting_workforce"] if policy.start_workers is None
                   else policy.start_workers)
    measure = (lambda w: w * hpd) if partial else (lambda w: w)
    prev_m = measure(prev_w)

    inv, bk = float(p["beginning_inventory"]), 0.0
    L = int(shelf_life or 0)
    coh = [0.0] * L
    if L:
        coh[0] = inv

    rows = []
    for i, m in enumerate(scn.months):
        w, reg, ot, sub = W[i], R[i], O[i], S[i]
        total_prod = reg + ot + sub
        cap = w * scn.cap_month
        cur_m = measure(w)
        hires, layoffs = max(0.0, cur_m - prev_m), max(0.0, prev_m - cur_m)

        beg_inv, beg_bk = inv, bk
        if L:
            coh[0] += total_prod
            avail = sum(coh)
        else:
            avail = inv + total_prod
        cleared = min(bk, avail)
        ontime = min(D[i], avail - cleared)
        short = D[i] - ontime
        disposed = 0.0
        if L:
            rem = cleared + ontime
            for a in range(L - 1, -1, -1):                    # oldest first
                take = min(coh[a], rem)
                coh[a] -= take
                rem -= take
            disposed = coh[L - 1]
            coh[L - 1] = 0.0
            coh = [0.0] + coh[:L - 1]
            inv = sum(coh)
        else:
            inv = avail - cleared - ontime
        bk = bk - cleared + short

        labor = w * p["regular_labor_cost"]
        hire_c, lay_c = hires * hire_rate, layoffs * layoff_rate
        ot_c, sub_c = ot * p["overtime_cost"], sub * p["subcontract_cost"]
        hold_c, bo_c = inv * p["holding_cost"], bk * p["backorder_cost"]
        disp_c = disposed * p["disposal_cost"]
        total = labor + hire_c + lay_c + ot_c + sub_c + hold_c + bo_c + disp_c

        warn = ""
        spoil = storage_heuristic_limit(scn, i)
        if inv > p["max_inventory"] + EPS:
            warn = f"Over storage limit ({p['max_inventory']:,.0f})"
        elif spoil and inv > spoil + EPS and not L:
            warn = f"Heuristic: above next 2 months' demand ({spoil:,.0f})"

        rows.append({
            "Month": m, "Forecast Demand": D[i], "Beginning Inventory": beg_inv,
            "Beginning Backlog": beg_bk, "Workers": w, "Paid Labor Hours/Day": w * hpd,
            "Regular Capacity": cap, "Regular Production": reg,
            "Idle Capacity": max(0.0, cap - reg), "Overtime Production": ot,
            "Subcontract Production": sub, "Total Production": total_prod,
            "Hires": hires, "Layoffs": layoffs, "On-Time Shipments": ontime,
            "Backlog Cleared": cleared, "New Shortage": short, "Ending Inventory": inv,
            "Backorders": bk, "Disposed": disposed, "Regular Labor Cost": labor,
            "Hiring Cost": hire_c, "Layoff Cost": lay_c, "Overtime Cost": ot_c,
            "Subcontract Cost": sub_c, "Holding Cost": hold_c, "Backorder Cost": bo_c,
            "Disposal Cost": disp_c, "Total Monthly Cost": total, "Storage Heuristic": warn,
        })
        prev_m = cur_m
    return pd.DataFrame(rows, columns=PLAN_COLUMNS)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
METRIC_DEFINITIONS: Dict[str, str] = {
    "On-time fulfillment": "Share of all demand shipped in the month it was due "
        "(on-time shipments ÷ total demand). Late shipments from backlog do not count.",
    "Months with shortages": "Months in which some of that month's own demand could not be "
        "shipped on time.",
    "Months ending with backlog": "Months that end with unfilled orders still waiting "
        "(includes backlog carried in from earlier months).",
    "Ending backlog": "Unfilled orders waiting at the end of December (bottles).",
    "Demand fulfilled by year end": "1 − ending backlog ÷ total demand: the share of the "
        "year's demand that was shipped at some point, on time or late.",
    "Highest inventory": "Largest physical month-end stock (bottles) — storage needed.",
    "Months below safety stock": "Months ending with physical inventory below the "
        "safety-stock target (only counted when the plan maintains safety stock).",
    "Backlog bottle-months": "Sum of month-end backlog; this is what backorder cost charges.",
    "Idle capacity (bottles)": "Paid regular capacity that was not used for production.",
    "Number of workforce changes": "Months in which workers were hired or laid off.",
}


def summarize(df: pd.DataFrame, scn: Scenario, policy: Optional[Policy] = None) -> dict:
    """Clearly defined KPIs for one plan (see METRIC_DEFINITIONS)."""
    policy = policy or Policy()
    total_d = float(df["Forecast Demand"].sum())
    ontime = float(df["On-Time Shipments"].sum())
    last = df.iloc[-1]
    changes = int(((df["Hires"] > EPS) | (df["Layoffs"] > EPS)).sum())
    below = 0
    if policy.maintains_safety:
        below = int((df["Ending Inventory"] < policy.safety_target - EPS).sum())
    out = {
        "Total regular labor cost": float(df["Regular Labor Cost"].sum()),
        "Total hiring cost": float(df["Hiring Cost"].sum()),
        "Total layoff cost": float(df["Layoff Cost"].sum()),
        "Total overtime cost": float(df["Overtime Cost"].sum()),
        "Total subcontract cost": float(df["Subcontract Cost"].sum()),
        "Total holding cost": float(df["Holding Cost"].sum()),
        "Total backorder cost": float(df["Backorder Cost"].sum()),
        "Total disposal cost": float(df["Disposal Cost"].sum()),
        "Total cost": float(df["Total Monthly Cost"].sum()),
        "Highest inventory": float(df["Ending Inventory"].max()),
        "Highest workforce": float(df["Workers"].max()),
        "Lowest workforce": float(df["Workers"].min()),
        "Total hires": float(df["Hires"].sum()),
        "Total layoffs": float(df["Layoffs"].sum()),
        "Number of workforce changes": changes,
        "On-time fulfillment": 100.0 * ontime / total_d if total_d > 0 else 100.0,
        "Months with shortages": int((df["New Shortage"] > EPS).sum()),
        "Months ending with backlog": int((df["Backorders"] > EPS).sum()),
        "Ending backlog": float(last["Backorders"]),
        "Ending inventory": float(last["Ending Inventory"]),
        "Demand fulfilled by year end": (100.0 * (1 - float(last["Backorders"]) / total_d)
                                         if total_d > 0 else 100.0),
        "Highest backlog": float(df["Backorders"].max()),
        "Backlog bottle-months": float(df["Backorders"].sum()),
        "Months below safety stock": below,
        "Idle capacity (bottles)": float(df["Idle Capacity"].sum()),
        "Total overtime bottles": float(df["Overtime Production"].sum()),
        "Total subcontracted bottles": float(df["Subcontract Production"].sum()),
        "Total disposed bottles": float(df["Disposed"].sum()),
        "Total production": float(df["Total Production"].sum()),
        "Total demand": total_d,
    }
    return out


# --------------------------------------------------------------------------- #
# Requirements / feasibility
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Requirements:
    max_inventory: float
    ot_pct: float
    sub_capacity: float
    service_min_pct: float
    terminal_inventory_min: float
    terminal_backlog_max: float
    safety_target: float
    whole_workers: bool
    overtime_allowed: bool = True
    subcontract_allowed: bool = True


def requirements_for(scn: Scenario, policy: Policy, overtime_allowed: bool = True,
                     subcontract_allowed: bool = True) -> Requirements:
    p = scn.params
    return Requirements(
        max_inventory=float(p["max_inventory"]), ot_pct=float(p["overtime_pct"]),
        sub_capacity=float(p["subcontract_capacity"]),
        service_min_pct=float(p["service_requirement_pct"]),
        terminal_inventory_min=float(policy.safety_target),
        terminal_backlog_max=float(p["terminal_backlog_max"]),
        safety_target=float(policy.safety_target), whole_workers=policy.whole,
        overtime_allowed=overtime_allowed, subcontract_allowed=subcontract_allowed)


def check_feasibility(df: pd.DataFrame, req: Requirements) -> List[dict]:
    """Each check: {key,label,ok,detail}. A plan is feasible when every check is ok."""
    checks: List[dict] = []
    months = list(df["Month"])

    def bad(mask):
        return [months[i] for i, v in enumerate(mask) if v]

    def add(key, label, mask, ok_text, fail_prefix):
        b = bad(mask)
        checks.append({"key": key, "label": label, "ok": not b,
                       "detail": ok_text if not b else f"{fail_prefix}: {', '.join(b)}"})

    add("capacity", "Regular production within workforce capacity",
        (df["Regular Production"] > df["Regular Capacity"] + EPS).tolist(),
        "Every month's regular production ≤ workers × capacity per worker.",
        "Regular production exceeds capacity in")
    if req.whole_workers:
        frac = [abs(w - round(w)) > 1e-6 for w in df["Workers"]]
        add("whole_workers", "Whole-number workforce", frac,
            "Workforce is whole workers every month.", "Fractional workers in")
    ot = df["Overtime Production"]
    if req.overtime_allowed:
        lim = df["Regular Capacity"] * req.ot_pct
        add("overtime_limit", f"Overtime within {req.ot_pct:.0%} of regular capacity",
            (ot > lim + EPS).tolist(), "Overtime is within the limit every month.",
            "Overtime exceeds the limit in")
        add("overtime_after_regular", "Overtime only when regular capacity is fully used",
            ((ot > EPS) & (df["Idle Capacity"] > EPS)).tolist(),
            "No overtime is used while regular capacity sits idle.",
            "Overtime used while regular capacity is idle in")
    else:
        add("overtime_limit", "Overtime not used (not enabled)", (ot > EPS).tolist(),
            "No overtime used.", "Overtime used but not enabled in")
    sub = df["Subcontract Production"]
    if req.subcontract_allowed:
        add("subcontract_limit",
            f"Subcontracting within {req.sub_capacity:,.0f} bottles/month",
            (sub > req.sub_capacity + EPS).tolist(),
            "Subcontracting is within its capacity limit.", "Subcontract limit exceeded in")
    else:
        add("subcontract_limit", "Subcontracting not used (not enabled)",
            (sub > EPS).tolist(), "No subcontracting used.", "Subcontracting used but not enabled in")
    add("storage", f"Storage limit ({req.max_inventory:,.0f} bottles)",
        (df["Ending Inventory"] > req.max_inventory + EPS).tolist(),
        "Ending inventory never exceeds the storage limit.", "Storage limit exceeded in")
    total_d = float(df["Forecast Demand"].sum())
    ont = 100.0 * float(df["On-Time Shipments"].sum()) / total_d if total_d > 0 else 100.0
    if req.service_min_pct > 0:
        checks.append({"key": "service",
                       "label": f"On-time fulfillment ≥ {req.service_min_pct:g}%",
                       "ok": ont + 1e-9 >= req.service_min_pct,
                       "detail": f"On-time fulfillment is {ont:.1f}%."})
    if req.safety_target > 0:
        add("safety_stock", f"Safety stock ({req.safety_target:,.0f}) held at each month-end",
            (df["Ending Inventory"] < req.safety_target - EPS).tolist(),
            "Ending inventory ≥ the safety-stock target every month.",
            "Ending inventory below the safety-stock target in")
    last = df.iloc[-1]
    checks.append({"key": "terminal_backlog",
                   "label": f"December backlog ≤ {req.terminal_backlog_max:,.0f}",
                   "ok": last["Backorders"] <= req.terminal_backlog_max + EPS,
                   "detail": f"December ending backlog is {last['Backorders']:,.0f}."})
    checks.append({"key": "terminal_inventory",
                   "label": f"December inventory ≥ {req.terminal_inventory_min:,.0f}",
                   "ok": last["Ending Inventory"] >= req.terminal_inventory_min - EPS,
                   "detail": f"December ending inventory is {last['Ending Inventory']:,.0f}."})
    return checks


def is_feasible(checks: List[dict]) -> bool:
    return all(c["ok"] for c in checks)


# --------------------------------------------------------------------------- #
# Reference plans
# --------------------------------------------------------------------------- #
BINV_POLICIES = {
    "use_first": "Use the beginning inventory in January (no safety stock)",
    "maintain": "Maintain the safety stock all year",
    "no_fire_first": "Spend the beginning inventory, but don't lay off in January if the "
                     "current crew + inventory covers demand",
}


def workers_for(scn: Scenario, req: float, whole: bool) -> float:
    """Workforce (worker-equivalents) needed to produce `req` bottles in a month."""
    r = max(0.0, req) / scn.cap_month
    return float(math.ceil(r - 1e-9)) if whole else r


def covered_month1(scn: Scenario) -> bool:
    """Does the starting workforce + beginning inventory already cover January?"""
    return (scn.params["starting_workforce"] * scn.cap_month
            + scn.params["beginning_inventory"]) >= scn.demand[0] - EPS


def chase_policy(scn: Scenario, key: str, whole: bool) -> Policy:
    target = float(scn.params["safety_stock"]) if key == "maintain" else 0.0
    return Policy(worker_mode="whole" if whole else "partial", safety_target=target,
                  start_workers=float(scn.params["starting_workforce"]))


def reference_chase(scn: Scenario, key: str = "use_first", whole: bool = True) -> Decision:
    """Chase: each month produce exactly what is needed (demand + target − net position)."""
    pol = chase_policy(scn, key, whole)
    net = float(scn.params["beginning_inventory"])
    workers, prod = [], []
    for i, d in enumerate(scn.demand):
        req = max(0.0, d + pol.safety_target - net)
        if i == 0 and key == "no_fire_first" and covered_month1(scn):
            w = float(scn.params["starting_workforce"])
        else:
            w = workers_for(scn, req, whole)
        workers.append(w)
        prod.append(req)
        net = net + req - d
    return Decision(workers, prod, policy=pol)


def level_target(scn: Scenario, maintain: bool) -> float:
    return float(scn.params["safety_stock"]) if maintain else 0.0


def level_rate(scn: Scenario, maintain: bool = False) -> float:
    """(total demand + ending-inventory target − beginning inventory) ÷ months, never < 0."""
    t = level_target(scn, maintain)
    return max(0.0, (scn.total_demand + t - scn.params["beginning_inventory"]) / len(scn.months))


def reference_level(scn: Scenario, whole: bool = True, maintain: bool = False) -> Decision:
    rate = level_rate(scn, maintain)
    w = workers_for(scn, rate, whole)
    n = len(scn.months)
    pol = Policy(worker_mode="whole" if whole else "partial",
                 safety_target=level_target(scn, maintain),
                 start_workers=float(scn.params["starting_workforce"]))
    return Decision([w] * n, [rate] * n, policy=pol)


def run_decision(scn: Scenario, dec: Decision, demand=None, shelf_life: int = 0) -> pd.DataFrame:
    return simulate(scn, dec.workers, dec.regular, dec.overtime, dec.subcontract,
                    dec.policy, demand=demand, shelf_life=shelf_life)


def reference_plan(scn: Scenario, kind: str, *, policy_key: str = "use_first",
                   whole: bool = True, maintain: bool = False):
    """(decision, plan DataFrame) for 'chase' or 'level'."""
    dec = (reference_chase(scn, policy_key, whole) if kind == "chase"
           else reference_level(scn, whole, maintain))
    return dec, run_decision(scn, dec)


def assumption_gaps(entries: Dict[str, Policy], scn: Scenario) -> List[str]:
    """Plain-language notes when plans being compared do not share assumptions."""
    notes: List[str] = []
    labels = list(entries)
    if len({e.worker_mode for e in entries.values()}) > 1:
        notes.append("Worker models differ (" + ", ".join(
            f"{k}: {v.worker_mode}" for k, v in entries.items()) + "). Whole-worker rounding "
            "adds paid idle capacity and whole-worker hire/layoff rates; part of any cost "
            "difference is this modelling choice, not strategy.")
    if len({round(e.safety_target, 6) for e in entries.values()}) > 1:
        notes.append("Safety-stock policies differ (" + ", ".join(
            f"{k}: {v.safety_target:,.0f}" for k, v in entries.items()) + "), so the plans "
            "also have different December inventory requirements.")
    if len({e.start_workers for e in entries.values()}) > 1:
        notes.append("Starting workforces differ between plans.")
    return notes
