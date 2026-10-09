"""Report snapshot: ONE consistent, JSON-safe picture of the student's validated work and the
active scenario. The PDF is generated from this snapshot only, so the report cannot drift
from what the app showed (every number comes from the same engine calls).

Rules enforced here
  * final=True only when every required activity is complete (see workflow.requirement_list);
    otherwise the snapshot is a DRAFT and says so.
  * a plan appears only if the student has passed its check — a draft never reveals the
    answer key for unfinished plans;
  * student writing is copied verbatim; nothing is generated or paraphrased;
  * plan cost is labelled as plan cost, never as a grade;
  * no secrets, tokens, store paths or raw student IDs are included.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
import uuid
from typing import Dict, List, Optional

from . import APP_NAME, MODEL_VERSION
from .engine import (BINV_POLICIES, METRIC_DEFINITIONS, assumption_gaps, check_feasibility,
                     is_feasible, reference_plan, requirements_for, summarize)
from .hybrid import HybridInputs, resolve as resolve_hybrid
from .identity import Identity
from .persistence import json_safe
from .scenario import PRESET_LABELS, Scenario
from . import text as T
from . import workflow as W
from .worksheet import WS_COLS, truth_worksheet

REPORT_SCHEMA = 1

REFLECTION_PROMPTS = {
    "refl_stability": "Which plan gives employees more stable, predictable work, and why does "
                      "that matter to a plant manager?",
    "refl_responsive": "Which plan is more responsive to a sudden demand spike? What did the "
                       "what-if slider show?",
    "refl_surprise": "What surprised you when you compared the two plans?",
}
COMPARE_PROMPTS = {
    "q_cost": "Which plan has the lower total annual cost?",
    "q_wf": "Which plan changes its workforce more?",
    "q_inv": "Which plan keeps more bottles sitting in inventory?",
    "q_serv": "Which plan leaves more customer demand unmet (worse on-time fulfillment)?",
}


def now_in_zone(tz_name: str = "") -> dt.datetime:
    """Timezone-aware 'now' in the configured zone (blank/invalid -> server local time)."""
    utc = dt.datetime.now(dt.timezone.utc)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    return utc.astimezone()


def format_time(t: dt.datetime) -> str:
    name = t.tzname() or ""
    off = t.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else ""
    return f"{t.strftime('%Y-%m-%d %H:%M')} {name} ({off})".replace("  ", " ")


def new_attempt_id(final: bool) -> str:
    return ("AA-" if final else "AA-DRAFT-") + uuid.uuid4().hex[:8].upper()


def safe_filename(name: str, attempt_id: str) -> str:
    """Aggregate_Anxiety_Lastname_Firstname_AttemptID.pdf — ASCII only, no path characters."""
    def clean(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^A-Za-z0-9]+", "", s)[:30]

    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        first = first.split()[0] if first.split() else ""
    else:
        parts = name.split()
        first, last = (parts[0], parts[-1]) if len(parts) > 1 else (parts[0] if parts else "", "")
    last_c, first_c = clean(last), clean(first)
    if not last_c and not first_c:
        last_c, first_c = "Student", "Unnamed"
    aid = clean(attempt_id) or "Attempt"
    return f"Aggregate_Anxiety_{last_c or 'Student'}_{first_c or 'Unnamed'}_{aid}.pdf"


def snapshot_hash(snap: dict) -> str:
    body = {k: v for k, v in snap.items() if k != "integrity"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _rows(df, cols) -> List[dict]:
    return [{c: (float(r[c]) if c != "Month" else str(r[c])) for c in cols}
            for _, r in df.iterrows()]


def _plan_block(scn: Scenario, pg: dict, kind: str) -> Optional[dict]:
    """Verified plan block, or None when the plan hasn't passed its check."""
    if not W.plan_complete(pg, kind, scn.config_hash):
        return None
    st = pg["plans"][kind]
    whole = bool(st.get("whole", True))
    key = st.get("policy", "use_first")
    maintain = bool(st.get("maintain", False))
    dec, df = reference_plan(scn, kind, policy_key=key, whole=whole, maintain=maintain)
    summ = summarize(df, scn, dec.policy)
    checks = check_feasibility(df, requirements_for(scn, dec.policy, False, False))
    if kind == "chase":
        policy_label = BINV_POLICIES.get(key, key)
    else:
        policy_label = ("Maintain the safety-stock target all year" if maintain
                        else "No safety stock (consume beginning inventory)")
    out = {
        "label": "Chase" if kind == "chase" else "Level",
        "settings": {"Worker model": "Whole workers" if whole else "Partial workers (worker-equivalents)",
                     "Inventory policy": policy_label,
                     "Safety-stock target": f"{dec.policy.safety_target:,.0f} bottles"
                     if dec.policy.safety_target else "none",
                     "Terminal requirement": T.term_inventory_text(scn, dec.policy.maintains_safety)},
        "summary": json_safe(summ), "checks": json_safe(checks), "feasible": is_feasible(checks),
        "monthly": _rows(truth_worksheet(df), WS_COLS),
        "policy": {"worker_mode": dec.policy.worker_mode, "safety_target": dec.policy.safety_target,
                   "start_workers": dec.policy.start_workers},
        "student_total_entered": st.get("total_in"),
    }
    if kind == "level":
        out["level_rate"] = float(dec.regular[0])
        out["level_workers"] = float(dec.workers[0])
    return out


def _hybrid_block(scn: Scenario, pg: dict) -> Optional[dict]:
    h = (pg.get("hybrid") or {}).get("state") or {}
    if not h.get("attempts"):
        return None

    def detail(rec):
        res = resolve_hybrid(scn, HybridInputs.from_json(rec["inputs"]))
        cols = ["Month", "Forecast Demand", "Workers", "Regular Production", "Overtime Production",
                "Subcontract Production", "Beginning Inventory", "Beginning Backlog",
                "Ending Inventory", "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
                "Overtime Cost", "Subcontract Cost", "Holding Cost", "Backorder Cost",
                "Total Monthly Cost"]
        return {"summary": json_safe(res.summary), "checks": json_safe(res.checks),
                "feasible": res.feasible, "monthly": _rows(res.df, cols),
                "mode": res.inputs.mode,
                "worker_model": "Whole workers" if res.policy.whole else "Partial workers",
                "safety_stock": res.policy.maintains_safety,
                "overtime_enabled": res.inputs.use_overtime,
                "subcontract_enabled": res.inputs.use_subcontract,
                "automatic_assistance": res.auto_notes}

    out = {"attempts": int(h.get("attempts", 0)), "best_feasible": None, "lowest_cost_infeasible": None}
    if h.get("best_feasible"):
        out["best_feasible"] = detail(h["best_feasible"])
    if h.get("lowest_cost_infeasible"):
        rec = h["lowest_cost_infeasible"]
        d = detail(rec)
        d["failed_requirements"] = rec.get("failed", [])
        out["lowest_cost_infeasible"] = d
    return out


def build_snapshot(scn: Scenario, pg: dict, *, identity: Identity, assignment_name: str,
                   attempt_id: str, now: dt.datetime, tz_label: str = "",
                   force_draft: bool = False, seal_fn=None) -> dict:
    """Assemble the snapshot. `final` is derived from the requirements, never from the caller."""
    reqs = W.requirement_list(pg, scn.config_hash)
    missing = W.missing_requirements(pg, scn.config_hash)
    final = (not missing) and not force_draft
    ans = pg.get("ans", {})
    chase = _plan_block(scn, pg, "chase")
    level = _plan_block(scn, pg, "level")
    notes = []
    if chase and level:
        from .engine import Policy
        cp, lp = Policy(**chase["policy"]), Policy(**level["policy"])
        notes = assumption_gaps({"Chase": cp, "Level": lp}, scn)
    usage_rows = []
    for k, lab in (("chase", "Chase plan"), ("level", "Level plan")):
        st = (pg.get("plans") or {}).get(k, {})
        usage_rows.append({"activity": lab, "checks_run": int(st.get("n_checks", 0)),
                           "scenario_replacements": int(st.get("tries", 1)) - 1,
                           "worked_solution_opened": int(st.get("stuck", 0))})
    for k, lab in (("data", "Data selection"), ("columns", "Column selection"),
                   ("formulas", "Formula selection"), ("capacity", "Capacity")):
        usage_rows.append({"activity": lab, "checks_run": W.check_count(pg, k),
                           "scenario_replacements": None, "worked_solution_opened": None})
    unc = None
    u = pg.get("uncertainty") or {}
    if u.get("committed") and u.get("eval"):
        unc = json_safe(u["eval"])
    snap = {
        "schema": REPORT_SCHEMA,
        "status": "Final" if final else "Draft",
        "app": {"name": APP_NAME, "model_version": MODEL_VERSION},
        "student": {"name": (pg.get("student_name") or "").strip(),
                    "section": (pg.get("section") or "").strip(),
                    "identity": identity.label},
        "assignment": {"name": assignment_name},
        "attempt": {"id": attempt_id, "generated": format_time(now), "tz": tz_label,
                    "generated_utc": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
        "scenario": {"seed": scn.seed, "base_seed": scn.base_seed, "variant": scn.variant,
                     "preset": scn.preset, "preset_label": PRESET_LABELS.get(scn.preset, scn.preset),
                     "config_hash": scn.config_hash, "fixed_demand": scn.fixed_demand,
                     "params": [list(r) for r in T.param_rows(scn)],
                     "demand": [[m, float(d)] for m, d in zip(scn.months, scn.demand)],
                     "notices": list(scn.notices), "safety_note": "Safety stock is a planning target, "
                     "not a hard floor: stock below the target is still shipped and reported as "
                     "'months below safety stock'."},
        "activities": reqs, "missing": missing,
        "plans": {"chase": chase, "level": level}, "comparison_notes": notes,
        "metric_definitions": METRIC_DEFINITIONS,
        "strategy": {"selected": ans.get("chosen_plan"),
                     "justification": ans.get("recommendation") or ""},
        "compare_answers": [{"prompt": COMPARE_PROMPTS[k], "response": ans.get(k) or ""}
                            for k in W.CMP_KEYS],
        "tradeoff_note": ans.get("cmp_tradeoff") or "",
        "reflections": [{"prompt": REFLECTION_PROMPTS[k], "response": ans.get(k) or ""}
                        for k in W.REFL_KEYS],
        "hybrid": _hybrid_block(scn, pg),
        "uncertainty": unc,
        "usage": usage_rows,
        "plan_cost_note": "Plan cost is the modelled annual operating cost of a plan (US dollars). "
                          "It is not an academic grade.",
    }
    h = snapshot_hash(snap)
    snap["integrity"] = {"snapshot_hash": h,
                         "seal": seal_fn(h, attempt_id) if seal_fn else ""}
    return snap
