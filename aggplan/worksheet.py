"""The student worksheet: column layout (shared by the in-app grid and the Excel template, so
column letters and formulas are identical in both), the answer key, and grading."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .engine import Decision, covered_month1
from .formula import GridResult, evaluate_grid
from .scenario import Scenario

ROW_BASE = 2    # spreadsheet row of January (row 1 = column headings), same in app and Excel

# Column A..Q. Both the app grid and the Excel template use exactly this order.
WS_COLS = [
    "Month", "Forecast Demand", "Beginning Inventory", "Beginning Backlog",
    "Regular Production", "Workers", "Paid Labor Hours/Day", "Hires", "Layoffs",
    "Ending Inventory", "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
    "Holding Cost", "Backorder Cost", "Total Monthly Cost",
]
EDIT_COLS = WS_COLS[2:]
SUM_COLS = ["Regular Production", "Ending Inventory", "Backorders", "Regular Labor Cost",
            "Hiring Cost", "Layoff Cost", "Holding Cost", "Backorder Cost", "Total Monthly Cost"]
MONEY_COLS = {"Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Holding Cost",
              "Backorder Cost", "Total Monthly Cost"}


def col_letter(name: str) -> str:
    return chr(ord("A") + WS_COLS.index(name))


def units(scn: Scenario, whole: bool) -> Dict[str, str]:
    """Unit label for each worksheet column (depends on the worker model)."""
    return {
        "Month": "calendar month", "Forecast Demand": "bottles",
        "Beginning Inventory": "bottles (physical stock, never negative)",
        "Beginning Backlog": "bottles (unfilled orders carried in)",
        "Regular Production": "bottles",
        "Workers": ("whole workers" if whole else "worker-equivalents (fractions allowed)"),
        "Paid Labor Hours/Day": f"paid hours/day, all workers (= Workers × {scn.hpd:g})",
        "Hires": ("workers added" if whole else "hours/day of capacity added"),
        "Layoffs": ("workers cut" if whole else "hours/day of capacity cut"),
        "Ending Inventory": "bottles", "Backorders": "bottles (backlog at month end)",
        "Regular Labor Cost": "$", "Hiring Cost": "$", "Layoff Cost": "$",
        "Holding Cost": "$", "Backorder Cost": "$", "Total Monthly Cost": "$",
    }


def blank_worksheet(scn: Scenario) -> pd.DataFrame:
    data = {"Month": list(scn.months), "Forecast Demand": [float(d) for d in scn.demand]}
    for c in EDIT_COLS:
        data[c] = [""] * len(scn.months)
    df = pd.DataFrame(data)
    df.index = range(ROW_BASE, ROW_BASE + len(scn.months))
    return df


def truth_worksheet(plan: pd.DataFrame) -> pd.DataFrame:
    return plan[WS_COLS].reset_index(drop=True)


def cell_raw(df: pd.DataFrame, col: str, i: int) -> str:
    v = df.iat[i, df.columns.get_loc(col)]
    return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v).strip()


def fmt(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(round(f))) if abs(f - round(f)) < 1e-9 else f"{f:,.4f}".rstrip("0").rstrip(".")


STATUS_LABEL = {"ok": "✔ Correct", "wrong": "✖ Check this", "blank": "○ Blank",
                "error": "⚠ Formula error"}
TOL = {"Workers": 0.02, "Paid Labor Hours/Day": 0.2}
DEFAULT_TOL = 0.5


@dataclass
class Grade:
    status: Dict[str, List[str]]
    display: Dict[str, List[str]]
    values: Dict[str, List[Optional[float]]]
    result: GridResult
    worker_expected: List[Optional[float]] = field(default_factory=list)

    def count(self, which: str) -> int:
        return sum(s == which for col in self.status.values() for s in col)

    @property
    def n_ok(self): return self.count("ok")
    @property
    def n_wrong(self): return self.count("wrong") + self.count("error")
    @property
    def n_blank(self): return self.count("blank")
    @property
    def total(self): return sum(len(c) for c in self.status.values())
    @property
    def complete(self): return self.n_wrong == 0 and self.n_blank == 0

    def wrong_cells(self):
        return [(c, i) for c, cs in self.status.items() for i, s in enumerate(cs)
                if s in ("wrong", "error")]


def grade_worksheet(df: pd.DataFrame, scn: Scenario, truth: pd.DataFrame, *, kind: str,
                    whole: bool, chase_key: str = "use_first") -> Grade:
    """Grade every editable cell. Chase 'Workers' is judged against the student's own
    Regular Production (and Hours/Day against their Workers) so one early mistake doesn't
    mark the whole row wrong; the plan only completes when every cell matches the key."""
    result = evaluate_grid(df, scn.named_constants(), row_base=ROW_BASE)
    n = len(scn.months)
    status, display = {}, {}
    for c in EDIT_COLS:
        cs, ds = [], []
        for i in range(n):
            raw = result.raw.get((c, i), "")
            val = result.values[c][i]
            tv = float(truth[c].iloc[i])
            tol = TOL.get(c, DEFAULT_TOL)
            if raw == "":
                cs.append("blank"); ds.append("")
            elif (c, i) in result.errors:
                cs.append("error"); ds.append(raw)
            elif val is None:
                cs.append("error"); ds.append(raw)
            elif abs(val - tv) > tol:
                cs.append("wrong"); ds.append(fmt(val))
            else:
                cs.append("ok"); ds.append(fmt(val))
        status[c], display[c] = cs, ds

    worker_expected: List[Optional[float]] = [None] * n
    if kind == "chase":
        cap = scn.cap_month
        for i in range(n):
            if result.raw.get(("Regular Production", i), "") == "":
                continue            # nothing to judge Workers against yet
            rp = result.values["Regular Production"][i]
            if i == 0 and chase_key == "no_fire_first" and covered_month1(scn):
                exp = float(scn.params["starting_workforce"])
            elif rp is None:
                exp = None
            else:
                exp = float(math.ceil(rp / cap - 1e-9)) if whole else rp / cap
            worker_expected[i] = exp
            w = result.values["Workers"][i]
            if status["Workers"][i] in ("blank", "error"):
                pass
            elif w is not None and exp is not None and abs(w - exp) <= TOL["Workers"]:
                status["Workers"][i] = "ok"
            else:
                status["Workers"][i] = "wrong"
            if status["Paid Labor Hours/Day"][i] not in ("blank", "error"):
                hv, wv = result.values["Paid Labor Hours/Day"][i], result.values["Workers"][i]
                status["Paid Labor Hours/Day"][i] = (
                    "ok" if (hv is not None and wv is not None
                             and abs(hv - wv * scn.hpd) <= TOL["Paid Labor Hours/Day"])
                    else "wrong")
    return Grade(status, display, result.values, result, worker_expected)


def column_sums(result_values: Dict[str, List[Optional[float]]]) -> Dict[str, str]:
    return {c: fmt(sum(v or 0 for v in result_values[c])) for c in SUM_COLS}
