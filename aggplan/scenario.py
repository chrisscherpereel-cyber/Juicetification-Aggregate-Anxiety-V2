"""Scenario: the immutable bundle of resolved parameters + demand every calculation
receives explicitly (no module-level FORECAST / TOTAL_DEMAND to swap)."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from manifest import MANIFEST

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
N = len(MONTHS)

DEFAULTS: Dict[str, object] = {k: s["default"] for k, s in MANIFEST["params"].items()}

# Parameters an instructor preset may change (only if the instructor left them at default).
PRESETS: Dict[str, dict] = {
    "standard": {},
    "strong_seasonal": {"_amplitude": 1.9},
    "stability_favored": {"hiring_cost": 2400, "layoff_cost": 2700},
    "limited_flex": {"overtime_pct": 0.05, "subcontract_capacity": 2000},
    "tight_storage": {"max_inventory": 12000},
    "forecast_error": {"forecast_error_pct": 0.20},
    "perishable": {"shelf_life_months": 2, "disposal_cost": 1.0},
}
PRESET_LABELS: Dict[str, str] = {
    "standard": "Standard seasonal demand",
    "strong_seasonal": "Strong seasonal demand (deep trough, tall peak)",
    "stability_favored": "Hiring and layoff costs favor workforce stability",
    "limited_flex": "Limited overtime and subcontracting",
    "tight_storage": "Restricted storage capacity",
    "forecast_error": "Forecast error evaluated after a plan is committed",
    "perishable": "Perishable inventory (age tracking and disposal)",
}
PRESET_LEARNING = {
    "standard": "Compare chase and level on a typical seasonal pattern.",
    "strong_seasonal": "See how a bigger peak-to-trough swing changes inventory, workforce churn and backlog.",
    "stability_favored": "Find out when high hire/layoff costs make a level workforce the better choice.",
    "limited_flex": "Learn why short-term flexibility (overtime, subcontracting) is a scarce resource.",
    "tight_storage": "Discover when a storage limit makes pure level production infeasible.",
    "forecast_error": "Judge a committed plan against demand that differs from the forecast.",
    "perishable": "Test plans against real inventory age: spoilage, disposal and the heuristic's limits.",
}

# Parameters that affect only PRESENTATION or server behaviour, never what a correct answer
# is. They are excluded from both hashes, so an instructor can retune feedback, timing or the
# assignment name mid-course without invalidating verified plans or rejecting saved progress.
NON_MODEL_PARAMS = frozenset({
    "feedback_mode", "benchmark_reveal", "allow_replacement", "assignment_name",
    "report_timezone", "autosave_seconds", "autosave_flush_seconds",
})

_SHAPE = [0.55, 0.60, 0.75, 1.00, 1.30, 1.55, 1.65, 1.40, 1.10, 0.85, 0.70, 0.55]


def stable_int(*parts, lo: int = 0, hi: int = 10 ** 9) -> int:
    """Deterministic integer from arbitrary parts (SHA-256; unlike hash() it is the same
    in every process, so option shuffles and variants are reproducible)."""
    key = "|".join(str(p) for p in parts).encode("utf-8")
    h = hashlib.sha256(key).digest()
    return lo + int.from_bytes(h[:8], "big") % max(1, hi - lo + 1)


def generate_forecast(seed: int, amplitude: float = 1.0) -> List[int]:
    """A unique but easy-to-calculate 12-month forecast (whole multiples of 500, annual
    total a multiple of 6,000). Same algorithm as V2 for amplitude 1.0 so existing seeds
    reproduce the same demand."""
    r = random.Random(seed)
    avg_h = r.choice([17, 19, 21, 23, 25, 27])                     # half-thousands / month
    shape = [max(0.35, s + r.uniform(-0.12, 0.12)) for s in _SHAPE]
    if amplitude != 1.0:
        mean = sum(shape) / len(shape)
        shape = [max(0.12, mean + (s - mean) * amplitude) for s in shape]
    tot_h = avg_h * 12
    ssum = sum(shape)
    dh = [max(1, round(s / ssum * tot_h)) for s in shape]
    dh[dh.index(max(dh))] += tot_h - sum(dh)                       # fix rounding drift on peak
    return [int(x) * 500 for x in dh]


def variant_of_fixed_demand(demand: List[float], seed: int) -> List[int]:
    """A reproducible 'practice variant' of an instructor-fixed demand curve: each month is
    scaled by a seeded factor, rounded to 500, and the annual total is preserved."""
    r = random.Random(seed)
    raw = [max(0.0, d * r.uniform(0.85, 1.15)) for d in demand]
    out = [int(round(x / 500.0)) * 500 for x in raw]
    drift = int(round(sum(demand) / 500.0)) * 500 - sum(out)
    out[out.index(max(out))] = max(0, out[out.index(max(out))] + drift)
    return out


@dataclass(frozen=True)
class Scenario:
    params: Dict[str, float]
    demand: Tuple[float, ...]
    seed: int
    base_seed: int
    variant: int = 0
    preset: str = "standard"
    fixed_demand: bool = False
    notices: Tuple[str, ...] = ()
    months: Tuple[str, ...] = tuple(MONTHS)

    # ---- convenience --------------------------------------------------------
    def __getitem__(self, k):
        return self.params[k]

    @property
    def total_demand(self) -> float:
        return float(sum(self.demand))

    @property
    def peak_demand(self) -> float:
        return float(max(self.demand))

    @property
    def cap_month(self) -> float:
        """Bottles one full worker makes per month (= rate x hours x days)."""
        return float(self.params["bottles_per_worker"])

    @property
    def cap_day(self) -> float:
        return float(self.params["bottles_per_hour"] * self.params["hours_per_day"])

    @property
    def hpd(self) -> float:
        return float(self.params["hours_per_day"])

    @property
    def ot_limit_per_worker(self) -> float:
        return self.cap_month * float(self.params["overtime_pct"])

    def quarter_labels(self) -> List[str]:
        m = self.months
        return [f"Q{q + 1} ({m[3 * q][:3]}–{m[3 * q + 2][:3]})" for q in range(4)]

    def named_constants(self) -> Dict[str, float]:
        """Brief-data values usable by NAME in any worksheet formula."""
        p = self.params
        return {
            "LABOR": p["regular_labor_cost"], "HIRE": p["hiring_cost"],
            "LAYOFF": p["layoff_cost"],
            "HIREHR": p["hiring_cost"] / p["hours_per_day"],
            "LAYOFFHR": p["layoff_cost"] / p["hours_per_day"],
            "HOLD": p["holding_cost"], "BACKORDER": p["backorder_cost"],
            "OVERTIME": p["overtime_cost"], "SUBCONTRACT": p["subcontract_cost"],
            "RATE": p["bottles_per_worker"], "BEGIN": p["beginning_inventory"],
            "SAFETY": p["safety_stock"], "MAXINV": p["max_inventory"],
            "SHIFT": p["hours_per_day"], "START": p["starting_workforce"],
        }

    # ---- identity ------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"params": dict(self.params), "demand": list(self.demand), "seed": self.seed,
                "base_seed": self.base_seed, "variant": self.variant, "preset": self.preset,
                "fixed_demand": self.fixed_demand}

    @property
    def model_params(self) -> Dict[str, object]:
        """Only the parameters that change a correct answer."""
        return {k: v for k, v in self.params.items() if k not in NON_MODEL_PARAMS}

    @property
    def config_hash(self) -> str:
        """Hash of everything that changes a correct answer (model parameters + demand)."""
        blob = json.dumps({"p": self.model_params, "d": list(self.demand)}, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def instructor_config_hash(self) -> str:
        """Hash of the instructor-controlled part only (parameters + fixed demand, but NOT the
        per-student seed). Saved progress is compatible only if this matches."""
        blob = json.dumps({"p": self.model_params, "fixed": list(self.demand) if self.fixed_demand
                           else None}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def resolve_scenario(cfg: Optional[dict], seed: int, variant: int = 0) -> Scenario:
    """Build a Scenario from the Director-resolved params (`cfg`), a base seed and a
    replacement-variant index (0 = the assigned scenario).

    Validation never raises: problems become `notices` and a safe fallback is used, so a
    bad config cannot crash the app mid-class."""
    notices: List[str] = []
    params = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in params:
            params[k] = v

    preset = params.get("scenario_preset") or "standard"
    if preset not in PRESETS:
        notices.append(f"Unknown scenario preset '{preset}' — using the standard scenario.")
        preset = "standard"
    amplitude = 1.0
    for k, v in PRESETS[preset].items():
        if k == "_amplitude":
            amplitude = float(v)
        elif params.get(k) == DEFAULTS[k]:               # never overwrite an instructor value
            params[k] = v
    params["scenario_preset"] = preset

    fixed = params.pop("forecast_demand", []) or []
    fixed_ok = False
    if fixed:
        vals = [_num(x) for x in fixed]
        if len(vals) == N and all(v is not None and v >= 0 for v in vals):
            fixed_ok = True
            fixed = [float(v) for v in vals]
        else:
            notices.append("The configured 12-month demand must contain exactly 12 non-negative "
                           "numbers; it was ignored and random demand is used instead.")

    derived = params["bottles_per_hour"] * params["hours_per_day"] * params["working_days"]
    if params["bottles_per_worker"] != derived:
        notices.append(
            f"Configured bottles/worker/month ({params['bottles_per_worker']:,}) does not equal "
            f"bottles/hour × hours/day × working days ({derived:,}). The calculated value "
            f"{derived:,} is used everywhere so every stage agrees.")
        params["bottles_per_worker"] = derived

    vseed = seed if variant == 0 else stable_int("variant", seed, variant, lo=1, hi=10 ** 6)
    if fixed_ok:
        demand = list(fixed) if variant == 0 else variant_of_fixed_demand(fixed, vseed)
    else:
        demand = generate_forecast(vseed, amplitude)

    return Scenario(params=params, demand=tuple(float(d) for d in demand), seed=vseed,
                    base_seed=seed, variant=variant, preset=preset, fixed_demand=fixed_ok,
                    notices=tuple(notices))
