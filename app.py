"""
Juicetification: Aggregate Anxiety
An individual experiential Streamlit simulation of aggregate production planning.

Students BUILD chase and level aggregate plans by hand: they pick the data they
need, choose worksheet columns (with reasons), select formulas, then TYPE every
value of the 12-month worksheet themselves. The app checks their work and coaches
them toward the fix WITHOUT revealing the numbers (a last-resort reveal exists).

Run:  streamlit run app.py
"""

import math
import re
import json
import random
import datetime as dt

import pandas as pd
import streamlit as st

from juice_director import resolve_config, serve_manifest_if_requested
from manifest import MANIFEST
import student_store as store

# The Director discovers this app's parameter schema via ?manifest=1 (emits JSON
# and stops). Otherwise resolve_config returns instructor overrides (?cfg=/?game=)
# merged over the built-in defaults; with no such param the app is unchanged.
serve_manifest_if_requested(MANIFEST)
CFG, CTX = resolve_config(MANIFEST)

# --------------------------------------------------------------------------- #
# 1. SCENARIO DATA
# --------------------------------------------------------------------------- #
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

def generate_forecast(seed):
    """A unique but easy-to-calculate 12-month forecast per student.

    Every month is a whole multiple of 500 and the annual total is an exact
    multiple of 6,000 — so the level rate is a clean multiple of 500. Some months
    land on an odd 500 (e.g. 14,500) so that WHOLE workers must round up while
    PARTIAL workers stay fractional — the two worker models then differ. Demand is
    seasonal (spring/summer peak) and randomized by the session seed."""
    r = random.Random(seed)
    # ODD half-thousands so the level rate lands on an odd 500 (e.g. 10,500) — then
    # WHOLE workers must round up while PARTIAL workers stay on a clean half.
    avg_h = r.choice([17, 19, 21, 23, 25, 27])             # half-thousands / month
    shape = [0.55, 0.60, 0.75, 1.00, 1.30, 1.55, 1.65, 1.40, 1.10, 0.85, 0.70, 0.55]
    shape = [max(0.35, s + r.uniform(-0.12, 0.12)) for s in shape]
    tot_h = avg_h * 12
    ssum = sum(shape)
    dh = [max(1, round(s / ssum * tot_h)) for s in shape]
    dh[dh.index(max(dh))] += tot_h - sum(dh)               # fix rounding drift on peak
    return {MONTHS[i]: int(dh[i]) * 500 for i in range(12)}


# Placeholder forecast (overwritten per session once the seed is known, below).
FORECAST = generate_forecast(0)

# Built from the resolved config: built-in defaults, overridden by the instructor's
# Director settings (?cfg=/?game=) when present. Defaults reproduce the original
# scenario exactly, so the app is unchanged when no config is supplied.
PARAMS = {k: CFG[k] for k in (
    "beginning_inventory",   # a multiple of 12 → clean level rates either policy
    "safety_stock",          # the safety-stock level *when a policy maintains one*
    "bottles_per_worker",    # = bottles_per_hour × hours_per_day × working_days
    "bottles_per_hour",      # output per worker-hour
    "hours_per_day",         # full shift length
    "working_days",          # producing days per month
    "regular_labor_cost",    # per FULL worker (8h/day) per month
    "hiring_cost",           # per worker hired
    "layoff_cost",           # per worker laid off
    "holding_cost",          # per bottle per month
    "backorder_cost",        # per bottle per month
    "overtime_pct",          # up to 20% above regular production
    "overtime_cost",         # per bottle
    "subcontract_cost",      # per bottle
    "max_inventory",         # bottles
    "starting_workforce",    # workers on payroll before January
)}

TOTAL_DEMAND = sum(FORECAST.values())  # 174,000 with the default forecast

# Brief-data values usable by NAME inside any formula, e.g. =D1*LABOR, =F1*HOLD.
# Derived from PARAMS so an instructor config change flows into the formula engine.
NAMED_CONSTS = {
    "LABOR": PARAMS["regular_labor_cost"],                            # regular labor $/worker/month
    "HIRE": PARAMS["hiring_cost"],                                    # hiring $/whole worker
    "LAYOFF": PARAMS["layoff_cost"],                                  # layoff $/whole worker
    "HIREHR": PARAMS["hiring_cost"] / PARAMS["hours_per_day"],        # hiring $ per hour/day of capacity
    "LAYOFFHR": PARAMS["layoff_cost"] / PARAMS["hours_per_day"],      # layoff $ per hour/day of capacity
    "HOLD": PARAMS["holding_cost"],                                   # holding $/bottle/month
    "BACKORDER": PARAMS["backorder_cost"],                            # backorder $/bottle/month
    "OVERTIME": PARAMS["overtime_cost"],                              # overtime $/bottle
    "SUBCONTRACT": PARAMS["subcontract_cost"],                        # subcontract $/bottle
    "RATE": PARAMS["bottles_per_worker"],                            # bottles per worker per month
    "BEGIN": PARAMS["beginning_inventory"],                          # beginning inventory
    "SAFETY": PARAMS["safety_stock"],                                # safety-stock level (only when a policy maintains one)
    "MAXINV": PARAMS["max_inventory"],                               # max finished-goods inventory
}
NAMED_RE = re.compile(r"\b(" + "|".join(NAMED_CONSTS) + r")\b", re.IGNORECASE)

# The formulas from Stage 4, kept handy while building the plan (Stages 6 & 7).
FORMULA_REF_CHASE = [
    ("Beginning Inventory", "previous month's Ending balance  (January = 2,400)"),
    ("Regular Production", "bottles to make this month, adjusting for beginning "
     "inventory per your policy"),
    ("Workers", "Regular Production ÷ capacity/worker/month  (WHOLE: round up; "
     "PARTIAL: keep the fraction)"),
    ("Hours/Day", "Workers × 8  (how many hours/day the line runs)"),
    ("Hires", "WHOLE: MAX(0, ΔWorkers vs last month) · PARTIAL: MAX(0, ΔHours/Day)"),
    ("Layoffs", "WHOLE: MAX(0, −ΔWorkers) · PARTIAL: MAX(0, −ΔHours/Day). Jan vs 8 "
     "workers = 64 h/day"),
    ("Ending Inventory", "MAX(0, Beginning + Production − Demand)"),
    ("Backorders", "MAX(0, Demand − Beginning − Production)"),
    ("Regular Labor Cost", "Workers × $3,200      (=…*LABOR)"),
    ("Hiring Cost", "WHOLE: Hires × $600 (HIRE) · PARTIAL: Hires × $75 (HIREHR)"),
    ("Layoff Cost", "WHOLE: Layoffs × $900 (LAYOFF) · PARTIAL: × $112.50 (LAYOFFHR)"),
    ("Holding Cost", "Ending Inventory × $0.25      (=…*HOLD)"),
    ("Backorder Cost", "Backorders × $1.50      (=…*BACKORDER)"),
    ("Total Monthly Cost", "Labor + Hiring + Layoff + Holding + Backorder"),
]
FORMULA_REF_LEVEL = [
    ("Beginning Inventory", "previous month's balance  (January = 2,400)"),
    ("Regular Production", "the constant level rate, same every month"),
    ("Workers", "the constant level workforce, same every month"),
    ("Hours/Day", "Workers × 8 (same every month)"),
    ("Hires", "January only: MAX(0, level workforce − 8) [whole] or hours version "
     "[partial]; 0 afterward"),
    ("Layoffs", "January only: MAX(0, 8 − level workforce); 0 afterward"),
    ("Ending Inventory", "MAX(0, Beginning + Production − Demand)"),
    ("Backorders", "MAX(0, Demand − Beginning − Production)"),
    ("Regular Labor Cost", "constant Workers × $3,200      (=…*LABOR)"),
    ("Hiring Cost", "WHOLE: Hires × $600 · PARTIAL: Hires(hrs) × $75 (only January)"),
    ("Layoff Cost", "WHOLE: Layoffs × $900 · PARTIAL: × $112.50 (only January)"),
    ("Holding Cost", "Ending Inventory × $0.25      (=…*HOLD)"),
    ("Backorder Cost", "Backorders × $1.50      (=…*BACKORDER)"),
    ("Total Monthly Cost", "Labor + Hiring + Layoff + Holding + Backorder"),
]

# Warm-up drills (Stage 5): pure level & pure chase, with and without starting stock.
PRACTICE = [
    {"name": "No starting inventory", "months": MONTHS,
     "demand": [10000, 20000, 30000, 40000], "begin": 0, "end": 0, "rate": 1000},
    {"name": "With starting inventory", "months": MONTHS,
     "demand": [10000, 20000, 30000, 40000], "begin": 20000, "end": 0, "rate": 1000},
    {"name": "Safety stock required (with starting inventory)", "months": MONTHS,
     "demand": [30000, 60000, 30000], "begin": 10000, "end": 10000, "rate": 1000},
    {"name": "Uneven demand, no starting inventory", "months": MONTHS,
     "demand": [5000, 5000, 10000, 10000, 5000, 5000], "begin": 0, "end": 0, "rate": 1000},
]

# Reasons every worksheet column exists (used as learning material, Stage 3).
COLUMN_REASONS = {
    "Month": "Defines the planning period each row covers; every calculation is monthly.",
    "Forecast Demand": "The bottles you must supply that month — the target every plan is built around.",
    "Beginning Inventory": "Stock on hand at the start of the month; it lowers how much you must produce and links each month to the previous one.",
    "Required Production": "Bottles you must make after using up beginning inventory; it drives the workforce calculation.",
    "Regular Production": "Bottles actually produced on regular time (workers × 1,000); compared against demand to update inventory.",
    "Workers Needed": "Converts required production into a headcount (production ÷ 1,000, rounded up) — the core capacity decision in a chase plan.",
    "Workers Available": "Last month's workforce; the baseline you compare against to know whether to hire or lay off.",
    "Hires": "Workers added versus last month; the only way a chase plan grows capacity, and it triggers hiring cost.",
    "Layoffs": "Workers cut versus last month; triggers layoff cost and signals workforce instability.",
    "Ending Inventory": "Positive stock carried into next month; drives holding cost and becomes next month's beginning inventory.",
    "Backorders": "Unmet demand carried forward; drives backorder cost and lowers the service level.",
    "Regular Labor Cost": "Workforce × $3,200 — usually the single largest cost in the plan.",
    "Hiring Cost": "Hires × $600 — the price of scaling the workforce up.",
    "Layoff Cost": "Layoffs × $900 — the price of scaling the workforce down.",
    "Holding Cost": "Ending inventory × $0.25 — the cost of carrying unsold bottles.",
    "Backorder Cost": "Backorders × $1.50 — the penalty for meeting demand late.",
    "Total Monthly Cost": "Sum of every monthly cost; the number you compare across strategies.",
    "Level Production": "The constant monthly output that defines a level plan.",
    "Spoilage Warning": "Flags inventory above the 30,000 / two-month limit so the plan stays feasible.",
}

# --------------------------------------------------------------------------- #
# 2. CALCULATION ENGINE  (the app's 'truth' for checking student work)
# --------------------------------------------------------------------------- #
def spoilage_limit(month_index):
    nxt = 0
    for k in (1, 2):
        j = month_index + k
        if j < len(MONTHS):
            nxt += FORECAST[MONTHS[j]]
    return nxt


def build_plan(workers_by_month, production_by_month, p=PARAMS, start_workers=None,
               maintain_safety=False, worker_mode="whole"):
    """Deterministic worksheet given a workforce and production quantity per month.

    maintain_safety controls the beginning-inventory buffer (see policies).
    worker_mode:
      'whole'   -> hire/fire WHOLE WORKERS; Hires/Layoffs are worker counts and cost
                   $600 / $900 each.
      'partial' -> hire/fire HOURS of daily capacity; Hires/Layoffs are hours/day and
                   cost $600÷8 = $75 and $900÷8 = $112.50 per hour/day.
    """
    rows = []
    buffer = p["beginning_inventory"] if maintain_safety else 0
    carry = 0 if maintain_safety else p["beginning_inventory"]   # inventory excl. buffer
    prev_workers = p["starting_workforce"] if start_workers is None else start_workers
    hpd = p["hours_per_day"]
    partial = (worker_mode == "partial")
    hire_rate = p["hiring_cost"] / hpd if partial else p["hiring_cost"]     # 75 or 600
    layoff_rate = p["layoff_cost"] / hpd if partial else p["layoff_cost"]   # 112.5 or 900

    def measure(w):                       # the thing you hire/fire in this mode
        return w * hpd if partial else w

    prev_measure = measure(prev_workers)

    for i, m in enumerate(MONTHS):
        demand = FORECAST[m]
        workers = round(float(workers_by_month[i]), 4)     # may be fractional (partial)
        production = round(float(production_by_month[i]), 4)

        cur_measure = measure(workers)
        hires = max(0, cur_measure - prev_measure)
        layoffs = max(0, prev_measure - cur_measure)

        beg = buffer + carry
        position = carry + production - demand
        ending_inv = buffer + max(0, position)
        backorders = max(0, -position)

        reg_labor = workers * p["regular_labor_cost"]
        hire_cost = hires * hire_rate
        layoff_cost = layoffs * layoff_rate
        hold_cost = ending_inv * p["holding_cost"]
        bo_cost = backorders * p["backorder_cost"]
        total = reg_labor + hire_cost + layoff_cost + hold_cost + bo_cost

        spoil = spoilage_limit(i)
        warn = ""
        if ending_inv > p["max_inventory"]:
            warn = f"> max inventory ({p['max_inventory']:,})"
        elif spoil and ending_inv > spoil:
            warn = f"> 2-month spoilage limit ({spoil:,})"

        rows.append({
            "Month": m, "Forecast Demand": demand, "Beginning Inventory": beg,
            "Workers": workers, "Hours/Day": round(workers * hpd, 2),
            "Hires": round(hires, 2), "Layoffs": round(layoffs, 2),
            "Regular Production": production, "Ending Inventory": ending_inv,
            "Backorders": backorders, "Regular Labor Cost": reg_labor,
            "Hiring Cost": hire_cost, "Layoff Cost": layoff_cost,
            "Holding Cost": hold_cost, "Backorder Cost": bo_cost,
            "Total Monthly Cost": total, "Spoilage Warning": warn,
        })
        carry = position
        prev_measure = cur_measure
    return pd.DataFrame(rows)


def summarize(df, p=PARAMS):
    last_position = df.iloc[-1]["Ending Inventory"] - df.iloc[-1]["Backorders"]
    workforce_changes = int((df["Hires"] > 0).sum() + (df["Layoffs"] > 0).sum())
    months_short = int((df["Backorders"] > 0).sum())
    demand_met = (df["Forecast Demand"] - df["Backorders"]).clip(lower=0).sum()
    service = 100.0 * demand_met / df["Forecast Demand"].sum()
    return {
        "Total regular labor cost": df["Regular Labor Cost"].sum(),
        "Total hiring cost": df["Hiring Cost"].sum(),
        "Total layoff cost": df["Layoff Cost"].sum(),
        "Total holding cost": df["Holding Cost"].sum(),
        "Total backorder cost": df["Backorder Cost"].sum(),
        "Total cost": df["Total Monthly Cost"].sum(),
        "Highest inventory": int(df["Ending Inventory"].max()),
        "Highest workforce": round(float(df["Workers"].max()), 2),
        "Lowest workforce": round(float(df["Workers"].min()), 2),
        "Total hires": round(float(df["Hires"].sum()), 2),
        "Total layoffs": round(float(df["Layoffs"].sum()), 2),
        "Number of workforce changes": workforce_changes,
        "Months with shortages": months_short,
        "Service level": service,
        "Ending inventory": int(last_position),
        "Ending inventory ok": last_position >= 0,
    }


BINV_POLICIES = {
    "use_first": "Use in first month (default)",
    "maintain": "Maintain 2,400 safety stock throughout",
    "no_fire_first": "Don't lay off in month 1 if inventory + current labor covers demand",
}


def binv_maintains(policy):
    return policy == "maintain"


def covered_month1(p=PARAMS):
    """Does the starting workforce + beginning inventory already cover January?"""
    return (p["starting_workforce"] * p["bottles_per_worker"]
            + p["beginning_inventory"]) >= FORECAST[MONTHS[0]]


def _workers_for(req, whole):
    """Workforce (worker-equivalents) needed to make `req` bottles this month.
    whole -> round UP to a full worker; partial -> exact fraction (paid pro-rata,
    reported as hours/day)."""
    bpw = PARAMS["bottles_per_worker"]
    return math.ceil(req / bpw) if whole else round(req / bpw, 4)


def reference_chase(policy="use_first", whole=True):
    """Recommended plan for the chosen beginning-inventory policy and worker model.
    Regular Production = the bottles you must make this month (produce-to-demand);
    the two worker models differ only in how the workforce for that output is
    counted (whole rounds up; partial stays fractional)."""
    workers, production, carry = [], [], PARAMS["beginning_inventory"]
    for i, m in enumerate(MONTHS):
        if policy == "maintain":
            req = FORECAST[m]                          # keep the 2,400 buffer
        else:
            req = max(0, FORECAST[m] - carry)          # use_first / no_fire
        if i == 0 and policy == "no_fire_first" and covered_month1():
            w = PARAMS["starting_workforce"]           # keep the team in month 1
        else:
            w = _workers_for(req, whole)
        workers.append(w); production.append(req)
        carry = carry + req - FORECAST[m]
    return workers, production


def level_rate(maintain=False):
    """Level production rate = (total demand + end target − beginning) ÷ 12.
    The end target is the SAFETY STOCK when the policy maintains one, else 0 —
    it is not a separate 'given', it follows the inventory policy."""
    end_target = PARAMS["safety_stock"] if maintain else 0
    return (TOTAL_DEMAND + end_target - PARAMS["beginning_inventory"]) / len(MONTHS)


def reference_level(whole=True, maintain=False):
    level = level_rate(maintain)
    w = _workers_for(level, whole)
    return [w] * len(MONTHS), [level] * len(MONTHS)


def strategy_summaries_for_demand(demand, settings):
    """Re-run BOTH strategies (using the student's stored policy/model settings) on a
    different demand dict, and return (chase_summary, level_summary). Temporarily
    swaps the module demand so the existing engine can be reused unchanged."""
    global FORECAST, TOTAL_DEMAND
    save_f, save_t = FORECAST, TOTAL_DEMAND
    FORECAST, TOTAL_DEMAND = demand, sum(demand.values())
    try:
        cw = settings.get("chase_whole", True)
        cpol = settings.get("chase_policy", "use_first")
        cmain = settings.get("chase_maintain", False)
        lw = settings.get("level_whole", True)
        lmain = settings.get("level_maintain", False)
        ct = build_plan(*reference_chase(cpol, cw), maintain_safety=cmain,
                        worker_mode="whole" if cw else "partial")
        lt = build_plan(*reference_level(lw, lmain),
                        start_workers=PARAMS["starting_workforce"],
                        maintain_safety=lmain, worker_mode="whole" if lw else "partial")
        return summarize(ct), summarize(lt)
    finally:
        FORECAST, TOTAL_DEMAND = save_f, save_t


def compare_columns(student_df, truth_df, mapping, tol=0.5):
    """Return {student_col: [months that are wrong]} without exposing values."""
    report = {}
    for scol, tcol in mapping.items():
        wrong = []
        for i in range(len(MONTHS)):
            try:
                sv = float(student_df[scol].iloc[i])
            except (TypeError, ValueError):
                sv = None
            tv = float(truth_df[tcol].iloc[i])
            if sv is None or abs(sv - tv) > tol:
                wrong.append(MONTHS[i])
        report[scol] = wrong
    return report


# --------------------------------------------------------------------------- #
# 3. STREAMLIT UI
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Juicetification: Aggregate Anxiety", page_icon="🧃",
                   layout="wide")

SS = st.session_state

# ---- per-student identity (from the URL) & progress store --------------------- #
# All student_store functions are safe no-ops when storage is unconfigured, so the
# app behaves exactly as before unless the instructor wires up the secrets.
GAME = store.game_code()          # ?game=<code>  (None if absent)
SID = store.get_student_id()      # ?sid=<id>     (None if absent)

# When storage IS configured, require a student ID before the lab proceeds, so every
# student maps to a stable, resumable record. Disabled → skip the gate entirely.
if store.enabled() and not SID:
    st.title("Juicetification: Aggregate Anxiety")
    st.subheader("Enter your student ID to begin")
    _entered = st.text_input("Student ID", key="_gate_sid",
                             help="Your progress is saved under this ID so you can "
                                  "leave and resume later.")
    if st.button("Start", type="primary") and _entered.strip():
        store.set_student_id(_entered)   # puts ?sid= in the URL
        st.rerun()
    st.stop()

# Seed the scenario deterministically from the student ID, so the SAME student always
# gets the SAME unique scenario (and can resume it). If no student ID, fall back to a
# Director-provided seed, else a fresh random one — exactly as before.
if "rand_seed" not in SS:
    if SID:
        SS["rand_seed"] = store.derive_seed(GAME, SID, lo=1, hi=10 ** 6)
    elif CTX["seed"] is not None:
        SS["rand_seed"] = CTX["seed"]
    else:
        SS["rand_seed"] = random.randint(1, 10 ** 6)
SS.setdefault("student_name", "")
SS.setdefault("section", "")
SS.setdefault("recommendation", "")

# ---- challenge metrics (attempts, completion, help usage) ----
for _p in ("chase", "level"):
    SS.setdefault(f"{_p}_tries", 1)        # scenario attempts (1 + rebuilds)
    SS.setdefault(f"{_p}_stuck", 0)        # times "I'm stuck" was opened
    SS.setdefault(f"{_p}_completed", False)

# Streamlit clears a widget's key when its page isn't shown. Persisted widgets use
# a throwaway key '_w_<store>'; mirror each into its store key every run so text and
# choices survive navigation and reach the report.
SS.setdefault("recommendation", "")
SS.setdefault("chosen_plan", "Chase")
for _k in list(SS.keys()):
    if _k.startswith("_w_"):
        SS[_k[3:]] = SS[_k]


def persist_text_area(label, store_key, **kw):
    """A text_area whose content survives leaving the page."""
    wk = "_w_" + store_key
    if wk not in SS:
        SS[wk] = SS.get(store_key, "")
    st.text_area(label, key=wk, **kw)
    SS[store_key] = SS[wk]
    return SS[store_key]


def persist_radio(label, options, store_key, **kw):
    """A radio whose choice survives leaving the page."""
    wk = "_w_" + store_key
    if wk not in SS or SS[wk] not in options:
        SS[wk] = SS.get(store_key, options[0]) if SS.get(store_key) in options \
            else options[0]
    st.radio(label, options, key=wk, **kw)
    SS[store_key] = SS[wk]
    return SS[store_key]


# --------------------------------------------------------------------------- #
# 2b. PROGRESS PERSISTENCE (save / resume via student_store)
# --------------------------------------------------------------------------- #
# Session-state keys that make up a student's saved progress. Everything else
# (widget states, RNG helpers, figures) is transient and rebuilt each run.
PROGRESS_KEYS = [
    # scenario identity & worksheet versioning
    "rand_seed", "chase_ver", "level_ver",
    # student identity fields
    "student_name", "section",
    # stage-completion gates
    "cols_done", "stage4_done", "cap_done",
    # plan settings
    "chase_policy", "chase_whole", "chase_maintain",
    "level_whole", "level_maintain", "level_prod", "level_workers",
    # the worksheets the student builds (DataFrames)
    "chase_ws", "level_ws",
    # computed plan summaries (dicts of numbers)
    "chase_summary", "level_summary",
    # predict-then-verify answers
    "chase_pred", "level_pred", "cmp_pred",
    # challenge metrics
    "chase_tries", "chase_stuck", "chase_completed",
    "level_tries", "level_stuck", "level_completed",
    # recommendation / choice / reflection
    "recommendation", "chosen_plan",
    "refl_stability", "refl_responsive", "refl_surprise",
    # design challenge
    "design_best",
]
# Design-challenge quarter inputs (dc_q0..dc_qN) are captured by prefix.
_PROGRESS_PREFIXES = ("dc_q",)


def _json_safe(v):
    """Make any value JSON-serializable: numpy → python, sets → sorted lists,
    DataFrames → {'__df__': columns dict}, NaN/inf → None."""
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, pd.DataFrame):
        return {"__df__": _json_safe(v.to_dict("list"))}
    if isinstance(v, set):
        return sorted(_json_safe(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if hasattr(v, "tolist"):            # numpy arrays & scalars, pandas Series
        try:
            return _json_safe(v.tolist())
        except Exception:
            pass
    if isinstance(v, float):
        return None if (v != v or v in (float("inf"), float("-inf"))) else v
    if isinstance(v, int):
        return v
    return str(v)


def _in_progress_keys(k):
    return k in PROGRESS_KEYS or (isinstance(k, str)
                                  and k.startswith(_PROGRESS_PREFIXES))


def progress_snapshot():
    """A plain-JSON snapshot of just the progress keys currently in session_state."""
    snap = {}
    keys = list(PROGRESS_KEYS) + [k for k in list(SS.keys()) if _in_progress_keys(k)]
    for k in keys:
        if k in SS and k not in snap:
            snap[k] = _json_safe(SS[k])
    return snap


def restore_progress():
    """Once per session, copy a saved record back into session_state (progress keys
    only). No-op when storage is disabled or nothing is saved."""
    if SS.get("_restored"):
        return
    SS["_restored"] = True
    if not (store.enabled() and SID):
        return
    saved = store.load(GAME, SID)
    if not saved:
        return
    for k, v in saved.items():
        if not _in_progress_keys(k):
            continue
        if isinstance(v, dict) and "__df__" in v:
            SS[k] = pd.DataFrame(v["__df__"])
        else:
            SS[k] = v


def autosave_progress():
    """Persist progress when it has changed since the last save (dedup by content).
    No-op when storage is disabled. Safe to call at the end of every run."""
    if not (store.enabled() and SID):
        return
    try:
        snap = progress_snapshot()
        blob = json.dumps(snap, separators=(",", ":"), sort_keys=True)
        if blob != SS.get("_last_saved_blob"):
            if store.save(GAME, SID, snap):
                SS["_last_saved_blob"] = blob
    except Exception:
        pass


# Restore a returning student's progress before the scenario is built, so a restored
# rand_seed drives the same forecast they left off on.
restore_progress()

# Give each student a unique, easy-to-calculate scenario (stable within a session).
# An instructor-fixed 12-month demand (forecast_demand) wins over the seeded generator.
if CFG["forecast_demand"]:
    FORECAST = dict(zip(MONTHS, CFG["forecast_demand"]))
else:
    FORECAST = generate_forecast(SS.rand_seed)
TOTAL_DEMAND = sum(FORECAST.values())


def shuffled(key, items):
    """Stable-per-session shuffled order for a set of options."""
    if key not in SS:
        r = random.Random(SS.rand_seed + (abs(hash(key)) % 100000))
        lst = list(items)
        r.shuffle(lst)
        SS[key] = lst
    return SS[key]


def money(x):
    return f"${x:,.0f}"


GRID_HEIGHT = 35 * 13 + 3          # tall enough to show the header + all 12 rows
WINDOW_ROWS = 3                    # compact months shown at once (default view)
WINDOW_HEIGHT = 35 * (WINDOW_ROWS + 1) + 3


def new_scenario():
    """After a student reveals the answer, give them a brand-new random forecast and
    a blank worksheet, so they must rebuild the plan and prove they learned it."""
    SS["rand_seed"] = random.randint(1, 10 ** 6)
    for k in ("chase_ws", "level_ws", "chase_summary", "level_summary"):
        SS.pop(k, None)
    SS["chase_ver"] = SS.get("chase_ver", 0) + 1
    SS["level_ver"] = SS.get("level_ver", 0) + 1
    SS["_new_scenario"] = True     # widget-key resets handled at top of next run


def chase_explanation(policy, whole=True):
    if policy == "maintain":
        prod = ("**Regular Production** = that month's demand every month "
                "(you keep the 2,400 safety stock on hand all year).")
    elif policy == "no_fire_first":
        prod = ("**Regular Production** = demand − 2,400 in January if that still "
                "keeps the starting team busy (don't lay off in month 1); afterward "
                "produce to demand as the cushion is used up.")
    else:  # use_first
        prod = ("**Regular Production** = demand − 2,400 in January (spend the "
                "cushion, end at 0), then each later month = that month's demand.")
    wk = ("ROUNDUP(Regular Production ÷ capacity/worker/month) — round up to a whole "
          "worker" if whole else
          "Regular Production ÷ capacity/worker/month — keep the fraction; "
          "**Hours/Day** = Workers × 8")
    if whole:
        hf = ("3. **Hires** = MAX(0, this month's Workers − last month's); **Layoffs** "
              "= MAX(0, last − this). January compares to the starting 8 workers.\n"
              "5. **Costs**: Labor = Workers × $3,200; Hiring = Hires × $600; Layoff = "
              "Layoffs × $900; Holding = Ending × $0.25; Backorder = Backorders × $1.50.")
    else:
        hf = ("3. You hire/fire **hours of capacity**: **Hires** = MAX(0, this month's "
              "Hours/Day − last month's); **Layoffs** = MAX(0, last − this). January "
              "compares to 8 × 8 = 64 h/day.\n"
              "5. **Costs**: Labor = Workers × $3,200; Hiring = Hires(hours) × $75; "
              "Layoff = Layoffs(hours) × $112.50; Holding = Ending × $0.25; Backorder = "
              "Backorders × $1.50.")
    return (
        "0. First find **capacity**: per day = bottles/hour × hours/day = 5 × 8 = 40; "
        "per month = 40 × 25 working days = **1,000** bottles/worker.\n"
        f"1. {prod}\n"
        f"2. **Workers** = {wk}.\n"
        f"{hf}\n"
        "4. **Ending Inventory** = MAX(0, Beginning + Production − Demand); "
        "**Backorders** = MAX(0, Demand − Beginning − Production). Each month's "
        "Beginning = last month's Ending balance.\n"
        "6. **Total Monthly Cost** = Labor + Hiring + Layoff + Holding + Backorder.")


def level_explanation(whole=True, maintain=False):
    end_target = PARAMS["safety_stock"] if maintain else 0
    pol = ("keep a 2,400 safety stock all year (produce to demand)" if maintain
           else "hold no safety stock — consume the 2,400 beginning inventory, end at 0")
    wk = ("ROUNDUP(Level Production ÷ capacity/worker/month); rounding up leaves slack"
          if whole else
          "Level Production ÷ capacity/worker/month (keep the fraction)")
    if whole:
        hf = ("3. **January only** you hire/fire whole workers to reach the level "
              "team: **Hires** = MAX(0, level Workers − 8), **Layoffs** = MAX(0, 8 − "
              "level Workers); every later month Hires = Layoffs = 0.\n"
              "5. **Costs**: Labor = Workers × $3,200 (constant); January Hiring = "
              "Hires × $600 or Layoff = Layoffs × $900; Holding = Ending × $0.25; "
              "Backorder = Backorders × $1.50.")
    else:
        hf = ("3. **January only** you hire/fire **hours of capacity** to reach the "
              "level team: **Hires** = MAX(0, level Hours/Day − 64), **Layoffs** = "
              "MAX(0, 64 − level Hours/Day); later months 0.\n"
              "5. **Costs**: Labor = Workers × $3,200; January Hiring = Hires(hrs) × "
              "$75 or Layoff = Layoffs(hrs) × $112.50; Holding = Ending × $0.25; "
              "Backorder = Backorders × $1.50.")
    return (
        "0. **Capacity** = 5 bottles/hour × 8 hours/day × 25 days = **1,000** "
        "bottles/worker/month.\n"
        f"Policy: {pol}.\n"
        f"1. **Level Production** = (Total Demand + {end_target:,} − 2,400) ÷ 12 — "
        "the SAME number every month.\n"
        f"2. **Workers** = {wk} (constant all year); **Hours/Day** = Workers × 8.\n"
        f"{hf}\n"
        "4. Each month: **Ending Inventory** = MAX(0, Beginning + Production − "
        "Demand); **Backorders** = MAX(0, Demand − Beginning − Production).\n"
        "6. **Total Monthly Cost** = Labor + Hiring + Layoff + Holding + Backorder.")


def evalnum(s):
    """Turn a cell entry into a number. Accepts plain numbers OR arithmetic
    formulas like '15*3200', '=2000+15000-8000'. Returns None if blank/invalid."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("$", "")
    if t == "":
        return None
    if t.startswith("="):
        t = t[1:]
    if not re.fullmatch(r"[0-9+\-*/(). ]+", t):
        return None
    try:
        return float(eval(t, {"__builtins__": {}}, {}))
    except Exception:
        return None


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


REF_RE = re.compile(r"([A-Za-z]{1,2})(\d{1,2})")


def column_letters(df):
    """A -> first column, B -> second, ... (spreadsheet-style)."""
    return {chr(ord("A") + i): c for i, c in enumerate(df.columns)}


def shift_refs(formula, delta):
    """Increment the row number of every A1-style reference by `delta`
    (used to copy a formula down a column with relative references)."""
    return REF_RE.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + delta}", str(formula))


def evaluate_grid(df):
    """Mini spreadsheet engine. Evaluate every cell, resolving A1-style cell
    references (e.g. =D1*3200, =C1+G1-B1) recursively. Blank refs act as 0,
    circular refs resolve to None. Returns {col: [value-or-None per row]}."""
    df = df.reset_index(drop=True)             # work positionally (rows 0..n-1)
    letters = column_letters(df)
    cache = {}

    def ev(r, colname, stack):
        key = (r, colname)
        if key in cache:
            return cache[key]
        if colname == "Month":
            return None
        raw = df.at[r, colname]
        if colname == "Forecast Demand":
            try:
                cache[key] = float(raw)
            except (TypeError, ValueError):
                cache[key] = None
            return cache[key]
        s = "" if (raw is None or (isinstance(raw, float) and pd.isna(raw))) \
            else str(raw).strip().replace(",", "").replace("$", "")
        if s == "":
            cache[key] = None
            return None
        if s.startswith("="):
            s = s[1:]
        if key in stack:                       # circular reference
            cache[key] = None
            return None
        stk = stack | {key}

        # named brief-data constants first (LABOR, HOLD, RATE, …)
        s = NAMED_RE.sub(lambda m: repr(float(NAMED_CONSTS[m.group(1).upper()])), s)

        def repl(m):
            cl, rn = m.group(1).upper(), int(m.group(2))
            cname = letters.get(cl)
            if cname is None or rn < 1 or rn > len(df):
                return "0"
            v = ev(rn - 1, cname, stk)
            return "0" if v is None else repr(v)

        expr = REF_RE.sub(repl, s)
        if not re.fullmatch(r"[-0-9+*/(). eE]+", expr.replace(" ", "")):
            cache[key] = None
            return None
        try:
            cache[key] = float(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            cache[key] = None
        return cache[key]

    return {c: [ev(i, c, set()) for i in range(len(df))] for c in df.columns}


def grade_from_values(df, values, truth, cols, tol=0.5):
    """Grade each editable cell's evaluated value against the truth worksheet.

    Returns (status, display):
      status  -> 'ok' / 'wrong' / 'blank'
      display -> evaluated value as a string, '' for blank, raw text if unparseable.
    """
    df = df.reset_index(drop=True)
    status, display = {}, {}
    for c in cols:
        cs, ds = [], []
        for i in range(len(MONTHS)):
            raw = df.at[i, c]
            raw = "" if (raw is None or (isinstance(raw, float) and pd.isna(raw))) \
                else str(raw).strip()
            val = values[c][i]
            tv = float(truth[c].iloc[i])
            if raw == "":
                cs.append("blank"); ds.append("")
            elif val is None:
                cs.append("wrong"); ds.append(raw)
            elif abs(val - tv) > tol:
                cs.append("wrong"); ds.append(_fmt(val))
            else:
                cs.append("ok"); ds.append(_fmt(val))
        status[c], display[c] = cs, ds
    return status, display


def apply_state(df, state):
    """Return a copy of df with the data_editor's pending edits applied
    (positionally, respecting the 1..12 row index). Does NOT mutate df."""
    d = df.copy()
    if isinstance(state, dict):
        for r, changes in state.get("edited_rows", {}).items():
            for c, v in changes.items():
                d.iat[int(r), d.columns.get_loc(c)] = v
    return d


def current_worksheet(store_key, editor_key):
    """The worksheet as the student currently sees it = stored baseline + the
    editor's live edits. We reconstruct edits instead of writing them back to the
    baseline, so the grid's data prop never changes during typing and the cell
    selection (cursor) is preserved after Enter/Tab/arrow — like Excel."""
    return apply_state(SS[store_key], st.session_state.get(editor_key))


def _window_edits(state, offset):
    """Editor edits (positions 0..2 within a 3-row window) mapped to full-row
    indices via the window's offset."""
    out = []
    if isinstance(state, dict):
        for r, ch in state.get("edited_rows", {}).items():
            for c, v in ch.items():
                out.append((offset + int(r), c, v))
    return out


def reconstruct_windowed(store_key, editor_key, offset):
    """Full 12-row worksheet = baseline + the current window's live edits
    (non-mutating, so the editor's data prop stays stable and the cursor holds)."""
    d = SS[store_key].copy()
    for r, c, v in _window_edits(st.session_state.get(editor_key), offset):
        d.iat[r, d.columns.get_loc(c)] = v
    return d


def bake_windowed(store_key, editor_key, offset):
    """Commit the current window's edits into the stored worksheet (used when the
    window changes, on navigation, or before fill-down / formula-write)."""
    d = SS[store_key]
    for r, c, v in _window_edits(st.session_state.get(editor_key), offset):
        d.iat[r, d.columns.get_loc(c)] = v
    SS[store_key] = d


def window_controls(prefix, store_key, ver):
    """Render the height toggle + month scroller and return the current view:
    (start_row, n_rows, pixel_height, row_list, editor_key). Compact = 3 months
    (default) with a slider to scroll to any months; Full = all 12 rows.
    Baking the previous window on any change keeps edits from being lost."""
    ctop = st.columns([2, 3])
    mode = ctop[0].radio("Height", ["Compact (3 months)", "Full year (12)"],
                         horizontal=False, key=f"{prefix}_mode")
    if mode.startswith("Compact"):
        first = ctop[1].slider("Scroll to first visible month",
                               1, 12 - WINDOW_ROWS + 1, 1, key=f"{prefix}_first")
        start, nrows, height = first - 1, WINDOW_ROWS, WINDOW_HEIGHT
        ctop[1].caption(f"Showing {MONTHS[start]}–{MONTHS[start + nrows - 1]}. "
                        "Slide to reach the other months; the live review follows.")
    else:
        start, nrows, height = 0, 12, GRID_HEIGHT

    winsig = (start, nrows)
    prev = SS.get(f"{prefix}_prevwin")
    if prev is not None and prev != winsig:
        bake_windowed(store_key, f"{prefix}_editor_{ver}_{prev[0]}_{prev[1]}", prev[0])
    SS[f"{prefix}_prevwin"] = winsig
    editor_key = f"{prefix}_editor_{ver}_{start}_{nrows}"
    return start, nrows, height, list(range(start, start + nrows)), editor_key


def status_metrics_html(n_ok, n_wrong, n_blank, total):
    """Three metric chips; the 'Wrong' chip flashes while any cell is wrong."""
    flash = "flashred" if n_wrong > 0 else ""
    return f"""
<style>
@keyframes flashpulse {{0%,100%{{background:#ffd6d6;color:#a30000}}
50%{{background:#ff3b3b;color:#fff}}}}
.jmchip{{display:inline-block;padding:8px 16px;border-radius:8px;margin:2px 8px 6px 0;
text-align:center;font-family:sans-serif;min-width:96px}}
.flashred{{animation:flashpulse 0.8s infinite}}
.jmlab{{font-size:12px;opacity:.8}} .jmval{{font-size:22px;font-weight:700}}
</style>
<div>
<span class="jmchip" style="background:#e6f4ea;color:#0a5c2b">
<div class="jmlab">Cells correct</div><div class="jmval">{n_ok}/{total}</div></span>
<span class="jmchip {flash}" style="background:#ffd6d6;color:#a30000">
<div class="jmlab">Wrong</div><div class="jmval">{n_wrong}</div></span>
<span class="jmchip" style="background:#fff3cd;color:#7a5b00">
<div class="jmlab">Blank</div><div class="jmval">{n_blank}</div></span>
</div>"""


def status_metrics_vertical_html(n_ok, n_wrong, n_blank, total):
    """Stacked chips for a narrow column to the LEFT of the worksheet.
    The 'Wrong' chip flashes while any cell is wrong."""
    flash = "vflash" if n_wrong > 0 else ""
    return f"""
<style>
@keyframes vflashpulse {{0%,100%{{background:#ffd6d6;color:#a30000}}
50%{{background:#ff3b3b;color:#fff}}}}
.vchip{{display:block;padding:10px;border-radius:8px;margin:0 0 8px 0;
text-align:center;font-family:sans-serif}}
.vflash{{animation:vflashpulse 0.8s infinite}}
.vlab{{font-size:12px;opacity:.85}} .vval{{font-size:24px;font-weight:800;line-height:1}}
</style>
<div class="vchip" style="background:#e6f4ea;color:#0a5c2b">
<div class="vlab">Correct</div><div class="vval">{n_ok}/{total}</div></div>
<div class="vchip {flash}" style="background:#ffd6d6;color:#a30000">
<div class="vlab">Wrong</div><div class="vval">{n_wrong}</div></div>
<div class="vchip" style="background:#fff3cd;color:#7a5b00">
<div class="vlab">Blank</div><div class="vval">{n_blank}</div></div>"""


# Per-column rule + the most likely mistake, phrased so the "why" is explained
# without handing over the target number. {L} placeholders are filled with the
# letters of the driver columns for the current worksheet, e.g. G1, D1.
DIAGNOSTICS = {
    "Beginning Inventory":
        ("this month should start where last month ended (January = 2,400)",
         "you may have re-typed 2,400 every month, or not carried the previous "
         "Ending Inventory (minus any backorder) forward"),
    "Workers":
        ("Workers = ROUNDUP(Regular Production ÷ 1,000)  (e.g. ={L_regprod}/RATE)",
         "it must match THIS row's Regular Production; a fraction rounds up to a "
         "whole worker"),
    "Hires":
        ("Hires = MAX(0, this month's Workers − last month's Workers)",
         "you may have recorded a hire when the workforce did not grow, or missed "
         "one when it did (January compares to the starting 8)"),
    "Layoffs":
        ("Layoffs = MAX(0, last month's Workers − this month's Workers)",
         "you may have logged a layoff when the workforce did not shrink, or a "
         "negative number"),
    "Regular Production":
        ("Regular Production = bottles you must make this month, adjusted for "
         "beginning inventory per your policy",
         "under 'use first', month 1 consumes the 2,400 cushion (produce demand − "
         "2,400); 'maintain' keeps the 2,400 so you produce to demand"),
    "Ending Inventory":
        ("Ending Inventory = MAX(0, Beginning + Production − Demand)",
         "you may have forgotten a term, used Demand instead of Production, or not "
         "floored a negative balance at 0 (that part is a backorder, not inventory)"),
    "Backorders":
        ("Backorders = MAX(0, Demand − Beginning − Production)",
         "you may have put the shortage into Ending Inventory instead, or left it 0"),
    "Regular Labor Cost":
        ("Regular Labor Cost = Workers × $3,200  (e.g. ={L_workers}×3200)",
         "check the Workers cell and the $3,200 rate"),
    "Hiring Cost":
        ("Hiring Cost = Hires × $600  (e.g. ={L_hires}×600)",
         "check the Hires cell and the $600 rate"),
    "Layoff Cost":
        ("Layoff Cost = Layoffs × $900  (e.g. ={L_layoffs}×900)",
         "check the Layoffs cell and the $900 rate"),
    "Holding Cost":
        ("Holding Cost = Ending Inventory × $0.25  (e.g. ={L_endinv}×0.25)",
         "check the Ending Inventory cell and the $0.25 rate"),
    "Backorder Cost":
        ("Backorder Cost = Backorders × $1.50  (e.g. ={L_backord}×1.5)",
         "check the Backorders cell and the $1.50 rate"),
    "Total Monthly Cost":
        ("Total = Labor + Hiring + Layoff + Holding + Backorder for that row",
         "you may have missed a cost column or added an extra one"),
}


# Level worksheet has no Workers/Hires/Layoffs columns and produces a constant
# rate, so two rules differ from the chase version.
LEVEL_DIAGNOSTICS = dict(DIAGNOSTICS)
LEVEL_DIAGNOSTICS["Regular Production"] = (
    "Regular Production = your constant monthly level rate (identical every month)",
    "every row should equal the level production you chose above")
LEVEL_DIAGNOSTICS["Regular Labor Cost"] = (
    "Regular Labor Cost = constant workforce × $3,200 (identical every month)",
    "it should be the same value each month; check the workforce and the $3,200 rate")


def diagnose_cell(col, i, student_val, truth, letters_for, rules=DIAGNOSTICS):
    """Explain WHY a specific cell is wrong: direction, the rule, the likely cause."""
    tv = float(truth[col].iloc[i])
    if student_val is None:
        direction = "isn't a valid number/formula"
    elif student_val > tv:
        direction = "is **too high**"
    elif student_val < tv:
        direction = "is **too low**"
    else:
        direction = "is off"
    rule, cause = rules.get(col, ("", ""))
    rule = rule.format(**letters_for)
    shown = "" if student_val is None else f" (you have {_fmt(student_val)})"
    return (f"🔴 **{MONTHS[i]} · {col}** {direction}{shown}. "
            f"Rule: {rule}. Likely cause: {cause}.")


VALUE_LABELS = {
    "LABOR": "Labor rate $3,200", "HIRE": "Hire $600 / worker",
    "LAYOFF": "Layoff $900 / worker", "HIREHR": "Hire $75 / hour-per-day",
    "LAYOFFHR": "Layoff $112.50 / hour-per-day", "HOLD": "Holding rate $0.25",
    "BACKORDER": "Backorder rate $1.50", "OVERTIME": "Overtime $4.50",
    "SUBCONTRACT": "Subcontract $5.25", "RATE": "Bottles/worker 1,000",
    "BEGIN": "Beginning inv 2,400", "SAFETY": "Safety stock 2,400",
    "MAXINV": "Max inventory 30,000",
}


def build_formula_ui(prefix, editcols, ws_key, ver_key, df, editor_key, offset=0):
    """Step-by-step formula builder with a live preview. The formula string is a
    plain session value (no widget key), so any step can safely append to it."""
    name_to_letter = {c: L for L, c in column_letters(df).items()}
    expr_key = f"{prefix}_expr"
    SS.setdefault(expr_key, "=")

    st.caption("Build a formula one piece at a time — watch it grow in the box below. "
               "(You can also just type formulas straight into the grid cells.)")

    # ---- live preview + clear/backspace ----
    st.markdown(f"### `{SS[expr_key] or '='}`")
    pcols = st.columns(2)
    if pcols[0].button("⌫ Delete last", key=f"{prefix}_back"):
        s = SS[expr_key]
        m = re.search(r"([A-Za-z]{1,2}\d{1,2}|[A-Za-z]+|.)$", s[1:]) if len(s) > 1 else None
        SS[expr_key] = s[:-(len(m.group(1)))] if m else "="
        st.rerun()
    if pcols[1].button("🗑 Clear", key=f"{prefix}_clear"):
        SS[expr_key] = "="
        st.rerun()

    # ---- Step 1: a cell value ----
    st.markdown("**① Add a cell** (a value from the worksheet)")
    a, b, c = st.columns([3, 2, 2])
    ref_col = a.selectbox("cell", list(df.columns), key=f"{prefix}_rcol",
                          format_func=lambda cn: f"{cn}  (col {name_to_letter[cn]})",
                          label_visibility="collapsed")
    ref_month = b.selectbox("month", MONTHS, key=f"{prefix}_rrow",
                            format_func=lambda m: f"{m} (row {MONTHS.index(m) + 1})",
                            label_visibility="collapsed")
    if c.button("Add cell ➕", key=f"{prefix}_insref", use_container_width=True):
        SS[expr_key] += f"{name_to_letter[ref_col]}{MONTHS.index(ref_month) + 1}"
        st.rerun()

    # ---- Step 2: an operator ----
    st.markdown("**② Add an operator**")
    for (lab, op), oc in zip([("+", "+"), ("−", "-"), ("×", "*"), ("÷", "/"),
                              ("(", "("), (")", ")")], st.columns(6)):
        if oc.button(lab, key=f"{prefix}_op_{op}", use_container_width=True):
            SS[expr_key] += op
            st.rerun()

    # ---- Step 3: a rate/value from the brief ----
    st.markdown("**③ Add a rate or number** (from the brief)")
    v1, v2 = st.columns([3, 2])
    nm = v1.selectbox("value", list(NAMED_CONSTS),
                      format_func=lambda k: VALUE_LABELS.get(k, k),
                      key=f"{prefix}_nm", label_visibility="collapsed")
    if v2.button("Add value ➕", key=f"{prefix}_insnm", use_container_width=True):
        SS[expr_key] += nm
        st.rerun()

    # ---- Step 4: drop it into a cell ----
    st.markdown("**④ Put the formula into a cell**")
    w1, w2, w3 = st.columns([3, 2, 2])
    tgt_col = w1.selectbox("into", editcols, key=f"{prefix}_tcol",
                           label_visibility="collapsed")
    tgt_month = w2.selectbox("row", MONTHS, key=f"{prefix}_trow",
                             format_func=lambda m: f"{m} (row {MONTHS.index(m) + 1})",
                             label_visibility="collapsed")
    if w3.button("✍️ Write", key=f"{prefix}_write", use_container_width=True):
        bake_windowed(ws_key, editor_key, offset)         # keep the window's typed edits
        d = SS[ws_key]
        d.iat[MONTHS.index(tgt_month), d.columns.get_loc(tgt_col)] = SS[expr_key]
        SS[ws_key] = d
        SS[ver_key] += 1
        SS[expr_key] = "="
        st.rerun()
    st.caption("Example: for March Holding Cost → add cell **Ending Inventory / March**, "
               "add **×**, add **Holding rate $0.25**, then Write into "
               "**Holding Cost / March**.")


def letter_column_config(df, editcols):
    """Show spreadsheet column letters (A, B, C…) in the editor headers while
    keeping the real column names as data keys."""
    cfg = {}
    for L, cname in column_letters(df).items():
        label = f"{L} · {cname}"
        if cname in editcols:
            cfg[cname] = st.column_config.TextColumn(
                label, help="number, formula, or cell ref (e.g. =G1*3200)")
        else:
            cfg[cname] = st.column_config.Column(label, disabled=True)
    return cfg


def with_row_numbers(df):
    """Display copy whose index shows Excel-style row numbers 1..12."""
    d = df.copy()
    d.index = [str(i + 1) for i in range(len(d))]
    return d


def worksheet_html(edited, status, display, show_cols, rows=None, totals=None):
    """Read-only HTML mirror with Excel-style row numbers and column letters.
    Formulas resolved; wrong cells red, blanks amber. `rows` limits which month
    rows are shown; `totals` (dict col->number) adds a 12-month sum footer row.
    Dependency-free."""
    if rows is None:
        rows = range(len(MONTHS))
    gut = ("padding:4px 8px;border:1px solid #ccc;background:#f0f2f6;"
           "font-weight:700;color:#555;height:27px")
    letter_row = f"<th style='{gut}'></th>" + "".join(
        f"<th style='{gut};text-align:center'>{chr(ord('A') + j)}</th>"
        for j in range(len(show_cols)))
    name_row = f"<th style='{gut}'>#</th>" + "".join(
        f"<th style='padding:5px 9px;border:1px solid #ccc;background:#f0f2f6;"
        f"text-align:{'left' if c == 'Month' else 'right'}'>{c}</th>"
        for c in show_cols)
    body = ""
    for i in rows:
        cells = f"<td style='{gut};text-align:center'>{i + 1}</td>"
        for c in show_cols:
            if c in display:
                shown, s = display[c][i], status[c][i]
            else:
                shown, s = edited[c].iloc[i], None
            align = "left" if c == "Month" else "right"
            style = f"padding:5px 9px;border:1px solid #ccc;text-align:{align}"
            if s == "wrong":
                style += ";background:#ffd6d6;color:#a30000;font-weight:700"
            elif s == "blank":
                style += ";background:#fff3cd;color:#7a5b00"
            cells += f"<td style='{style}'>{shown}</td>"
        body += f"<tr>{cells}</tr>"
    if totals is not None:
        tot = ("padding:5px 9px;border:1px solid #ccc;background:#eef2ff;"
               "font-weight:700;color:#1f2a5a;text-align:right")
        foot = f"<td style='{gut};text-align:center'>Σ</td>"
        for c in show_cols:
            if c == "Month":
                foot += f"<td style='{tot};text-align:left'>12-month total</td>"
            elif c in totals:
                foot += f"<td style='{tot}'>{totals[c]}</td>"
            else:
                foot += f"<td style='{tot}'></td>"
        body += f"<tr>{foot}</tr>"
    return (f"<div style='overflow-x:auto'><table style='border-collapse:collapse;"
            f"font-size:13px'><thead><tr>{letter_row}</tr><tr>{name_row}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


# ---- Always-available brief data (sidebar, open on every stage) ----------- #
def render_reference_data(where):
    fdf = pd.DataFrame({"Month": MONTHS,
                        "Demand": [FORECAST[m] for m in MONTHS]})
    cost_rows = [
        ("Beginning inventory", "2,400 bottles"),
        ("Safety stock (if policy keeps one)", "2,400 bottles"),
        ("Bottles / worker / hour", "5"),
        ("Hours / day (full shift)", "8"),
        ("Working days / month", "25"),
        ("Regular labor", "$3,200 / full worker / mo"),
        ("Hiring", "$600 / worker"),
        ("Layoff", "$900 / worker"),
        ("Holding", "$0.25 / bottle / mo"),
        ("Backorder", "$1.50 / bottle / mo"),
        ("Overtime", "≤20% above reg · $4.50/bottle"),
        ("Subcontract", "$5.25 / bottle"),
        ("Max inventory", "30,000 bottles"),
        ("Spoilage", "≤ 2 months future demand"),
        ("Starting workforce", "8 workers"),
    ]
    where.markdown(f"**Forecast — total {TOTAL_DEMAND:,}**")
    where.dataframe(fdf, hide_index=True, use_container_width=True, height=250)
    where.markdown("**Cost & capacity**")
    where.dataframe(pd.DataFrame(cost_rows, columns=["Item", "Value"]),
                    hide_index=True, use_container_width=True, height=250)


def worker_model_control(prefix):
    """Worker-model toggle for a build stage. Capacity is computed in its own
    stage (cap_done); here we just show it and require it. Returns (whole, cap_ok)."""
    p = PARAMS
    cap_day = p["bottles_per_hour"] * p["hours_per_day"]
    cap_month = cap_day * p["working_days"]
    model = st.radio("Worker model",
                     ["Whole workers (round up)", "Partial workers (hours/day)"],
                     horizontal=True, key=f"{prefix}_model")
    whole = model.startswith("Whole")
    if SS.get("cap_done"):
        st.caption(f"Capacity (from your Capacity challenge ✔): **{cap_day}** "
                   f"bottles/worker/day · **{cap_month}** bottles/worker/month.")
    else:
        st.warning("⚠️ Finish the **Capacity** stage first — its numbers feed this plan.")
    if whole:
        st.caption("**Whole workers**: round the workforce **up** each month; hire/fire "
                   "**whole workers** ($600 / $900 each).")
    else:
        st.caption("**Partial workers**: fractional workforce shown as **Hours/Day = "
                   "workers × 8**; hire/fire **hours of capacity** ($75 HIREHR / "
                   "$112.50 LAYOFFHR per hour-per-day). Starting team = 8 workers = 64 h/day.")
    return whole, bool(SS.get("cap_done"))


STAGES = [
    "0 · Scenario Brief",
    "1 · Understand the Problem",
    "2 · What Data Do You Need?",
    "3 · Choose Your Columns",
    "4 · Build the Formulas",
    "5 · Practice: Level & Chase Basics",
    "6 · Capacity: Bottles per Worker",
    "7 · Chase Plan (you build it)",
    "8 · Level Plan (you build it)",
    "9 · Compare Strategies",
    "10 · Design Challenge (beat the plans)",
    "11 · Performance Report",
]

st.sidebar.title("🧃 Juicetification")
st.sidebar.caption("Aggregate Anxiety")
if store.enabled() and SID:
    st.sidebar.caption(f"Signed in as **{SID}** · progress saved automatically")
stage = st.sidebar.radio("Simulation stage", STAGES, index=0,
                         label_visibility="collapsed")

# After a "reveal → new scenario", clear reveal + total-cost inputs before widgets render.
if SS.pop("_new_scenario", False):
    for k in ("chase_reveal", "level_reveal", "chase_totalcost", "level_totalcost"):
        SS.pop(k, None)

# On stage change: bake any pending grid edits into the stored worksheet (so they
# survive navigation) and reset the reveal checkboxes.
if SS.get("prev_stage") != stage:
    for wk, prefix in (("chase_ws", "chase"), ("level_ws", "level")):
        vk, pw = f"{prefix}_ver", f"{prefix}_prevwin"
        if wk in SS and vk in SS and SS.get(pw) is not None:
            s0, nr = SS[pw]
            bake_windowed(wk, f"{prefix}_editor_{SS[vk]}_{s0}_{nr}", s0)
    for k in ("chase_reveal", "level_reveal"):
        SS.pop(k, None)
    SS["prev_stage"] = stage

SS.student_name = st.sidebar.text_input("Student name", SS.student_name)
SS.section = st.sidebar.text_input("Course section", SS.section)

# Brief data reachable from ANY stage
with st.sidebar.expander("📋 Brief data (open any time)", expanded=False):
    render_reference_data(st)

# Global help available on every stage
with st.sidebar.expander("❓ Help & how it works", expanded=False):
    st.markdown(
        "**The stages**\n"
        "1–4 warm you up: read the brief, spot the trade-off, pick the data and "
        "columns, and choose the right formulas.\n\n"
        "5 drills the two core calculations on quick scenarios.\n\n"
        "6 & 7 are where you **build the plan** cell by cell (chase, then level).\n\n"
        "8 compares them; 9 is your report.\n\n"
        "**Entering values**\n"
        "Type a number, or a formula starting with `=`. Formulas can use other "
        "cells (`=H5`), math (`=8*3200`), and brief-data names (`=D5*LABOR`).\n\n"
        "**If you get stuck**\n"
        "Each build stage shows the exact rule for any cell you get wrong. The "
        "**I'm stuck** box reveals a full worked example — but then gives you a "
        "fresh scenario so you actually learn it.\n\n"
        "**Tip:** rounding a workforce always goes **up** — you can't staff a "
        "fraction of a worker.")

if SS.get("stage4_done"):
    with st.sidebar.expander("📐 Quick formula reference", expanded=False):
        st.dataframe(pd.DataFrame(FORMULA_REF_CHASE, columns=["Column", "Formula"]),
                     hide_index=True, use_container_width=True, height=340)
else:
    st.sidebar.caption("🔒 Formula reference unlocks after you finish Stage 4.")

st.sidebar.caption("You are **building** the plan by hand, not filling a template.")


# ----------------------------- STAGE 0 ------------------------------------- #
if stage == STAGES[0]:
    st.title("Juicetification: Aggregate Anxiety")
    st.write(
        "You are the **operations planning analyst** for Juicetification Inc., a small juice "
        "bottler. You bottle one aggregate product family — 16-oz bottled juice — "
        "and must build a **12-month aggregate production plan** that meets the "
        "forecast at the lowest reasonable total cost while weighing workforce "
        "stability, inventory, and customer service. You'll build a **chase** plan "
        "and a **level** plan by hand, then compare them."
    )
    st.info(f"📌 This is **your unique scenario** (ID {SS.rand_seed}). Demand is "
            "randomized in whole thousands with a clean annual total, so every "
            "calculation stays tidy. Your classmates get different numbers — the "
            "method is the same, the answers are yours.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("12-Month Sales Forecast")
        st.dataframe(pd.DataFrame({"Month": MONTHS,
                     "Forecast Demand (bottles)": [FORECAST[m] for m in MONTHS]}),
                     hide_index=True, use_container_width=True)
        st.metric("Total annual demand", f"{TOTAL_DEMAND:,} bottles")
    with c2:
        st.subheader("Cost & Capacity Data")
        render_reference_data(st)
    st.divider()
    a, b = st.columns(2)
    a.success("**Chase plan** — adjust workforce to *follow* demand. Low inventory, "
              "but repeated hiring and layoffs.")
    b.warning("**Level plan** — hold production *steady* and absorb swings with "
              "inventory and backorders. Stable workforce, more inventory risk.")
    st.info("The **Brief data** panel in the sidebar is available on every stage — "
            "open it whenever you need a value.")

# ----------------------------- STAGE 1 ------------------------------------- #
elif stage == STAGES[1]:
    st.title("Stage 1 · Understand the Planning Problem")
    st.write("Frame the trade-off before calculating. No penalty here.")
    q1 = st.multiselect("1. Which months have the **highest** demand?", MONTHS)
    q2 = st.multiselect("2. Which months have the **lowest** demand?", MONTHS)
    q3 = st.radio("3. If the company produces the *same* amount every month:",
                  shuffled("s1q3", [
                      "Inventory builds in slow months and shortages appear at the peak",
                      "Costs are always minimized",
                      "The workforce must change every month"]), index=None)
    q4 = st.radio("4. If the workforce changes every month to match demand:",
                  shuffled("s1q4", [
                      "Hiring and layoff costs rise, but inventory stays low",
                      "Inventory holding cost becomes the biggest cost",
                      "Nothing changes"]), index=None)
    q5 = st.multiselect("5. Which costs tend to **rise under a chase strategy**?",
                        shuffled("s1q5", ["Hiring cost", "Layoff cost", "Holding cost",
                                          "Backorder cost", "Regular labor cost"]))
    q6 = st.multiselect("6. Which costs tend to **rise under a level strategy**?",
                        shuffled("s1q6", ["Hiring cost", "Layoff cost", "Holding cost",
                                          "Backorder cost", "Regular labor cost"]))
    with st.expander("💡 Help — how to think about this (no answers)"):
        st.write("- A **chase** plan moves *capacity* up and down. What has to change "
                 "to move capacity? What does changing it cost?")
        st.write("- A **level** plan freezes capacity. If output is flat but demand "
                 "isn't, where does the mismatch go — into stock or into shortages?")
    if st.button("Check my understanding"):
        out = []
        out.append("✅ Peak demand is June–August."
                   if set(q1) >= {"June", "July", "August"} else
                   "🔎 Re-scan the peak: the three biggest numbers sit mid-year.")
        out.append("✅ Lowest demand is Jan/Feb/Dec."
                   if set(q2) & {"January", "February", "December"} else
                   "🔎 The smallest numbers are at the start and end of the year.")
        out.append("✅ " + q3 if q3 and q3.startswith("Inventory builds") else
                   "🔎 Flat output vs. seasonal demand → stock early, shortages at peak.")
        out.append("✅ " + q4 if q4 and q4.startswith("Hiring and layoff") else
                   "🔎 Chasing demand means constantly resizing the workforce — that costs money.")
        out.append("✅ Chase → hiring & layoff costs."
                   if {"Hiring cost", "Layoff cost"} <= set(q5) else
                   "🔎 Which two costs come *only* from changing headcount?")
        out.append("✅ Level → holding & backorder costs."
                   if {"Holding cost", "Backorder cost"} <= set(q6) else
                   "🔎 Which two costs come from carrying stock or missing demand?")
        for line in out:
            st.write(line)

# ----------------------------- STAGE 2 ------------------------------------- #
elif stage == STAGES[2]:
    st.title("Stage 2 · What Data Do You Need?")
    st.write("Select **every input** the cost model requires — and nothing it doesn't.")
    required = {"Demand forecast", "Production rate (bottles per worker)",
                "Regular labor cost", "Hiring cost", "Layoff cost",
                "Holding cost", "Backorder cost", "Beginning inventory",
                "Inventory policy (safety stock or none)"}
    distractors = {"Retail selling price", "Number of delivery trucks",
                   "Annual interest rate", "Machine setup time", "Marketing budget",
                   "Warehouse square footage", "Employee turnover rate",
                   "Competitor pricing"}
    picks = st.multiselect("Inputs needed to build the plan",
                           shuffled("s2opts", required | distractors))
    with st.expander("💡 Help (no answers)"):
        st.write("For each option ask: *does a number in my worksheet multiply or "
                 "depend on it?* If nothing in the plan uses it, it's a distractor. "
                 "Selling price, financing, and facilities matter to the business but "
                 "never enter an aggregate **production** cost calc in this version.")
    if st.button("Check my data list"):
        missing = required - set(picks)
        wrong = distractors & set(picks)
        if not missing and not wrong:
            st.success("✅ Exactly the nine inputs the model needs — nothing extra.")
        else:
            if missing:
                st.warning(f"You're missing {len(missing)} required input(s). "
                           "Re-check which values your cost formulas multiply.")
            if wrong:
                st.error(f"{len(wrong)} of your picks never appear in a production "
                         "cost formula. Which ones are about sales or facilities, "
                         "not production?")

# ----------------------------- STAGE 3 ------------------------------------- #
elif stage == STAGES[3]:
    st.title("Stage 3 · Choose Your Chase-Plan Columns")
    st.write("A worksheet is only as good as its columns. Pick the columns a **chase** "
             "plan needs. Order is randomized and several options are traps.")

    good = {"Month", "Forecast Demand", "Beginning Inventory", "Workers Needed",
            "Hires", "Layoffs", "Regular Production", "Ending Inventory",
            "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
            "Holding Cost", "Backorder Cost", "Total Monthly Cost"}
    out_of_scope = {"Overtime Cost", "Subcontract Cost"}   # real, but not in v1
    traps = {"Retail Selling Price", "Units Damaged in Transit",
             "Employee Satisfaction Score", "Warehouse Square Footage",
             "Marketing Spend", "Machine Color", "Prior-Year Tax Rate",
             "Delivery Route Count"}
    picks = st.multiselect("Columns for the chase worksheet",
                           shuffled("s3opts", good | out_of_scope | traps))

    with st.expander("💡 Why each column is needed (reference)"):
        st.caption("Every legitimate aggregate-planning column and the job it does:")
        st.dataframe(pd.DataFrame(
            [(c, COLUMN_REASONS[c]) for c in
             ["Month", "Forecast Demand", "Beginning Inventory", "Workers Needed",
              "Workers Available", "Hires", "Layoffs", "Regular Production",
              "Ending Inventory", "Backorders", "Regular Labor Cost", "Hiring Cost",
              "Layoff Cost", "Holding Cost", "Backorder Cost", "Total Monthly Cost"]],
            columns=["Column", "Why it's needed"]),
            hide_index=True, use_container_width=True)

    with st.expander("💡 Help (no answers)"):
        st.write("- **Concept:** a chase plan changes capacity to follow demand — so "
                 "your columns must *show the workforce moving* and *price that movement*.")
        st.write("- **Test each candidate:** can you write a formula for it using only "
                 "the brief data? If not (e.g., *Employee Satisfaction Score*), it's a trap.")
        st.write("- Two options are real planning levers but belong to the **advanced** "
                 "version, not this first build — leave them out here.")

    if st.button("Check my columns"):
        missing = good - set(picks)
        picked_traps = traps & set(picks)
        picked_scope = out_of_scope & set(picks)
        if not missing and not picked_traps and not picked_scope:
            SS["cols_done"] = True
            st.success("✅ Clean worksheet — every column earns its place.")
        else:
            if picked_traps:
                st.error(f"{len(picked_traps)} pick(s) can't be computed from the "
                         "brief data at all — those are distractors.")
            if picked_scope:
                st.warning("Overtime / subcontracting aren't used in this first "
                           "version — leave those columns out for now.")
            if missing:
                st.warning(f"A chase plan still needs {len(missing)} more column(s). "
                           "Have you shown *how the workforce changes* and *every cost "
                           "category* that flows into Total Monthly Cost?")

# ----------------------------- STAGE 4 ------------------------------------- #
elif stage == STAGES[4]:
    st.title("Stage 4 · Build the Formulas")
    st.write("Pick the correct formula for each column. Options are shuffled.")

    defs = {
        "Required Production": (
            "Forecast Demand + Desired Ending Inventory − Beginning Inventory",
            ["Forecast Demand + Desired Ending Inventory − Beginning Inventory",
             "Forecast Demand × Holding Cost",
             "Beginning Inventory − Forecast Demand",
             "Forecast Demand + Beginning Inventory"]),
        "Workers Needed": (
            "ROUNDUP(Required Production ÷ 1,000)",
            ["ROUNDUP(Required Production ÷ 1,000)",
             "Required Production × 1,000",
             "Forecast Demand ÷ Labor Cost",
             "ROUNDDOWN(Required Production ÷ 1,000)"]),
        "Hires": (
            "MAX(0, Workers Needed − Workers Available)",
            ["MAX(0, Workers Needed − Workers Available)",
             "Workers Needed − Workers Available",
             "Forecast Demand × Hiring Cost",
             "MAX(0, Workers Available − Workers Needed)"]),
        "Layoffs": (
            "MAX(0, Workers Available − Workers Needed)",
            ["MAX(0, Workers Available − Workers Needed)",
             "Workers Needed − Workers Available",
             "Layoff Cost × Workers Needed",
             "MAX(0, Workers Needed − Workers Available)"]),
        "Ending Inventory": (
            "MAX(0, Beginning Inventory + Production − Forecast Demand)",
            ["MAX(0, Beginning Inventory + Production − Forecast Demand)",
             "Beginning Inventory − Production",
             "Forecast Demand − Production",
             "MAX(0, Forecast Demand − Production)"]),
        "Backorders": (
            "MAX(0, Forecast Demand − Beginning Inventory − Production)",
            ["MAX(0, Forecast Demand − Beginning Inventory − Production)",
             "Ending Inventory × Backorder Cost",
             "MAX(0, Production − Forecast Demand)",
             "Forecast Demand − Ending Inventory"]),
        "Holding Cost": (
            "Ending Inventory × $0.25",
            ["Ending Inventory × $0.25",
             "Beginning Inventory × $1.50",
             "Backorders × $0.25",
             "Ending Inventory × $1.50"]),
        "Total Monthly Cost": (
            "Regular Labor + Hiring + Layoff + Holding + Backorder",
            ["Regular Labor + Hiring + Layoff + Holding + Backorder",
             "Regular Labor + Holding only",
             "Forecast Demand × Labor Cost",
             "Hiring + Layoff + Holding"]),
    }
    answers = {}
    for name, (correct, opts) in defs.items():
        answers[name] = (correct, st.radio(f"**{name}** =",
                         shuffled(f"s4_{name}", opts), index=None, key=f"r_{name}"))

    with st.expander("💡 Help (no answers)"):
        st.write("- You can never hire a *fraction* of a worker, and you can't "
                 "meet demand with too few — so headcount always rounds a certain way.")
        st.write("- Hires and layoffs should never be negative; a MAX(0, …) guard "
                 "keeps each one from turning into the other.")
        st.write("- A month is either holding stock **or** backordering — never both — "
                 "so ending inventory and backorders come from the *same* balance.")
    if st.button("Grade my formulas"):
        score = 0
        for name, (correct, got) in answers.items():
            if got == correct:
                score += 1
                st.write(f"✅ {name}")
            else:
                st.write(f"❌ {name} — re-read the help hint for this one.")
        st.metric("Formula score", f"{score} / {len(defs)}")
        if score == len(defs):
            SS["stage4_done"] = True
            st.success("✅ All formulas correct — the **formula reference** is now "
                       "unlocked in the sidebar and in the build stages.")
    if SS.get("stage4_done"):
        st.caption("Formula reference unlocked. ✔")

# ----------------------------- STAGE 5 : PRACTICE -------------------------- #
elif stage == STAGES[5]:
    st.title("Stage 5 · Practice: Pure Level & Pure Chase")
    st.write("Warm up before you build the full worksheet. For each mini-scenario, "
             "compute the **pure level** production and a **pure chase** figure. Some "
             "scenarios start with inventory on hand, some don't — notice how that "
             "changes the math. Answers are checked with targeted feedback.")

    for idx, sc in enumerate(PRACTICE):
        with st.container(border=True):
            st.subheader(f"Scenario {idx + 1}: {sc['name']}")
            n = len(sc["demand"])
            months = sc["months"][:n]
            st.dataframe(pd.DataFrame({"Month": months,
                         "Demand": sc["demand"]}), hide_index=True,
                         use_container_width=True)
            total = sum(sc["demand"])
            st.caption(f"Months = {n} · Total demand = {total:,} · Beginning "
                       f"inventory = {sc['begin']:,} · Desired ending = {sc['end']:,} "
                       f"· Rate = {sc['rate']:,} units/worker")

            # correct answers
            level = (total + sc["end"] - sc["begin"]) / n
            level_workers = math.ceil(level / sc["rate"])
            chase1_req = max(0, sc["demand"][0] + sc["end"] - sc["begin"])
            chase1_workers = math.ceil(chase1_req / sc["rate"])

            c1, c2, c3 = st.columns(3)
            a_level = c1.number_input("Pure LEVEL production / month",
                                      min_value=0.0, step=1.0, key=f"pr{idx}_level")
            a_lw = c2.number_input("Workers for that level (round up)",
                                   min_value=0, step=1, key=f"pr{idx}_lw")
            a_c1 = c3.number_input(f"CHASE workers in {months[0]}",
                                   min_value=0, step=1, key=f"pr{idx}_c1")

            if st.button("Check this scenario", key=f"pr{idx}_btn"):
                # LEVEL production
                if abs(a_level - level) < 0.5:
                    st.success(f"✅ Level production = {level:g} "
                               f"= ({total:,} + {sc['end']:,} − {sc['begin']:,}) ÷ {n}.")
                else:
                    hi = "too high" if a_level > level else "too low"
                    tip = ("remember to subtract the beginning inventory — it reduces "
                           "how much you must produce") if sc["begin"] else \
                          ("with no beginning inventory, just (total demand + desired "
                           "ending) ÷ months")
                    st.error(f"❌ Level production is {hi}. Formula: "
                             f"(total demand + desired ending − beginning) ÷ months. {tip}.")
                # LEVEL workers
                if a_lw == level_workers:
                    st.success(f"✅ Level workers = ROUNDUP({level:g} ÷ {sc['rate']:,}) "
                               f"= {level_workers}.")
                else:
                    st.error("❌ Level workers: divide your level production by the rate "
                             "and **round up** (you can't staff a fraction of a worker).")
                # CHASE first-month workers
                if a_c1 == chase1_workers:
                    st.success(f"✅ Chase {months[0]} workers = ROUNDUP(({sc['demand'][0]:,}"
                               f" + {sc['end']:,} − {sc['begin']:,}) ÷ {sc['rate']:,}) "
                               f"= {chase1_workers}.")
                else:
                    tip = ("the beginning inventory covers part of month 1, so you need "
                           "fewer workers") if sc["begin"] else \
                          ("no starting inventory, so you must produce the whole month-1 "
                           "demand plus any desired ending")
                    st.error(f"❌ Chase {months[0]} workers: required production = "
                             f"demand + desired ending − beginning, then ÷ rate, round up. {tip}.")

    st.info("Once these feel automatic, move on to build the full 12-month plans.")

# ----------------------------- STAGE 6 : CAPACITY -------------------------- #
elif stage == STAGES[6]:
    st.title("Stage 6 · Capacity: how much can one worker make?")
    st.write("Before building any plan, work out one worker's output. These numbers "
             "feed **both** the chase and level builders, so get them right here first.")
    p = PARAMS
    cap_day_true = p["bottles_per_hour"] * p["hours_per_day"]        # 40
    cap_month_true = cap_day_true * p["working_days"]                # 1000
    st.dataframe(pd.DataFrame(
        [("Bottles / worker / hour", p["bottles_per_hour"]),
         ("Hours / day (full shift)", p["hours_per_day"]),
         ("Working days / month", p["working_days"])],
        columns=["Given", "Value"]), hide_index=True, use_container_width=True)

    c1, c2 = st.columns(2)
    capd = c1.number_input("Capacity per worker per **DAY** (bottles)",
                           min_value=0, step=1, key="cap_capday",
                           help="bottles/hour × hours/day")
    capm = c2.number_input("Capacity per worker per **MONTH** (bottles)",
                           min_value=0, step=50, key="cap_capmonth",
                           help="capacity/day × working days")
    if st.button("Check capacity"):
        ok_d, ok_m = capd == cap_day_true, capm == cap_month_true
        if ok_d:
            st.success(f"✅ Per day = {p['bottles_per_hour']} × {p['hours_per_day']} "
                       f"= **{cap_day_true}** bottles.")
        else:
            st.error("❌ Per day = bottles/hour × hours/day.")
        if ok_m:
            st.success(f"✅ Per month = {cap_day_true} × {p['working_days']} days "
                       f"= **{cap_month_true}** bottles.")
        else:
            st.error("❌ Per month = capacity/day × working days.")
        if ok_d and ok_m:
            SS["cap_done"] = True
            st.success("🎉 Capacity confirmed — it now passes to the chase and level "
                       "builders. Also note: a full worker = 8 h/day, so hiring an hour "
                       "of capacity costs $600 ÷ 8 = **$75**, and firing one costs "
                       "$900 ÷ 8 = **$112.50**.")
    if SS.get("cap_done"):
        st.info(f"Capacity challenge complete ✔ — **{cap_day_true}** /day, "
                f"**{cap_month_true}** /month are locked in for your plans.")

# ----------------------------- STAGE 7 : CHASE (student builds) ------------ #
elif stage == STAGES[7]:
    st.title("Stage 7 · Build the Chase Plan (by hand)")
    st.write("**You** fill in the whole worksheet, left to right. First work out each "
             "month's **Regular Production** (adjusting for beginning inventory per your "
             "policy), then the **Workers** it needs, then the rest. Cells accept a "
             "number, a formula, or **cell references** — e.g. `=D1/RATE` or `=C1+D1-B1`. "
             "Formulas resolve automatically and the live review updates as you type.")

    editcols = ["Beginning Inventory", "Regular Production", "Workers", "Hours/Day",
                "Hires", "Layoffs", "Ending Inventory", "Backorders",
                "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
                "Holding Cost", "Backorder Cost", "Total Monthly Cost"]

    if "chase_ws" not in SS or list(SS.chase_ws.columns)[2:] != editcols:
        data = {"Month": MONTHS, "Forecast Demand": [FORECAST[m] for m in MONTHS]}
        for c in editcols:
            data[c] = [""] * 12
        df0 = pd.DataFrame(data)          # nothing pre-filled — the student fills it all
        df0.index = range(1, 13)          # Excel-style row numbers 1..12
        SS.chase_ws = df0
    SS.setdefault("chase_ver", 0)

    whole, cap_ok = worker_model_control("chase")

    # ---- beginning-inventory policy (changes what "correct" means) ----
    policy = st.radio(
        "How should the 2,400 beginning inventory be handled?",
        list(BINV_POLICIES.keys()),
        format_func=lambda k: BINV_POLICIES[k], key="chase_binv")
    if policy == "use_first":
        st.caption("The 2,400 is a one-time cushion that **must be consumed in "
                   "January** — January produces demand − 2,400 (fewer workers) and "
                   "ends at 0. It is *not* kept as inventory. No safety stock afterward.")
    elif policy == "maintain":
        st.caption("The 2,400 stays on hand as safety stock every month — you produce "
                   "to demand and pay holding on the buffer all year.")
    else:
        st.caption("Consume the cushion as needed, but keep the January team (no "
                   "layoff) when current workforce + inventory already covers January.")
    maintain = binv_maintains(policy)

    st.caption(f"Starting workforce before January = {PARAMS['starting_workforce']} "
               "workers. In a pure chase plan the workforce is dictated by demand, so "
               "each month has one correct set of values (they change with the policy).")

    # ---- prescribed correct chase plan for this policy & worker model ----
    wmode = "whole" if whole else "partial"
    SS["chase_policy"], SS["chase_whole"], SS["chase_maintain"] = policy, whole, maintain
    ref_w, ref_p = reference_chase(policy, whole)
    truth = build_plan(ref_w, ref_p, maintain_safety=maintain, worker_mode=wmode)

    hb1, hb2 = st.columns(2)
    with hb1.expander("📎 Brief-data values for formulas"):
        st.caption("Use these names in any formula, e.g. `=D1*LABOR`. Forecast "
                   "Demand is column **B**.")
        st.dataframe(pd.DataFrame(
            [(k, v) for k, v in NAMED_CONSTS.items()], columns=["Name", "Value"]),
            hide_index=True, use_container_width=True, height=200)
    if SS.get("stage4_done"):
        with hb2.expander("📐 Formulas (from Stage 4) — use these to fill each column"):
            st.dataframe(pd.DataFrame(FORMULA_REF_CHASE, columns=["Column", "Formula"]),
                         hide_index=True, use_container_width=True, height=200)
    else:
        hb2.caption("🔒 Finish **Stage 4** to unlock the formula reference here.")

    # ---- #1 predict-then-verify: commit a guess before building ----
    with st.expander("🔮 Predict first (before you build)", expanded=False):
        persist_radio("A **chase** plan follows demand. Which cost do you think will "
                      "dominate it?",
                      ["Hiring + layoff (workforce changes)", "Holding inventory"],
                      "chase_pred", horizontal=True)
        st.caption("You'll find out whether you were right once the plan is complete.")

    # ---- height toggle + month scroller (editor and review move together) ----
    start, nrows, height, win_rows, editor_key = window_controls(
        "chase", "chase_ws", SS.chase_ver)

    # TOP: working spreadsheet on the LEFT, live review immediately on its RIGHT
    grid_col, review_col = st.columns(2)
    grid_col.markdown("**Your worksheet** — type here")
    grid_col.data_editor(
        SS.chase_ws.iloc[start:start + nrows], hide_index=False,
        use_container_width=True, height=height,
        column_config=letter_column_config(SS.chase_ws, editcols), key=editor_key)
    edited = reconstruct_windowed("chase_ws", editor_key, start)   # full 12-row view
    grid_col.caption("Type a value then **Enter** (down) or **Tab** (right) to commit "
                     "and move.")

    values = evaluate_grid(edited)
    status, display = grade_from_values(edited, values, truth, editcols)

    # Workers depends on THIS row's Regular Production (neutral until it's filled);
    # rounded UP for whole workers, exact fraction for partial. Hours/Day = Workers×8.
    cap_m = PARAMS["bottles_per_worker"]
    hpd = PARAMS["hours_per_day"]

    def _raw(col, i):
        v = edited.iat[i, edited.columns.get_loc(col)]
        return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v).strip()

    worker_expected = [None] * 12
    for i in range(12):
        if _raw("Regular Production", i) == "":
            status["Workers"][i] = "blank"          # not gradable yet
            status["Hours/Day"][i] = "blank"
            continue
        rp_val = values["Regular Production"][i]
        if i == 0 and policy == "no_fire_first" and covered_month1():
            exp = PARAMS["starting_workforce"]          # keep the team in month 1
        else:
            exp = (math.ceil(rp_val / cap_m) if whole else round(rp_val / cap_m, 4)) \
                if rp_val is not None else None
        worker_expected[i] = exp
        w_val = values["Workers"][i]
        if _raw("Workers", i) == "":
            status["Workers"][i] = "blank"
        elif w_val is not None and exp is not None and abs(w_val - exp) < 0.02:
            status["Workers"][i] = "ok"
        else:
            status["Workers"][i] = "wrong"
        # Hours/Day depends on the student's Workers (neutral until Workers filled)
        if _raw("Workers", i) == "":
            status["Hours/Day"][i] = "blank"
        else:
            hd_exp = w_val * hpd if w_val is not None else None
            hd_val = values["Hours/Day"][i]
            if _raw("Hours/Day", i) == "":
                status["Hours/Day"][i] = "blank"
            elif hd_val is not None and hd_exp is not None and abs(hd_val - hd_exp) < 0.02:
                status["Hours/Day"][i] = "ok"
            else:
                status["Hours/Day"][i] = "wrong"

    total = 12 * len(editcols)
    n_ok = sum(s == "ok" for col in status.values() for s in col)
    n_wrong = sum(s == "wrong" for col in status.values() for s in col)
    n_blank = sum(s == "blank" for col in status.values() for s in col)

    # 12-month sums for the live-review footer
    sum_cols = ["Regular Production", "Ending Inventory", "Backorders",
                "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Holding Cost",
                "Backorder Cost", "Total Monthly Cost"]
    totals = {c: _fmt(sum(v or 0 for v in values[c])) for c in sum_cols if c in editcols}

    review_col.markdown("**Live review** — same 3 months; Σ row sums all 12 months")
    review_col.markdown(worksheet_html(edited, status, display,
                        ["Month", "Forecast Demand"] + editcols, rows=win_rows,
                        totals=totals), unsafe_allow_html=True)

    # BOTTOM: two-column panel — feedback on the left, tools on the right
    st.divider()
    fb_col, tool_col = st.columns(2)

    fb_col.markdown(status_metrics_html(n_ok, n_wrong, n_blank, total),
                    unsafe_allow_html=True)
    name2let = {c: L for L, c in column_letters(edited).items()}

    def _lr(name):
        return f"{name2let.get(name, '?')}{{r}}"

    letters_for = {"L_regprod": _lr("Regular Production"), "L_workers": _lr("Workers"),
                   "L_hires": _lr("Hires"), "L_layoffs": _lr("Layoffs"),
                   "L_endinv": _lr("Ending Inventory"), "L_backord": _lr("Backorders")}
    round_word = "ROUNDUP(" if whole else "("
    wrong_cells = [(c, i) for c, cs in status.items()
                   for i, s in enumerate(cs) if s == "wrong"]
    if wrong_cells:
        fb_col.markdown("**Why cells are wrong**")
        for c, i in wrong_cells[:14]:
            if c == "Workers":
                wv, exp = values["Workers"][i], worker_expected[i]
                d = ("is **too high**" if (wv is not None and exp is not None and wv > exp)
                     else "is **too low**" if (wv is not None and exp is not None and wv < exp)
                     else "doesn't match Regular Production")
                fb_col.warning(
                    f"🔴 **{MONTHS[i]} · Workers** {d}. Rule: Workers = {round_word}"
                    f"Regular Production ÷ capacity/worker/month)"
                    + ("  — round up to a whole worker." if whole
                       else "  — keep the fraction (partial workers)."))
            elif c == "Hours/Day":
                fb_col.warning(f"🔴 **{MONTHS[i]} · Hours/Day** is off. Rule: "
                               f"Hours/Day = Workers × 8 (a full worker = 8 h/day).")
            elif c in ("Hires", "Layoffs") and not whole:
                base = "Hours/Day" if not whole else "Workers"
                sign = "increase" if c == "Hires" else "drop"
                fb_col.warning(f"🔴 **{MONTHS[i]} · {c}** is off. Partial mode hires/fires "
                               f"**hours of capacity**: {c} = MAX(0, {sign} in **Hours/Day** "
                               f"vs last month). January compares to 8 workers × 8 = 64 h/day.")
            elif c in ("Hiring Cost", "Layoff Cost") and not whole:
                rate = "HIREHR ($75/hour-per-day)" if c == "Hiring Cost" \
                    else "LAYOFFHR ($112.50/hour-per-day)"
                src = "Hires" if c == "Hiring Cost" else "Layoffs"
                fb_col.warning(f"🔴 **{MONTHS[i]} · {c}** is off. Partial mode: "
                               f"{c} = {src} (hours/day) × {rate}.")
            else:
                lf = {k: v.format(r=i + 1) for k, v in letters_for.items()}
                fb_col.warning(diagnose_cell(c, i, values[c][i], truth, lf))
        if len(wrong_cells) > 14:
            fb_col.caption(f"…and {len(wrong_cells) - 14} more — fix these first.")

    # ---- student enters the plan's TOTAL annual cost ----
    truth_total = float(truth["Total Monthly Cost"].sum())
    fb_col.markdown("**Total annual cost of your plan**")
    stc = fb_col.number_input("Add up all 12 months' Total Monthly Cost:",
                              min_value=0, step=1000, key="chase_totalcost")
    total_ok = abs(stc - truth_total) < 0.5
    if stc:
        if total_ok:
            fb_col.success("✅ Total annual cost is correct.")
        else:
            hi = "high" if stc > truth_total else "low"
            fb_col.warning(f"Total annual cost looks too {hi}. It's the sum of every "
                           "month's **Total Monthly Cost** (the Σ row in the review).")

    if n_wrong == 0 and n_blank == 0 and total_ok and cap_ok:
        summ = summarize(truth)
        SS["chase_summary"] = summ
        SS["chase_completed"] = True
        fb_col.success(f"✅ Chase plan complete & correct. Total = "
                       f"{money(summ['Total cost'])}, service {summ['Service level']:.1f}%.")
        hf = summ["Total hiring cost"] + summ["Total layoff cost"]
        hold = summ["Total holding cost"]
        actual = "Hiring + layoff (workforce changes)" if hf >= hold else "Holding inventory"
        if SS.get("chase_pred"):
            tick = "✓ matches" if SS["chase_pred"] == actual else "✗ differs from"
            fb_col.info(f"🔮 Prediction check: the biggest cost was **{actual}** "
                        f"(hire+fire {money(hf)} vs holding {money(hold)}) — {tick} your "
                        "prediction.")
    elif n_wrong == 0 and n_blank == 0 and not cap_ok:
        fb_col.info("Cells look right — complete the capacity calculation (Step A) to finish.")
    elif n_wrong == 0 and n_blank == 0 and not total_ok:
        fb_col.info("All cells are right — now enter the plan's total annual cost to finish.")

    with tool_col.expander("🖱️ Build a formula", expanded=False):
        build_formula_ui("chase", editcols, "chase_ws", "chase_ver", edited,
                         editor_key, offset=start)
    with tool_col.expander("📋 Fill a formula down (all 12 months)", expanded=False):
        fill_col = st.selectbox("Column", editcols, key="chase_fillcol")
        tmpl = st.text_input("Row-1 (January) formula",
                             key="chase_filltmpl", placeholder="=G1*LABOR")
        if st.button("Fill down", key="chase_fillbtn") and tmpl.strip():
            bake_windowed("chase_ws", editor_key, start)
            baked = SS.chase_ws
            for i in range(12):
                baked.iat[i, baked.columns.get_loc(fill_col)] = shift_refs(tmpl.strip(), i)
            SS.chase_ws = baked
            SS.chase_ver += 1
            st.rerun()

    # ---- FULL-WIDTH "I'm stuck": how it's done + fresh-scenario rebuild ----
    st.divider()
    chase_reveal = st.checkbox("🧑‍🏫 I'm stuck — show me how it's done", key="chase_reveal")
    if chase_reveal and not SS.get("chase_reveal_counted"):
        SS["chase_stuck"] = SS.get("chase_stuck", 0) + 1
        SS["chase_reveal_counted"] = True
    if not chase_reveal:
        SS["chase_reveal_counted"] = False
    if chase_reveal:
        st.markdown(f"#### How to build this chase plan ({BINV_POLICIES[policy]}, "
                    f"{'whole' if whole else 'partial'} workers)")
        e1, e2 = st.columns([1, 1])
        e1.markdown(chase_explanation(policy, whole))
        e2.caption("Worked solution for your current numbers:")
        e2.dataframe(truth[["Month"] + editcols], hide_index=True,
                     use_container_width=True, height=460)
        st.warning("Now prove you learned it: rebuild the plan on a **fresh forecast** "
                   "— copying this answer won't work because the numbers change.")
        if st.button("🎲 New scenario — rebuild to prove it", key="chase_newscen"):
            SS["chase_tries"] = SS.get("chase_tries", 1) + 1
            new_scenario()
            st.rerun()

# ----------------------------- STAGE 7 : LEVEL (student builds) ------------ #
elif stage == STAGES[8]:
    st.title("Stage 8 · Build the Level Plan (by hand)")
    st.write("Level = **constant** production and a **constant** workforce. In **month 1** "
             "you hire (or fire) from the starting 8 workers up to the level workforce, "
             "then hold it steady all year.")

    # ---- inventory policy (same idea as chase — it sets the ending target) ----
    lvl_policy = st.radio(
        "Inventory policy — this sets the ending target:",
        ["No safety stock (consume the 2,400, end at 0)",
         "Maintain 2,400 safety stock all year"], key="level_binv")
    maintain = lvl_policy.startswith("Maintain")
    end_target = PARAMS["safety_stock"] if maintain else 0
    st.latex(r"\text{Level Rate}=\frac{\text{Total Demand}+"
             + (r"\text{Safety }2{,}000" if maintain else r"0")
             + r"-\text{Beginning }2{,}000}{12}")

    whole, cap_ok = worker_model_control("level")
    wmode = "whole" if whole else "partial"

    # ---- the ONE correct level plan (prescriptive: reveal == what is graded) ----
    SS["level_whole"], SS["level_maintain"] = whole, maintain
    ref_w, ref_p = reference_level(whole, maintain)
    lvl_rate = level_rate(maintain)
    lvl_workers = ref_w[0]
    truth = build_plan(ref_w, ref_p, start_workers=PARAMS["starting_workforce"],
                       maintain_safety=maintain, worker_mode=wmode)

    # optional self-check: the student states the level rate & workforce first
    c1, c2 = st.columns(2)
    lp = c1.number_input("Level production / month (compute it!)", 0, 40000,
                         value=int(SS.get("level_prod", 0)), step=50, key="lp_in")
    lw = c2.number_input("Level workforce (" +
                         ("whole workers" if whole else "may be fractional") + ")",
                         0.0, 60.0, value=float(SS.get("level_workers", 0)),
                         step=(1.0 if whole else 0.5), key="lw_in")
    SS.level_prod, SS.level_workers = lp, lw
    checks = []
    if lp:
        checks.append("✅ level rate" if abs(lp - lvl_rate) < 0.5
                      else f"❌ level rate = (total demand + {end_target:,} − 2,400) ÷ 12")
    if lw:
        checks.append("✅ workforce" if abs(lw - lvl_workers) < 0.02
                      else ("❌ workforce = ROUNDUP(rate ÷ 1,000)" if whole
                            else "❌ workforce = rate ÷ 1,000 (keep the fraction)"))
    if checks:
        st.caption(" · ".join(checks))
    st.caption("Fill the worksheet with the **correct** level rate as Regular "
               "Production every month; in **January** hire/fire from the starting 8 "
               "workers up to the level workforce (the only change all year).")

    editcols = ["Beginning Inventory", "Regular Production", "Workers", "Hours/Day",
                "Hires", "Layoffs", "Ending Inventory", "Backorders",
                "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
                "Holding Cost", "Backorder Cost", "Total Monthly Cost"]
    if "level_ws" not in SS or list(SS.level_ws.columns)[2:] != editcols:
        data = {"Month": MONTHS, "Forecast Demand": [FORECAST[m] for m in MONTHS]}
        for c in editcols:
            data[c] = [""] * 12
        df0 = pd.DataFrame(data)          # nothing pre-filled — the student fills it all
        df0.index = range(1, 13)          # Excel-style row numbers 1..12
        SS.level_ws = df0
    SS.setdefault("level_ver", 0)

    hb1, hb2 = st.columns(2)
    with hb1.expander("📎 Brief-data values for formulas"):
        st.caption("Use these names in any formula, e.g. `=E1*HOLD`. Forecast "
                   "Demand is column **B**.")
        st.dataframe(pd.DataFrame(
            [(k, v) for k, v in NAMED_CONSTS.items()], columns=["Name", "Value"]),
            hide_index=True, use_container_width=True, height=200)
    if SS.get("stage4_done"):
        with hb2.expander("📐 Formulas (from Stage 4) — use these to fill each column"):
            st.dataframe(pd.DataFrame(FORMULA_REF_LEVEL, columns=["Column", "Formula"]),
                         hide_index=True, use_container_width=True, height=200)
    else:
        hb2.caption("🔒 Finish **Stage 4** to unlock the formula reference here.")

    # ---- #1 predict-then-verify ----
    with st.expander("🔮 Predict first (before you build)", expanded=False):
        persist_radio("A **level** plan holds output steady. Which cost do you think "
                      "will dominate it?",
                      ["Hiring + layoff (workforce changes)", "Holding inventory"],
                      "level_pred", horizontal=True)
        st.caption("You'll find out whether you were right once the plan is complete.")

    # ---- height toggle + month scroller (editor and review move together) ----
    start, nrows, height, win_rows, editor_key = window_controls(
        "level", "level_ws", SS.level_ver)

    # TOP: working spreadsheet LEFT, live review immediately RIGHT
    grid_col, review_col = st.columns(2)
    grid_col.markdown("**Your worksheet** — type here")
    grid_col.data_editor(
        SS.level_ws.iloc[start:start + nrows], hide_index=False,
        use_container_width=True, height=height,
        column_config=letter_column_config(SS.level_ws, editcols), key=editor_key)
    edited = reconstruct_windowed("level_ws", editor_key, start)   # full 12-row view
    grid_col.caption("Type a value then **Enter** (down) or **Tab** (right) to commit "
                     "and move.")

    values = evaluate_grid(edited)
    status, display = grade_from_values(edited, values, truth, editcols)
    total = 12 * len(editcols)
    n_ok = sum(s == "ok" for col in status.values() for s in col)
    n_wrong = sum(s == "wrong" for col in status.values() for s in col)
    n_blank = sum(s == "blank" for col in status.values() for s in col)

    sum_cols = ["Regular Production", "Ending Inventory", "Backorders",
                "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Holding Cost",
                "Backorder Cost", "Total Monthly Cost"]
    totals = {c: _fmt(sum(v or 0 for v in values[c])) for c in sum_cols if c in editcols}
    review_col.markdown("**Live review** — same 3 months; Σ row sums all 12 months")
    review_col.markdown(worksheet_html(edited, status, display,
                        ["Month", "Forecast Demand"] + editcols, rows=win_rows,
                        totals=totals), unsafe_allow_html=True)

    # BOTTOM: two-column panel — feedback left, tools right
    st.divider()
    fb_col, tool_col = st.columns(2)

    fb_col.markdown(status_metrics_html(n_ok, n_wrong, n_blank, total),
                    unsafe_allow_html=True)
    name2let = {c: L for L, c in column_letters(edited).items()}
    lvl_letters = {"L_endinv": f"{name2let.get('Ending Inventory','?')}{{r}}",
                   "L_backord": f"{name2let.get('Backorders','?')}{{r}}",
                   "L_workers": "", "L_hires": "", "L_layoffs": "", "L_regprod": ""}
    wrong_cells = [(c, i) for c, cs in status.items()
                   for i, s in enumerate(cs) if s == "wrong"]
    if wrong_cells:
        fb_col.markdown("**Why cells are wrong**")
        for c, i in wrong_cells[:14]:
            if c == "Workers":
                fb_col.warning(f"🔴 **{MONTHS[i]} · Workers** — a level plan uses the "
                               f"**same** workforce every month (= your level headcount).")
            elif c == "Hours/Day":
                fb_col.warning(f"🔴 **{MONTHS[i]} · Hours/Day** = Workers × 8 (same all year).")
            elif c in ("Hires", "Layoffs"):
                unit = "whole workers" if whole else "hours/day"
                base = ("8 workers" if whole else "64 h/day")
                fb_col.warning(f"🔴 **{MONTHS[i]} · {c}** — in a level plan the workforce "
                               f"changes **only in January** (ramp from {base} to the "
                               f"level team, in {unit}); every other month is 0.")
            elif c == "Hiring Cost":
                rate = "$600/worker" if whole else "$75/hour-per-day (HIREHR)"
                fb_col.warning(f"🔴 **{MONTHS[i]} · Hiring Cost** = Hires × {rate} "
                               "(nonzero only in January if you hired).")
            elif c == "Layoff Cost":
                rate = "$900/worker" if whole else "$112.50/hour-per-day (LAYOFFHR)"
                fb_col.warning(f"🔴 **{MONTHS[i]} · Layoff Cost** = Layoffs × {rate} "
                               "(nonzero only in January if you cut staff).")
            else:
                lf = {k: v.format(r=i + 1) for k, v in lvl_letters.items()}
                fb_col.warning(diagnose_cell(c, i, values[c][i], truth, lf,
                                             rules=LEVEL_DIAGNOSTICS))
        if len(wrong_cells) > 14:
            fb_col.caption(f"…and {len(wrong_cells) - 14} more — fix these first.")

    # ---- student enters the plan's TOTAL annual cost ----
    truth_total = float(truth["Total Monthly Cost"].sum())
    fb_col.markdown("**Total annual cost of your plan**")
    stc = fb_col.number_input("Add up all 12 months' Total Monthly Cost:",
                              min_value=0, step=1000, key="level_totalcost")
    total_ok = abs(stc - truth_total) < 0.5
    if stc:
        if total_ok:
            fb_col.success("✅ Total annual cost is correct.")
        else:
            hi = "high" if stc > truth_total else "low"
            fb_col.warning(f"Total annual cost looks too {hi}. It's the sum of every "
                           "month's **Total Monthly Cost**.")

    if n_wrong == 0 and n_blank == 0 and total_ok and cap_ok:
        summ = summarize(truth)
        SS["level_summary"] = summ
        SS["level_completed"] = True
        fb_col.success(f"✅ Level plan complete & correct. Total = "
                       f"{money(summ['Total cost'])}, service {summ['Service level']:.1f}%.")
        hf = summ["Total hiring cost"] + summ["Total layoff cost"]
        hold = summ["Total holding cost"]
        actual = "Hiring + layoff (workforce changes)" if hf >= hold else "Holding inventory"
        if SS.get("level_pred"):
            tick = "✓ matches" if SS["level_pred"] == actual else "✗ differs from"
            fb_col.info(f"🔮 Prediction check: the biggest cost was **{actual}** "
                        f"(hire+fire {money(hf)} vs holding {money(hold)}) — {tick} your "
                        "prediction.")
        if (truth["Spoilage Warning"] != "").any():
            fb_col.info("Some months exceed the inventory/spoilage limit.")
    elif n_wrong == 0 and n_blank == 0 and not cap_ok:
        fb_col.info("Cells look right — finish the **Capacity** stage to complete.")
    elif n_wrong == 0 and n_blank == 0 and not total_ok:
        fb_col.info("All cells are right — now enter the plan's total annual cost to finish.")

    with tool_col.expander("🖱️ Build a formula", expanded=False):
        build_formula_ui("level", editcols, "level_ws", "level_ver", edited,
                         editor_key, offset=start)
    with tool_col.expander("📋 Fill a formula down (all 12 months)", expanded=False):
        fill_col = st.selectbox("Column", editcols, key="level_fillcol")
        tmpl = st.text_input("Row-1 (January) formula",
                             key="level_filltmpl", placeholder="=C1+D1-B1")
        if st.button("Fill down", key="level_fillbtn") and tmpl.strip():
            bake_windowed("level_ws", editor_key, start)
            baked = SS.level_ws
            for i in range(12):
                baked.iat[i, baked.columns.get_loc(fill_col)] = shift_refs(tmpl.strip(), i)
            SS.level_ws = baked
            SS.level_ver += 1
            st.rerun()

    # ---- FULL-WIDTH "I'm stuck": how it's done + fresh-scenario rebuild ----
    st.divider()
    level_reveal = st.checkbox("🧑‍🏫 I'm stuck — show me how it's done", key="level_reveal")
    if level_reveal and not SS.get("level_reveal_counted"):
        SS["level_stuck"] = SS.get("level_stuck", 0) + 1
        SS["level_reveal_counted"] = True
    if not level_reveal:
        SS["level_reveal_counted"] = False
    if level_reveal:
        st.markdown(f"#### How to build the level plan ({'whole' if whole else 'partial'} workers)")
        st.info(f"Correct level rate = **{lvl_rate:,.0f}** bottles/month; "
                f"workforce = **{lvl_workers:g}** "
                + ("whole workers." if whole else "partial workers (a fractional value).")
                + f" January hires from 8 → {lvl_workers:g}.")
        e1, e2 = st.columns([1, 1])
        e1.markdown(level_explanation(whole, maintain))
        e2.caption("Worked solution — exactly what is graded:")
        e2.dataframe(truth[["Month"] + editcols], hide_index=True,
                     use_container_width=True, height=460)
        st.warning("Now prove you learned it: rebuild on a **fresh forecast** — copying "
                   "this answer won't work because the numbers change.")
        if st.button("🎲 New scenario — rebuild to prove it", key="level_newscen"):
            SS["level_tries"] = SS.get("level_tries", 1) + 1
            new_scenario()
            st.rerun()

# ----------------------------- STAGE 9 : COMPARE --------------------------- #
elif stage == STAGES[9]:
    st.title("Stage 9 · Compare Strategies")
    if "chase_summary" not in SS or "level_summary" not in SS:
        st.warning("Finish and pass the check on **both** plans (Stages 7 and 8) first.")
    else:
        cs, ls = SS["chase_summary"], SS["level_summary"]
        # #1 predict-then-verify: commit before reading the table
        persist_radio("Before you study the table — which plan do you **predict** is "
                      "cheaper overall?", ["Chase", "Level"], "cmp_pred", horizontal=True)
        rows = ["Total regular labor cost", "Total hiring cost", "Total layoff cost",
                "Total holding cost", "Total backorder cost", "Total cost",
                "Highest inventory", "Number of workforce changes",
                "Months with shortages", "Service level"]
        METRIC_HELP = {
            "Total regular labor cost": "Wages for the workforce (workers × $3,200/mo, "
                "summed over the year). Usually the single biggest cost.",
            "Total hiring cost": "Cost of adding capacity — $600/worker (or $75 per "
                "hour-per-day). Rises when a plan ramps staffing up and down.",
            "Total layoff cost": "Cost of cutting capacity — $900/worker (or $112.50 per "
                "hour-per-day). Chase plans that shed staff pay a lot here.",
            "Total holding cost": "Cost of storing unsold bottles (ending inventory × "
                "$0.25/mo, summed). Level plans that build inventory pay more.",
            "Total backorder cost": "Penalty for demand you can't fill on time "
                "(backorders × $1.50/mo). Signals lost service, not just money.",
            "Total cost": "The bottom line — every cost above added together. The "
                "headline number, but not the only thing that matters.",
            "Highest inventory": "The largest end-of-month stock the plan ever holds — "
                "storage space needed and spoilage risk.",
            "Number of workforce changes": "How many months the workforce moved up or "
                "down. More changes = less stable employment and more disruption.",
            "Months with shortages": "Months that ended with unmet demand (backorders). "
                "More months = worse customer service.",
            "Service level": "Share of annual demand met on time. Higher is better for "
                "customers; 100% means no backorders all year.",
        }

        def _fmtv(r, v):
            if "cost" in r.lower():
                return money(v)
            if r == "Service level":
                return f"{v:.1f}%"
            return f"{v:,}"

        # comparison table with a hover (?) explanation on every metric
        hdr = ("padding:6px 10px;border:1px solid #ccc;background:#f0f2f6;"
               "font-weight:700;text-align:left")
        cell = "padding:6px 10px;border:1px solid #ccc;text-align:right"
        body = ""
        for r in rows:
            cv, lv = cs[r], ls[r]
            tip = METRIC_HELP[r].replace('"', "'")
            name = (f"<span title=\"{tip}\" style='cursor:help;border-bottom:1px dotted "
                    f"#888'>{r} <b>ⓘ</b></span>")
            body += (f"<tr><td style='{cell};text-align:left'>{name}</td>"
                     f"<td style='{cell}'>{_fmtv(r, cv)}</td>"
                     f"<td style='{cell}'>{_fmtv(r, lv)}</td></tr>")
        st.markdown(
            f"<div style='overflow-x:auto'><table style='border-collapse:collapse;"
            f"font-size:14px'><thead><tr><th style='{hdr}'>Metric (hover ⓘ)</th>"
            f"<th style='{hdr};text-align:right'>Chase Plan</th>"
            f"<th style='{hdr};text-align:right'>Level Plan</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>", unsafe_allow_html=True)
        st.caption("Hover the **ⓘ** on any metric for what it means. Read the numbers "
                   "yourself and work through the questions below.")

        # ---- guided interpretation: let the student read the numbers ----
        st.divider()
        st.subheader("Interpret the numbers")

        def cmp_plan(metric, mode):
            a, b = cs[metric], ls[metric]
            if abs(a - b) < 1e-9:
                return "About the same"
            return ("Chase" if a < b else "Level") if mode == "lower" \
                else ("Chase" if a > b else "Level")

        opts = ["Chase", "Level", "About the same"]
        qdefs = [
            ("q_cost", "Which plan has the **lower total annual cost**?",
             cmp_plan("Total cost", "lower"),
             "Read the **Total cost** row again and compare the two numbers."),
            ("q_wf", "Which plan **changes its workforce more** (more hiring, layoffs, "
             "and workforce changes)?",
             cmp_plan("Number of workforce changes", "higher"),
             "Compare the **hiring cost**, **layoff cost**, and **Number of workforce "
             "changes** rows."),
            ("q_inv", "Which plan **keeps more bottles sitting in inventory**?",
             cmp_plan("Highest inventory", "higher"),
             "Compare the **Highest inventory** and **holding cost** rows."),
            ("q_serv", "Which plan **leaves more customer demand unmet** (worse service)?",
             cmp_plan("Months with shortages", "higher"),
             "Compare the **Months with shortages** and **Service level** rows."),
        ]
        for key, q, correct, hint in qdefs:
            ans = st.radio(q, opts, index=None, key=key, horizontal=True)
            if ans is not None:
                if ans == correct:
                    st.success("✓ Yes — that's what the numbers show.")
                else:
                    st.caption("🔎 " + hint)

        # confront the earlier prediction (only once they've read the Total cost row)
        if SS.get("q_cost") is not None:
            cheaper = cmp_plan("Total cost", "lower")
            same = SS.get("cmp_pred") == cheaper
            st.caption(f"🔮 You predicted **{SS.get('cmp_pred','—')}** would be cheaper; the "
                       f"Total cost row shows **{cheaper}** is lower — "
                       + ("your prediction held." if same else "the opposite of your guess."))

        st.text_input("In your own words, what does the **cheaper** plan give up to be "
                      "cheaper? (think about the rows above)", key="cmp_tradeoff")

        # ---- #4 concept check: WHY the numbers came out that way ----
        st.divider()
        st.subheader("Concept check — why?")
        cc = [
            ("cc1", "A level plan builds up inventory in the spring. **Why?**",
             "To cover the summer peak with steady production",
             ["To cover the summer peak with steady production",
              "Because it hired extra summer workers",
              "To use up the beginning inventory faster"],
             "Output is flat but demand rises in summer, so the plan must stockpile "
             "earlier to meet the peak."),
            ("cc2", "A chase plan has very little holding cost. **Why?**",
             "It produces close to each month's demand, so little is left over",
             ["It produces close to each month's demand, so little is left over",
              "It never hires anyone",
              "It has no workers in slow months"],
             "Chase matches output to demand, so almost nothing sits in inventory."),
            ("cc3", "Frequent hiring and layoffs mostly drive up **which** cost?",
             "Hiring + layoff cost",
             ["Hiring + layoff cost", "Holding cost", "Backorder cost"],
             "Every up/down move in the workforce triggers a $600 hire or $900 layoff."),
        ]
        for key, q, correct, options, why in cc:
            a = st.radio(q, options, index=None, key=key)
            if a is not None:
                if a == correct:
                    st.success("✓ " + why)
                else:
                    st.caption("🔎 Not quite — think about what each plan is doing month to month.")

        # ---- #5 sensitivity: what if demand changed? ----
        with st.expander("🧪 What-if: how do the plans respond to a demand change?"):
            st.caption("Scale **every** month's demand and see how each strategy's cost "
                       "and service move. The strategies re-run on the new demand.")
            factor = st.slider("Demand change", 0.7, 1.3, 1.0, 0.05,
                               format="%.2fx", key="sens_factor")
            scaled = {m: max(0, round(FORECAST[m] * factor)) for m in MONTHS}
            settings = {k: SS.get(k) for k in ("chase_whole", "chase_policy",
                        "chase_maintain", "level_whole", "level_maintain")}
            ncs, nls = strategy_summaries_for_demand(scaled, settings)
            sc1, sc2 = st.columns(2)
            sc1.metric("Chase total cost", money(ncs["Total cost"]),
                       delta=money(ncs["Total cost"] - cs["Total cost"]),
                       delta_color="inverse")
            sc1.caption(f"Service {ncs['Service level']:.1f}%")
            sc2.metric("Level total cost", money(nls["Total cost"]),
                       delta=money(nls["Total cost"] - ls["Total cost"]),
                       delta_color="inverse")
            sc2.caption(f"Service {nls['Service level']:.1f}%")
            if abs(factor - 1.0) > 1e-9:
                new_cheaper = "Chase" if ncs["Total cost"] < nls["Total cost"] else "Level"
                st.caption(f"At **{factor:.2f}×** demand, the cheaper plan is now "
                           f"**{new_cheaper}**. Did the ranking change? What does that "
                           "tell you about which strategy is more robust to demand swings?")

        # ---- student picks a plan and justifies it (widgets bound to SS keys so
        #      they persist when you navigate away and into the report) ----
        st.divider()
        st.subheader("Your recommendation")
        persist_radio("Which plan would you recommend?", ["Chase", "Level"],
                      "chosen_plan", horizontal=True)
        st.markdown("Justify it with **both** a numerical and a managerial argument:")
        persist_text_area(
            "Your justification", "recommendation", height=150,
            placeholder="e.g. I recommend the level plan: its total cost is $X lower, "
            "and although it holds more inventory the steady workforce is easier to "
            "manage and keeps service at Y%…")
        if len(SS["recommendation"].strip()) > 40:
            st.success("✅ Recommendation saved — it appears in your report.")
        else:
            st.caption("Write at least a couple of sentences citing the numbers and the "
                       "trade-offs you found above.")

        # ---- #8 structured reflection ----
        st.divider()
        st.subheader("Reflect")
        persist_text_area("Which plan gives employees more **stable, predictable** work, "
                          "and why does that matter to a plant manager?", "refl_stability",
                          height=80)
        persist_text_area("Which plan is more **responsive** to a sudden demand spike? "
                          "What did the what-if slider show?", "refl_responsive", height=80)
        persist_text_area("What **surprised** you when you compared the two plans?",
                          "refl_surprise", height=80)
        done = sum(1 for k in ("refl_stability", "refl_responsive", "refl_surprise")
                   if len(SS.get(k, "").strip()) > 15)
        st.caption(f"Reflections completed: {done}/3 — these go into your report.")

# ----------------------------- STAGE 10 : DESIGN CHALLENGE ----------------- #
elif stage == STAGES[10]:
    st.title("Stage 10 · Design Challenge — beat both textbook plans")
    if "chase_summary" not in SS or "level_summary" not in SS:
        st.warning("Build the chase and level plans first (Stages 7 and 8) — this "
                   "challenge compares against them.")
    else:
        target = min(SS["chase_summary"]["Total cost"], SS["level_summary"]["Total cost"])
        st.write("Neither pure chase nor pure level is usually best. **You** set the "
                 "workforce for each quarter — build a *hybrid*: a steady base with extra "
                 "hands only when demand peaks. Try to beat the cheaper of your two plans "
                 f"(**{money(target)}**) while keeping service high. No single right "
                 "answer — experiment with the levers.")

        peak = max(FORECAST.values())
        approx = math.ceil(peak / PARAMS["bottles_per_worker"])
        qlabels = ["Q1 (Jan–Mar)", "Q2 (Apr–Jun)", "Q3 (Jul–Sep)", "Q4 (Oct–Dec)"]
        st.markdown("**Workers each quarter** (they carry across that quarter's 3 months):")
        qc = st.columns(4)
        qworkers = []
        for i, (col, lab) in enumerate(zip(qc, qlabels)):
            SS.setdefault(f"dc_q{i}", int(approx))
            qworkers.append(col.number_input(lab, 0, 60, step=1, key=f"dc_q{i}"))
        allow_ot = st.checkbox("Allow overtime (up to +20% output per quarter, "
                               "$4.50/bottle) to trim shortages", key="dc_ot")

        # build the hybrid plan
        workers = [qworkers[i // 3] for i in range(12)]
        cap = PARAMS["bottles_per_worker"]
        production = [w * cap for w in workers]
        ot_bottles = [0] * 12
        if allow_ot:
            # add overtime where a month would otherwise backorder, up to 20%
            carry = PARAMS["beginning_inventory"]
            for i, m in enumerate(MONTHS):
                need = FORECAST[m] - carry - production[i]
                if need > 0:
                    ot = min(need, round(0.20 * production[i]))
                    ot_bottles[i] = ot
                carry = carry + production[i] + ot_bottles[i] - FORECAST[m]
        prod_total = [production[i] + ot_bottles[i] for i in range(12)]
        plan = build_plan(workers, prod_total, start_workers=PARAMS["starting_workforce"],
                          worker_mode="whole")
        summ = summarize(plan)
        ot_cost = sum(ot_bottles) * PARAMS["overtime_cost"]
        grand = summ["Total cost"] + ot_cost

        m1, m2, m3 = st.columns(3)
        m1.metric("Your total cost", money(grand),
                  delta=money(grand - target), delta_color="inverse")
        m2.metric("Service level", f"{summ['Service level']:.1f}%")
        m3.metric("Target to beat", money(target))
        if grand < target and summ["Service level"] >= 99.9:
            st.success("🏆 You beat both plans **and** met all demand — that's a strong "
                       "hybrid. What combination worked, and why?")
            SS["design_best"] = min(SS.get("design_best", grand), grand)
        elif grand < target:
            st.info(f"💡 Cheaper than the target, but service is {summ['Service level']:.1f}% "
                    "— you're leaving demand unmet. Add workers or overtime in the short "
                    "months and see if the total still beats the target.")
            SS["design_best"] = min(SS.get("design_best", grand), grand)
        else:
            st.info("Not cheaper yet. Level plans overstaff slow months; chase plans pay "
                    "to hire/fire. Find the middle: a base crew plus a summer bump.")

        with st.expander("See your hybrid plan's monthly detail"):
            st.dataframe(plan[["Month", "Forecast Demand", "Workers", "Regular Production",
                               "Ending Inventory", "Backorders", "Total Monthly Cost"]]
                         .assign(Overtime=ot_bottles),
                         hide_index=True, use_container_width=True)
        st.caption("This challenge isn't graded — it's a sandbox to feel how the levers "
                   "trade off. Your best total is saved to your report.")

# ----------------------------- STAGE 11 : REPORT --------------------------- #
elif stage == STAGES[11]:
    st.title("Stage 11 · Performance Report")
    if "chase_summary" not in SS or "level_summary" not in SS:
        st.warning("Finish and pass the check on **both** plans (Stages 7 and 8) first.")
    else:
        cs, ls = SS["chase_summary"], SS["level_summary"]
        pick = SS.get("chosen_plan", "Undecided")
        if SS.get("recommendation", "").strip():
            st.success(f"Your recommendation (from Stage 9): **{pick} plan**")
            st.write(SS["recommendation"])
        else:
            st.warning("Go to **Stage 9 · Compare Strategies** to choose a plan and write "
                       "your justification — it appears here in the report.")

        # ---- challenge metrics ----
        st.subheader("Challenge metrics")
        yn = lambda b: "Yes" if b else "No"
        mrows = [
            ("Stage 3 · Choose columns", "—", yn(SS.get("cols_done", False)), "—"),
            ("Stage 4 · Build formulas", "—", yn(SS.get("stage4_done", False)), "—"),
            ("Stage 6 · Capacity", "—", yn(SS.get("cap_done", False)), "—"),
            ("Stage 7 · Chase plan", SS.get("chase_tries", 1),
             yn(SS.get("chase_completed", False)), SS.get("chase_stuck", 0)),
            ("Stage 8 · Level plan", SS.get("level_tries", 1),
             yn(SS.get("level_completed", False)), SS.get("level_stuck", 0)),
        ]
        st.dataframe(pd.DataFrame(mrows, columns=["Challenge", "Attempts",
                     "Completed", "‘I’m stuck’ used"]),
                     hide_index=True, use_container_width=True)

        metrics_txt = "\n".join(
            f"{c:<26} attempts={a}  completed={comp}  stuck-used={s}"
            for c, a, comp, s in mrows)

        reflect_txt = "\n".join(f"- {q}\n  {SS.get(k) or '(not written)'}" for q, k in [
            ("Stable employment", "refl_stability"),
            ("Responsiveness", "refl_responsive"),
            ("What surprised you", "refl_surprise")])
        design_txt = (f"Best hybrid total cost: {money(SS['design_best'])}"
                      if SS.get("design_best") else "Not attempted")

        if st.button("Generate submission report", type="primary"):
            tag = {"Chase": "CHS", "Level": "LVL", "Undecided": "UND"}.get(pick, "UND")
            seed = SS.rand_seed
            sub_code = f"AGG-JUST-{seed % 100000:05d}-{tag}"
            now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

            # Record the completion to the student's store (no-op unless configured).
            # Score = chosen plan's total cost (or the cheaper of the two if undecided).
            _score = {"Chase": cs["Total cost"], "Level": ls["Total cost"]}.get(
                pick, min(cs["Total cost"], ls["Total cost"]))
            store.record_completion(GAME, SID, completion_code=sub_code,
                                    score=_json_safe(_score))
            report = f"""JUICETIFICATION: AGGREGATE ANXIETY — PERFORMANCE REPORT
====================================================
Student:        {SS.student_name or '(not entered)'}
Course section: {SS.section or '(not entered)'}
Completed:      {now}
Scenario:       Juicetification: Aggregate Anxiety v1  (seed {seed})
Submission code: {sub_code}

CHASE PLAN SUMMARY
------------------
Total cost:            {money(cs['Total cost'])}
Highest workforce:     {cs['Highest workforce']}
Lowest workforce:      {cs['Lowest workforce']}
Total hires:           {cs['Total hires']}
Total layoffs:         {cs['Total layoffs']}
Ending inventory:      {cs['Ending inventory']:,}
Months with shortages: {cs['Months with shortages']}
Service level:         {cs['Service level']:.1f}%

LEVEL PLAN SUMMARY
------------------
Total cost:            {money(ls['Total cost'])}
Monthly production:    {SS.get('level_prod', 0):,}
Constant workforce:    {SS.get('level_workers', 0)}
Highest inventory:     {ls['Highest inventory']:,}
Ending inventory:      {ls['Ending inventory']:,}
Months with shortages: {ls['Months with shortages']}
Service level:         {ls['Service level']:.1f}%

DESIGN CHALLENGE
------------------
{design_txt}

RECOMMENDATION ({pick})
------------------
{SS.recommendation or '(not written)'}

REFLECTION
------------------
{reflect_txt}

CHALLENGE METRICS
------------------
{metrics_txt}
"""
            st.code(report)
            st.download_button("Download report (.txt)", report,
                               file_name=f"{sub_code}.txt")
            st.success(f"Submission code: **{sub_code}**")


# --------------------------------------------------------------------------- #
# Autosave: runs at the end of every script pass, so any completed step, submitted
# answer, advanced round, or finished rush is persisted. Writes only when the
# snapshot changed, and is a no-op unless storage is configured and a student is set.
# --------------------------------------------------------------------------- #
autosave_progress()
