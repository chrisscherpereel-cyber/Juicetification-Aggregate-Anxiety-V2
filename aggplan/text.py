"""Instructional text generated from the ACTIVE scenario/configuration, so a changed
instructor config (costs, capacity, starting workforce, safety stock, overtime, …) can never
leave stale numbers in the brief, formulas, hints or explanations."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .engine import BINV_POLICIES
from .scenario import PRESET_LABELS, Scenario
from .worksheet import col_letter


def n0(x) -> str:
    return f"{float(x):,.0f}"


def usd(x, cents: bool = False) -> str:
    return f"${float(x):,.2f}" if cents else (f"${float(x):,.0f}" if float(x).is_integer()
                                              else f"${float(x):,.2f}")


def usdm(x, cents: bool = False) -> str:
    """Markdown-safe dollars: Streamlit renders a pair of bare $ signs as LaTeX, so text meant
    for st.markdown/caption/info/radio labels escapes them. Tables/Excel/PDF use usd()."""
    return usd(x, cents).replace("$", "\\$")


def param_rows(scn: Scenario) -> List[Tuple[str, str, str]]:
    """(label, value, unit) for every resolved parameter that matters to the student, in the
    order shown in the brief, the Excel 'Assumptions' sheet and the PDF."""
    p = scn.params
    rows = [
        ("Beginning inventory", n0(p["beginning_inventory"]), "bottles"),
        ("Safety stock (planning target when a plan maintains one)", n0(p["safety_stock"]), "bottles"),
        ("Maximum inventory (storage capacity)", n0(p["max_inventory"]), "bottles"),
        ("Output per worker-hour", n0(p["bottles_per_hour"]), "bottles/hour"),
        ("Paid hours per full worker per day", n0(p["hours_per_day"]), "hours/day"),
        ("Working days per month", n0(p["working_days"]), "days"),
        ("Capacity per worker per month", n0(p["bottles_per_worker"]), "bottles/worker/month"),
        ("Starting workforce (before January)", n0(p["starting_workforce"]), "workers"),
        ("Regular labor", usd(p["regular_labor_cost"]), "$/worker/month"),
        ("Hiring", usd(p["hiring_cost"]), "$/worker"),
        ("Layoff", usd(p["layoff_cost"]), "$/worker"),
        ("Holding", usd(p["holding_cost"], True), "$/bottle/month"),
        ("Backorder", usd(p["backorder_cost"], True), "$/bottle/month"),
        ("Overtime limit", f"{p['overtime_pct']:.0%}", "of regular capacity"),
        ("Overtime cost", usd(p["overtime_cost"], True), "$/overtime bottle (extra)"),
        ("Subcontract cost", usd(p["subcontract_cost"], True), "$/bottle"),
        ("Subcontract capacity", n0(p["subcontract_capacity"]), "bottles/month"),
        ("Required on-time fulfillment (hybrid feasibility)",
         f"{p['service_requirement_pct']:g}%" if p["service_requirement_pct"] > 0 else "none", ""),
        ("Maximum December backlog", n0(p["terminal_backlog_max"]), "bottles"),
    ]
    if p["shelf_life_months"]:
        rows.append(("Shelf life (perishability test)", n0(p["shelf_life_months"]), "months"))
        rows.append(("Disposal cost", usd(p["disposal_cost"], True), "$/spoiled bottle"))
    if p["forecast_error_pct"]:
        rows.append(("Forecast error (monthly std-dev)", f"{p['forecast_error_pct']:.0%}", "of forecast"))
    rows.append(("Scenario", PRESET_LABELS.get(scn.preset, scn.preset), ""))
    return rows


def named_constant_rows(scn: Scenario) -> List[Tuple[str, str, str]]:
    desc = {
        "LABOR": "regular labor $ per worker per month", "HIRE": "hiring $ per whole worker",
        "LAYOFF": "layoff $ per whole worker", "HIREHR": "hiring $ per hour/day of capacity",
        "LAYOFFHR": "layoff $ per hour/day of capacity", "HOLD": "holding $ per bottle-month",
        "BACKORDER": "backorder $ per bottle-month", "OVERTIME": "overtime $ per bottle",
        "SUBCONTRACT": "subcontract $ per bottle", "RATE": "bottles per worker per month",
        "BEGIN": "beginning inventory", "SAFETY": "safety-stock target", "MAXINV": "storage limit",
        "SHIFT": "paid hours per full worker per day", "START": "starting workforce",
    }
    return [(k, f"{v:,.4g}", desc.get(k, "")) for k, v in scn.named_constants().items()]


def term_inventory_text(scn: Scenario, maintain: bool) -> str:
    if maintain:
        return (f"end December with at least {n0(scn['safety_stock'])} bottles on hand "
                f"(the safety-stock target) and at most {n0(scn['terminal_backlog_max'])} bottles of backlog")
    return (f"end December with no more than {n0(scn['terminal_backlog_max'])} bottles of backlog "
            "(no safety-stock target)")


SAFETY_EXPLANATION = (
    "**Safety stock is a planning target, not a hard floor.** A plan that *maintains* safety "
    "stock aims to end every month with at least that many bottles on hand. Customers still come "
    "first: if demand is high, the stock is shipped and the plan is flagged as being *below "
    "safety stock* that month. Only when physical stock reaches zero do unfilled orders become "
    "*backlog*.")


def policy_caption(scn: Scenario, key: str) -> str:
    b, s = n0(scn["beginning_inventory"]), n0(scn["safety_stock"])
    if key == "maintain":
        return (f"Aim to end every month with **{s}** bottles of safety stock. Required "
                f"production = demand + {s} − net position, so January = demand + {s} − {b}. "
                "You pay holding on the safety stock all year.")
    if key == "no_fire_first":
        return (f"Spend the **{b}** beginning inventory (no safety stock afterward), but keep "
                f"the January team of {n0(scn['starting_workforce'])} workers when the crew plus "
                "inventory already covers January demand — idle paid capacity is the price.")
    return (f"Treat the **{b}** beginning inventory as a one-time cushion: January produces "
            f"demand − {b} and ends at 0. No safety stock afterward.")


def capacity_lines(scn: Scenario) -> List[str]:
    p = scn.params
    return [f"Per day = {n0(p['bottles_per_hour'])} bottles/hour × {n0(p['hours_per_day'])} "
            f"hours/day = **{n0(scn.cap_day)}** bottles per worker.",
            f"Per month = {n0(scn.cap_day)} × {n0(p['working_days'])} working days = "
            f"**{n0(scn.cap_month)}** bottles per worker."]


def workforce_model_text(scn: Scenario, whole: bool) -> str:
    h = n0(scn.hpd)
    if whole:
        return (f"**Whole workers**: you pay for a whole number of workers. If production needs "
                f"less than their full capacity, the extra capacity is *paid idle time*. "
                f"Hire/layoff in whole workers ({usdm(scn['hiring_cost'])} / "
                f"{usdm(scn['layoff_cost'])} each).")
    return (f"**Partial workers (worker-equivalents)**: the workforce may be fractional — read it "
            f"as paid labor hours: Paid Labor Hours/Day = Workers × {h}, summed over *all* workers "
            f"(not the running time of one line). Hire/layoff per hour/day of capacity "
            f"({usdm(scn['hiring_cost'] / scn.hpd, True)} / {usdm(scn['layoff_cost'] / scn.hpd, True)}). "
            f"Starting team = {n0(scn['starting_workforce'])} workers = "
            f"{n0(scn['starting_workforce'] * scn.hpd)} paid hours/day.")


def formula_reference(scn: Scenario, kind: str, whole: bool) -> List[Tuple[str, str]]:
    """(column, formula text) with the active numbers filled in."""
    p = scn.params
    L = col_letter
    start = n0(p["starting_workforce"])
    sh = n0(scn.hpd)
    hire = usd(p["hiring_cost"]) if whole else f"{usd(p['hiring_cost'] / scn.hpd, True)} (HIREHR)"
    lay = usd(p["layoff_cost"]) if whole else f"{usd(p['layoff_cost'] / scn.hpd, True)} (LAYOFFHR)"
    chase = kind == "chase"
    rows = [
        ("Beginning Inventory", f"previous month's Ending Inventory (January = {n0(p['beginning_inventory'])})"),
        ("Beginning Backlog", "previous month's Backorders (January = 0)"),
        ("Regular Production",
         "bottles you must make this month: demand + target ending inventory − (beginning "
         "inventory − beginning backlog), never below 0" if chase else
         "the constant level rate = (total demand + ending-inventory target − beginning "
         "inventory) ÷ 12, the same every month"),
        ("Workers", ("ROUNDUP(Regular Production ÷ " + n0(scn.cap_month) + ")" if whole else
                     "Regular Production ÷ " + n0(scn.cap_month) + " (keep the fraction)")
         if chase else "the constant level workforce (same every month)"),
        ("Paid Labor Hours/Day", f"Workers × {sh} (paid hours across all workers)"),
        ("Hires", ("MAX(0, this month's Workers − last month's)" if whole else
                   "MAX(0, this month's Hours/Day − last month's)")
         + f"; January compares with the starting {start} workers"
         + ("" if chase else " — later months are 0")),
        ("Layoffs", ("MAX(0, last month's Workers − this month's)" if whole else
                     "MAX(0, last month's Hours/Day − this month's)")
         + ("" if chase else " — later months are 0")),
        ("Ending Inventory", "MAX(0, Beginning Inventory + Production − Beginning Backlog − Demand)"),
        ("Backorders", "MAX(0, Demand + Beginning Backlog − Beginning Inventory − Production)"),
        ("Regular Labor Cost", f"Workers × {usd(p['regular_labor_cost'])}   (=…*LABOR)"),
        ("Hiring Cost", f"Hires × {hire}"),
        ("Layoff Cost", f"Layoffs × {lay}"),
        ("Holding Cost", f"Ending Inventory × {usd(p['holding_cost'], True)}   (=…*HOLD)"),
        ("Backorder Cost", f"Backorders × {usd(p['backorder_cost'], True)}   (=…*BACKORDER)"),
        ("Total Monthly Cost", "Labor + Hiring + Layoff + Holding + Backorder"),
    ]
    return [(f"{L(c)} · {c}", t) for c, t in rows]


def diagnostics(scn: Scenario, kind: str, whole: bool) -> Dict[str, Tuple[str, str]]:
    """Per-column (rule, likely cause) text for wrong-cell feedback. Rules explain the method
    without giving the number."""
    p = scn.params
    L = col_letter
    b, start = n0(p["beginning_inventory"]), n0(p["starting_workforce"])
    d = {
        "Beginning Inventory": (
            f"this month's stock on hand = last month's Ending Inventory (January = {b}); it is "
            "never negative",
            "you may have re-typed the starting figure every month, or carried a backlog into "
            "inventory (backlog has its own column)"),
        "Beginning Backlog": (
            "unfilled orders carried in = last month's Backorders (January = 0)",
            "you may have forgotten to carry last month's Backorders forward"),
        "Workers": (
            f"Workers = {'ROUNDUP' if whole else ''}(Regular Production ÷ {n0(scn.cap_month)})",
            "it must match THIS row's Regular Production; for whole workers a fraction rounds up"),
        "Paid Labor Hours/Day": (
            f"Paid Labor Hours/Day = Workers × {n0(scn.hpd)}",
            "check the Workers cell and the paid shift length"),
        "Hires": (f"Hires = MAX(0, this month's Workers − last month's) (January vs {start})"
                  if whole else "Hires = MAX(0, increase in Paid Labor Hours/Day vs last month)",
                  "you may have recorded a hire when the workforce did not grow"),
        "Layoffs": ("Layoffs = MAX(0, last month's Workers − this month's)" if whole else
                    "Layoffs = MAX(0, decrease in Paid Labor Hours/Day vs last month)",
                    "you may have logged a layoff when the workforce did not shrink"),
        "Ending Inventory": (
            "Ending Inventory = MAX(0, Beginning Inventory + Production − Beginning Backlog − Demand)",
            "you may have forgotten a term, or not floored a negative balance at 0 "
            "(that part is backlog, not inventory)"),
        "Backorders": (
            "Backorders = MAX(0, Demand + Beginning Backlog − Beginning Inventory − Production)",
            "you may have put the shortage into Ending Inventory, or not included last month's backlog"),
        "Regular Labor Cost": (
            f"Regular Labor Cost = Workers × {usdm(p['regular_labor_cost'])}",
            "check the Workers cell and the labor rate — labor is paid on workers, not on bottles"),
        "Hiring Cost": (f"Hiring Cost = Hires × "
                        f"{usdm(p['hiring_cost']) if whole else usdm(p['hiring_cost'] / scn.hpd, True)}",
                        "check the Hires cell and the rate for your worker model"),
        "Layoff Cost": (f"Layoff Cost = Layoffs × "
                        f"{usdm(p['layoff_cost']) if whole else usdm(p['layoff_cost'] / scn.hpd, True)}",
                        "check the Layoffs cell and the rate for your worker model"),
        "Holding Cost": (f"Holding Cost = Ending Inventory × {usdm(p['holding_cost'], True)}",
                         "check the Ending Inventory cell and the holding rate"),
        "Backorder Cost": (f"Backorder Cost = Backorders × {usdm(p['backorder_cost'], True)}",
                           "check the Backorders cell and the backorder rate"),
        "Total Monthly Cost": ("Total = Labor + Hiring + Layoff + Holding + Backorder for that row",
                               "you may have missed a cost column or added an extra one"),
    }
    d["Regular Production"] = (
        ("Regular Production = bottles you must make this month: demand + target ending "
         "inventory − net position (inventory − backlog)",
         "check which inventory policy you chose and whether January spends the beginning inventory")
        if kind == "chase" else
        ("Regular Production = your constant monthly level rate (identical every month)",
         "every row should equal the level rate you computed above"))
    if kind == "level":
        d["Workers"] = ("a level plan uses the SAME workforce every month (your level headcount)",
                        "compute ROUNDUP(level rate ÷ capacity) once and repeat it")
        d["Hires"] = (f"the workforce changes only in January (from the starting {start}); every "
                      "other month is 0", "later months should not hire or lay off")
        d["Layoffs"] = d["Hires"]
    return d


def chase_how_to(scn: Scenario, key: str, whole: bool) -> str:
    p = scn.params
    s = n0(p["safety_stock"]); b = n0(p["beginning_inventory"])
    if key == "maintain":
        prod = (f"**Regular Production** = demand + {s} − net position; so January = demand + {s} "
                f"− {b}, then each month ≈ demand (the {s} safety stock stays on hand).")
    elif key == "no_fire_first":
        prod = (f"**Regular Production** = demand − net position (spend the {b}); in January keep "
                f"the {n0(p['starting_workforce'])}-worker team if it already covers the month.")
    else:
        prod = (f"**Regular Production** = demand − {b} in January (spend the cushion, end at 0), "
                "then each later month = that month's demand.")
    return ("\n".join([
        "0. " + capacity_lines(scn)[0] + " " + capacity_lines(scn)[1],
        f"1. {prod}",
        f"2. **Workers** = {'ROUNDUP(' if whole else '('}Regular Production ÷ {n0(scn.cap_month)})"
        + (" — whole workers; any unused capacity is paid idle time." if whole else
           f" — keep the fraction; **Paid Labor Hours/Day** = Workers × {n0(scn.hpd)}."),
        "3. **Hires / Layoffs** = change in " + ("Workers" if whole else "Paid Labor Hours/Day")
        + f" vs last month (January vs the starting {n0(p['starting_workforce'])} workers).",
        "4. **Beginning Inventory** = last month's Ending Inventory; **Beginning Backlog** = last "
        "month's Backorders. **Ending Inventory** = MAX(0, Beg Inv + Production − Beg Backlog − "
        "Demand); **Backorders** = MAX(0, Demand + Beg Backlog − Beg Inv − Production).",
        "5. Costs: Labor = Workers × labor rate; Hiring/Layoff = Hires/Layoffs × rate; Holding = "
        "Ending Inventory × holding; Backorder = Backorders × backorder rate.",
        "6. **Total Monthly Cost** = Labor + Hiring + Layoff + Holding + Backorder.",
    ]))


def level_how_to(scn: Scenario, whole: bool, maintain: bool) -> str:
    p = scn.params
    t = n0(p["safety_stock"]) if maintain else "0"
    return "\n".join([
        "0. " + capacity_lines(scn)[0] + " " + capacity_lines(scn)[1],
        f"1. **Level Production** = (Total Demand + {t} − {n0(p['beginning_inventory'])}) ÷ 12 — the "
        "SAME number every month.",
        f"2. **Workers** = {'ROUNDUP(' if whole else '('}Level Production ÷ {n0(scn.cap_month)}) — "
        "constant all year" + ("; rounding up leaves paid idle capacity." if whole else "."),
        f"3. January only: hire/lay off from the starting {n0(p['starting_workforce'])} workers to the "
        "level crew; every later month Hires = Layoffs = 0.",
        "4. Each month: Beginning Inventory = last Ending Inventory; Beginning Backlog = last "
        "Backorders; Ending Inventory and Backorders as in the chase plan.",
        "5. Costs and **Total Monthly Cost** as in the chase plan.",
    ])


def stage4_formulas(scn: Scenario) -> Dict[str, Tuple[str, List[str]]]:
    """Stage 4 multiple-choice formulas (correct answer + options), numbers from the scenario."""
    p = scn.params
    cap = n0(scn.cap_month)
    hold, bo = usdm(p["holding_cost"], True), usdm(p["backorder_cost"], True)
    return {
        "Required Production": (
            "Forecast Demand + Desired Ending Inventory − Beginning Inventory + Beginning Backlog",
            ["Forecast Demand + Desired Ending Inventory − Beginning Inventory + Beginning Backlog",
             "Forecast Demand × Holding Cost", "Beginning Inventory − Forecast Demand",
             "Forecast Demand + Beginning Inventory"]),
        "Workers": (f"ROUNDUP(Required Production ÷ {cap})",
                    [f"ROUNDUP(Required Production ÷ {cap})", f"Required Production × {cap}",
                     "Forecast Demand ÷ Labor Cost", f"ROUNDDOWN(Required Production ÷ {cap})"]),
        "Hires": ("MAX(0, Workers − Workers Last Month)",
                  ["MAX(0, Workers − Workers Last Month)", "Workers − Workers Last Month",
                   "Forecast Demand × Hiring Cost", "MAX(0, Workers Last Month − Workers)"]),
        "Layoffs": ("MAX(0, Workers Last Month − Workers)",
                    ["MAX(0, Workers Last Month − Workers)", "Workers − Workers Last Month",
                     "Layoff Cost × Workers", "MAX(0, Workers − Workers Last Month)"]),
        "Ending Inventory": (
            "MAX(0, Beginning Inventory + Production − Beginning Backlog − Forecast Demand)",
            ["MAX(0, Beginning Inventory + Production − Beginning Backlog − Forecast Demand)",
             "Beginning Inventory − Production", "Forecast Demand − Production",
             "MAX(0, Forecast Demand − Production)"]),
        "Backorders": (
            "MAX(0, Forecast Demand + Beginning Backlog − Beginning Inventory − Production)",
            ["MAX(0, Forecast Demand + Beginning Backlog − Beginning Inventory − Production)",
             "Ending Inventory × Backorder Cost", "MAX(0, Production − Forecast Demand)",
             "Forecast Demand − Ending Inventory"]),
        "Holding Cost": (f"Ending Inventory × {hold}",
                         [f"Ending Inventory × {hold}", f"Beginning Inventory × {bo}",
                          f"Backorders × {hold}", f"Ending Inventory × {bo}"]),
        "Total Monthly Cost": (
            "Regular Labor + Hiring + Layoff + Holding + Backorder",
            ["Regular Labor + Hiring + Layoff + Holding + Backorder", "Regular Labor + Holding only",
             "Forecast Demand × Labor Cost", "Hiring + Layoff + Holding"]),
    }


def column_reasons(scn: Scenario) -> Dict[str, str]:
    p = scn.params
    return {
        "Month": "Defines the planning period each row covers; every calculation is monthly.",
        "Forecast Demand": "The bottles you must supply that month — the target every plan is built around.",
        "Beginning Inventory": "Physical stock on hand at the start of the month; links each month to the previous one. Never negative.",
        "Beginning Backlog": "Unfilled orders carried in from last month. Keeps physical inventory and shortages separate.",
        "Required Production": "Bottles you must make after using stock and clearing backlog; it drives the workforce calculation.",
        "Regular Production": f"Bottles actually produced on regular time (at most workers × {n0(scn.cap_month)}).",
        "Workers": f"The paid workforce; capacity = workers × {n0(scn.cap_month)} bottles/month, used or not.",
        "Workers Available": "Last month's workforce — the baseline for deciding whether to hire or lay off.",
        "Hires": "Workers added versus last month; the only way a chase plan grows capacity; triggers hiring cost.",
        "Layoffs": "Workers cut versus last month; triggers layoff cost and signals instability.",
        "Ending Inventory": "Physical stock carried to next month; drives holding cost.",
        "Backorders": "Backlog (unfilled orders) at month end; carried forward; drives backorder cost.",
        "Regular Labor Cost": f"Workers × {usd(p['regular_labor_cost'])} — usually the largest cost.",
        "Hiring Cost": f"Hires × {usd(p['hiring_cost'])} — the price of adding capacity.",
        "Layoff Cost": f"Layoffs × {usd(p['layoff_cost'])} — the price of cutting capacity.",
        "Holding Cost": f"Ending inventory × {usd(p['holding_cost'], True)} — the cost of carrying stock.",
        "Backorder Cost": f"Backorders × {usd(p['backorder_cost'], True)} — the penalty for late orders.",
        "Total Monthly Cost": "Sum of every monthly cost; the number you compare across strategies.",
    }
