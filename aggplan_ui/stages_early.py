"""Stages 0-6: understand the scenario and prepare the worksheet."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from aggplan import text as T
from aggplan import workflow as W
from aggplan.scenario import PRESET_LABELS, PRESET_LEARNING

from .common import (Ctx, SS, STRETCH, feedback, p_multiselect, p_number, p_radio, record_check,
                     shuffled)


def demand_table(ctx: Ctx) -> pd.DataFrame:
    return pd.DataFrame({"Month": list(ctx.scn.months),
                         "Forecast demand (bottles)": [f"{d:,.0f}" for d in ctx.scn.demand]})


def brief_tables(ctx: Ctx, where=st, height: int = 250) -> None:
    """Scenario data readable beside any worksheet."""
    where.markdown(f"**Forecast — total {ctx.scn.total_demand:,.0f} bottles**")
    where.dataframe(demand_table(ctx), hide_index=True, **STRETCH, height=height)
    where.markdown("**Costs, capacity and rules (with units)**")
    where.dataframe(pd.DataFrame(T.param_rows(ctx.scn), columns=["Item", "Value", "Unit"]),
                    hide_index=True, **STRETCH, height=height)


def model_rules(ctx: Ctx) -> None:
    s = ctx.scn
    with st.expander("📏 Model rules (how inventory, backlog, safety stock and workers work)"):
        st.markdown(T.SAFETY_EXPLANATION)
        st.markdown(
            "**Inventory vs. backlog.** Physical inventory is never negative. Demand you can't "
            "ship becomes *backlog* that carries to next month; when stock arrives, the **oldest "
            "orders are cleared first**, then the current month's demand is shipped. "
            "*Net position = inventory − backlog.*")
        st.markdown(
            f"**Terminal requirements.** In December: backlog ≤ {s['terminal_backlog_max']:,.0f} "
            "bottles; inventory ≥ the policy's safety-stock target (0 if the plan keeps none).")
        st.markdown(T.workforce_model_text(s, True))
        st.markdown(T.workforce_model_text(s, False))
        st.markdown(
            f"**Costs.** Workers are paid for their *capacity* ({s.cap_month:,.0f} bottles each per "
            "month) even if production is lower. Overtime and subcontracting are extra costs per "
            "bottle on top of regular labor. Money is shown to the cent; totals are computed from "
            "unrounded monthly values.")


def render_brief(ctx: Ctx) -> None:
    s = ctx.scn
    ctx.pg.setdefault("visited", {})["brief"] = True
    st.title("Juicetification: Aggregate Anxiety")
    st.write(
        "You are the **operations planning analyst** for Juicetification Inc., a small juice "
        "bottler. You bottle one aggregate product family — 16-oz bottled juice — and must build "
        "a **12-month aggregate production plan** that meets the forecast at a reasonable total "
        "cost while weighing workforce stability, inventory and customer service. You'll build a "
        "**chase** plan and a **level** plan yourself, compare them, and defend a recommendation.")
    label = PRESET_LABELS.get(s.preset, s.preset)
    st.info(f"📌 **Your scenario** — ID {s.seed}"
            + (f" (replacement #{s.variant})" if s.variant else "")
            + f" · {label}. {PRESET_LEARNING.get(s.preset, '')}")
    for n in s.notices:
        st.warning("Configuration note: " + n)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("12-month sales forecast")
        st.dataframe(demand_table(ctx), hide_index=True, **STRETCH)
        st.metric("Total annual demand", f"{s.total_demand:,.0f} bottles")
    with c2:
        st.subheader("Cost and capacity data")
        st.dataframe(pd.DataFrame(T.param_rows(s), columns=["Item", "Value", "Unit"]),
                     hide_index=True, **STRETCH, height=420)
    model_rules(ctx)
    a, b = st.columns(2)
    a.success("**Chase plan** — adjust the workforce to *follow* demand. Little inventory, but "
              "repeated hiring and layoffs.")
    b.warning("**Level plan** — hold production *steady* and absorb swings with inventory and "
              "backlog. Stable workforce, more inventory and shortage risk.")
    st.caption("The **Scenario data** panel in the sidebar is open to you on every stage.")


# --------------------------------------------------------------------------- #
def render_understand(ctx: Ctx) -> None:
    st.write("Frame the trade-off before calculating. Feedback appears when you press "
             "**Check my work**.")
    months = list(ctx.scn.months)
    q1 = p_multiselect(ctx, "1. Which months have the **highest** demand?", months, "s1q1")
    q2 = p_multiselect(ctx, "2. Which months have the **lowest** demand?", months, "s1q2")
    q3 = p_radio(ctx, "3. If the company produces the *same* amount every month:",
                 shuffled(ctx, "s1q3", [
                     "Inventory builds in slow months and shortages appear at the peak",
                     "Costs are always minimized", "The workforce must change every month"]),
                 "s1q3_ans", optional=True)
    q4 = p_radio(ctx, "4. If the workforce changes every month to match demand:",
                 shuffled(ctx, "s1q4", [
                     "Hiring and layoff costs rise, but inventory stays low",
                     "Inventory holding cost becomes the biggest cost", "Nothing changes"]),
                 "s1q4_ans", optional=True)
    costs = ["Hiring cost", "Layoff cost", "Holding cost", "Backorder cost", "Regular labor cost"]
    q5 = p_multiselect(ctx, "5. Which costs tend to **rise under a chase strategy**?",
                       shuffled(ctx, "s1q5", costs), "s1q5_ans")
    q6 = p_multiselect(ctx, "6. Which costs tend to **rise under a level strategy**?",
                       shuffled(ctx, "s1q6", costs), "s1q6_ans")
    with st.expander("💡 Hint — how to think about this (no answers)"):
        st.write("- A **chase** plan moves *capacity* up and down. What has to change to move "
                 "capacity, and what does changing it cost?")
        st.write("- A **level** plan freezes capacity. If output is flat but demand isn't, does "
                 "the mismatch go into stock or into shortages?")
    if st.button("Check my work", key="chk_understand", type="primary"):
        top = sorted(range(12), key=lambda i: -ctx.scn.demand[i])[:3]
        low = sorted(range(12), key=lambda i: ctx.scn.demand[i])[:3]
        res = [
            ("Highest-demand months", set(q1) == {months[i] for i in top}),
            ("Lowest-demand months", set(q2) == {months[i] for i in low}),
            ("Flat output vs. seasonal demand", bool(q3) and q3.startswith("Inventory builds")),
            ("Chasing demand", bool(q4) and q4.startswith("Hiring and layoff")),
            ("Costs that rise under chase", set(q5) == {"Hiring cost", "Layoff cost"}),
            ("Costs that rise under level", set(q6) == {"Holding cost", "Backorder cost"}),
        ]
        ctx.pg["understand_res"] = [[n, ok] for n, ok in res]
        record_check(ctx, "understand", all(ok for _, ok in res))
    for n, ok in ctx.pg.get("understand_res", []):
        st.write(("✅ Correct — " if ok else "🔎 Not yet — ") + n
                 + ("" if ok else ": re-read the data table and the hint, then change your answer "
                                  "and check again."))


# --------------------------------------------------------------------------- #
REQUIRED_DATA = {"Demand forecast", "Production rate (bottles per worker)", "Regular labor cost",
                 "Hiring cost", "Layoff cost", "Holding cost", "Backorder cost",
                 "Beginning inventory", "Inventory policy (safety stock or none)"}
DISTRACTORS = {"Retail selling price", "Number of delivery trucks", "Annual interest rate",
               "Machine setup time", "Marketing budget", "Warehouse square footage",
               "Employee turnover rate", "Competitor pricing"}


def render_data(ctx: Ctx) -> None:
    st.write("Select **every input** the cost model requires — and nothing it doesn't.")
    picks = p_multiselect(ctx, "Inputs needed to build the plan",
                          shuffled(ctx, "s2opts", REQUIRED_DATA | DISTRACTORS), "s2picks")
    with st.expander("💡 Hint (no answers)"):
        st.write("For each option ask: *does a number in my worksheet multiply or depend on it?* "
                 "Selling price, financing and facilities matter to the business but never enter "
                 "an aggregate **production** cost calculation here.")
    if st.button("Check my work", key="chk_data", type="primary"):
        missing, wrong = REQUIRED_DATA - set(picks), DISTRACTORS & set(picks)
        record_check(ctx, "data", not missing and not wrong)
        ctx.pg["data_res"] = {"missing": len(missing), "wrong": len(wrong)}
    r = ctx.pg.get("data_res")
    if r:
        if not r["missing"] and not r["wrong"]:
            feedback("ok", f"Exactly the {len(REQUIRED_DATA)} inputs the model needs — nothing extra.")
        else:
            if r["missing"]:
                feedback("bad", f"You're missing {r['missing']} required input(s). Re-check which "
                         "values your cost formulas multiply.")
            if r["wrong"]:
                feedback("bad", f"{r['wrong']} of your picks never appear in a production cost "
                         "formula. Which are about sales or facilities rather than production?")
            st.caption("Change your selection and press **Check my work** again.")


# --------------------------------------------------------------------------- #
GOOD_COLS = {"Month", "Forecast Demand", "Beginning Inventory", "Beginning Backlog",
             "Regular Production", "Workers", "Hires", "Layoffs", "Ending Inventory",
             "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Holding Cost",
             "Backorder Cost", "Total Monthly Cost"}
OPTIONAL_COLS = {"Paid Labor Hours/Day", "Workers Available"}     # fine either way
OUT_OF_SCOPE = {"Overtime Cost", "Subcontract Cost"}
TRAP_COLS = {"Retail Selling Price", "Units Damaged in Transit", "Employee Satisfaction Score",
             "Warehouse Square Footage", "Marketing Spend", "Machine Color",
             "Prior-Year Tax Rate", "Delivery Route Count"}


def render_columns(ctx: Ctx) -> None:
    st.write("A worksheet is only as good as its columns. Pick the columns a **chase** plan "
             "needs. Order is shuffled and several options are traps.")
    picks = p_multiselect(ctx, "Columns for the chase worksheet",
                          shuffled(ctx, "s3opts", GOOD_COLS | OPTIONAL_COLS | OUT_OF_SCOPE | TRAP_COLS),
                          "s3picks")
    reasons = T.column_reasons(ctx.scn)
    with st.expander("💡 Why each column is needed (reference)"):
        st.dataframe(pd.DataFrame([(c, r) for c, r in reasons.items()],
                                  columns=["Column", "Why it's needed"]),
                     hide_index=True, **STRETCH)
    with st.expander("💡 Hint (no answers)"):
        st.write("- A chase plan changes capacity to follow demand — your columns must *show the "
                 "workforce moving* and *price that movement*.")
        st.write("- Physical inventory and unfilled orders are different things; each needs its "
                 "own column.")
        st.write("- **Paid Labor Hours/Day** is optional (useful for partial workers). Overtime "
                 "and subcontracting belong to the hybrid challenge, not this build.")
    if st.button("Check my work", key="chk_cols", type="primary"):
        missing = GOOD_COLS - set(picks)
        traps, scope = TRAP_COLS & set(picks), OUT_OF_SCOPE & set(picks)
        ctx.pg["cols_res"] = {"missing": len(missing), "traps": len(traps), "scope": len(scope)}
        record_check(ctx, "columns", not (missing or traps or scope))
    r = ctx.pg.get("cols_res")
    if r:
        if not (r["missing"] or r["traps"] or r["scope"]):
            feedback("ok", "Clean worksheet — every column earns its place.")
        else:
            if r["traps"]:
                feedback("bad", f"{r['traps']} pick(s) can't be computed from the brief data at "
                         "all — those are distractors.")
            if r["scope"]:
                feedback("warn", "Overtime / subcontract columns aren't part of the chase and "
                         "level builds — leave them out here.")
            if r["missing"]:
                feedback("bad", f"A chase plan still needs {r['missing']} more column(s). Have you "
                         "shown how the workforce changes, kept inventory and backlog separate, "
                         "and included every cost category in the total?")


# --------------------------------------------------------------------------- #
def render_formulas(ctx: Ctx) -> None:
    st.write("Pick the correct formula for each column. Options are shuffled.")
    defs = T.stage4_formulas(ctx.scn)
    answers = {}
    for name, (correct, opts) in defs.items():
        answers[name] = (correct, p_radio(ctx, f"**{name}** =", shuffled(ctx, f"s4_{name}", opts),
                                          f"s4_{name}_ans", optional=True))
    with st.expander("💡 Hint (no answers)"):
        st.write("- You can't hire a *fraction* of a whole worker, and too few workers can't meet "
                 "demand — so headcount rounds a certain way.")
        st.write("- Hires and layoffs are never negative; MAX(0, …) keeps one from turning into the other.")
        st.write("- A month is holding stock **or** carrying backlog — never both — so ending "
                 "inventory and backorders come from the *same* balance.")
    if st.button("Check my work", key="chk_formulas", type="primary"):
        res = {n: (got == c) for n, (c, got) in answers.items()}
        ctx.pg["formula_res"] = res
        record_check(ctx, "formulas", all(res.values()))
    res = ctx.pg.get("formula_res")
    if res:
        for n, ok in res.items():
            st.write(f"✅ {n}" if ok else f"❌ {n} — not yet; re-read this column's hint.")
        st.metric("Formula score", f"{sum(res.values())} / {len(res)}")
        if all(res.values()):
            feedback("ok", "All formulas correct — the formula reference is unlocked beside the "
                     "plan worksheets.")


# --------------------------------------------------------------------------- #
def practice_scenarios(ctx: Ctx):
    m = ctx.scn.months
    return [
        {"name": "No starting inventory", "demand": [10000, 20000, 30000, 40000], "begin": 0, "end": 0, "rate": 1000},
        {"name": "With starting inventory", "demand": [10000, 20000, 30000, 40000], "begin": 20000, "end": 0, "rate": 1000},
        {"name": "Safety stock required (with starting inventory)", "demand": [30000, 60000, 30000], "begin": 10000, "end": 10000, "rate": 1000},
        {"name": "Uneven demand, no starting inventory", "demand": [5000, 5000, 10000, 10000, 5000, 5000], "begin": 0, "end": 0, "rate": 1000},
    ], m


def render_practice(ctx: Ctx) -> None:
    st.write("Warm up before the full worksheet. For each mini-scenario compute the **pure level** "
             "production and the first month of a **pure chase**. Some scenarios start with stock "
             "on hand — notice how that changes the math. Each scenario has its own "
             "**Check my work**.")
    scs, months = practice_scenarios(ctx)
    prac = ctx.pg.setdefault("practice", {})
    for idx, sc in enumerate(scs):
        with st.container(border=True):
            st.subheader(f"Scenario {idx + 1}: {sc['name']}")
            n = len(sc["demand"])
            st.dataframe(pd.DataFrame({"Month": months[:n], "Demand": sc["demand"]}),
                         hide_index=True, **STRETCH)
            total = sum(sc["demand"])
            st.caption(f"Months = {n} · Total demand = {total:,} · Beginning inventory = "
                       f"{sc['begin']:,} · Desired ending inventory = {sc['end']:,} · Rate = "
                       f"{sc['rate']:,} bottles/worker")
            level = (total + sc["end"] - sc["begin"]) / n
            lw = math.ceil(level / sc["rate"] - 1e-9)
            c1w = math.ceil(max(0, sc["demand"][0] + sc["end"] - sc["begin"]) / sc["rate"] - 1e-9)
            c1, c2, c3 = st.columns(3)
            with c1:
                a_level = p_number(ctx, "Pure LEVEL production / month", f"pr{idx}_level",
                                   min_value=0.0, step=1.0)
            with c2:
                a_lw = p_number(ctx, "Workers for that level (round up)", f"pr{idx}_lw",
                                min_value=0, step=1)
            with c3:
                a_c1 = p_number(ctx, f"CHASE workers in {months[0]}", f"pr{idx}_c1",
                                min_value=0, step=1)
            if st.button("Check my work", key=f"pr{idx}_btn"):
                ok = [abs(a_level - level) < 0.5, a_lw == lw, a_c1 == c1w]
                prac[str(idx)] = {"passed": all(ok), "ok": ok, "n": int(prac.get(str(idx), {}).get("n", 0)) + 1}
            r = prac.get(str(idx))
            if r and r.get("ok"):
                tips = ["Level production = (total demand + desired ending − beginning) ÷ months.",
                        "Workers = level production ÷ rate, rounded **up**.",
                        f"Chase {months[0]} workers = ROUNDUP((demand + desired ending − beginning) ÷ rate)."]
                for okk, tip, lab in zip(r["ok"], tips, ("Level production", "Level workers", "Chase workers")):
                    st.write(("✅ " + lab + " — correct.") if okk else ("❌ " + lab + " — not yet. " + tip))
    st.info("Once these feel automatic, move on to capacity and the full 12-month plans.")


# --------------------------------------------------------------------------- #
def render_capacity(ctx: Ctx) -> None:
    s = ctx.scn
    st.write("Work out one worker's output. These numbers feed **both** plan builders.")
    st.dataframe(pd.DataFrame(
        [("Bottles / worker / hour", f"{s['bottles_per_hour']:,.0f}"),
         ("Paid hours / day (full shift)", f"{s['hours_per_day']:,.0f}"),
         ("Working days / month", f"{s['working_days']:,.0f}")], columns=["Given", "Value"]),
        hide_index=True, **STRETCH)
    c1, c2 = st.columns(2)
    with c1:
        capd = p_number(ctx, "Capacity per worker per **DAY** (bottles)", "cap_capday",
                        min_value=0, step=1, help="bottles/hour × hours/day")
    with c2:
        capm = p_number(ctx, "Capacity per worker per **MONTH** (bottles)", "cap_capmonth",
                        min_value=0, step=50, help="capacity/day × working days")
    if st.button("Check my work", key="chk_cap", type="primary"):
        ok_d, ok_m = capd == s.cap_day, capm == s.cap_month
        ctx.pg["cap_res"] = {"d": ok_d, "m": ok_m}
        record_check(ctx, "capacity", ok_d and ok_m)
    r = ctx.pg.get("cap_res")
    if r:
        d_line, m_line = T.capacity_lines(s)
        feedback("ok", d_line) if r["d"] else feedback("bad", "Per day = bottles/hour × hours/day.")
        feedback("ok", m_line) if r["m"] else feedback("bad", "Per month = capacity/day × working days.")
        if r["d"] and r["m"]:
            st.info(f"A full worker = {s.hpd:.0f} paid hours/day, so a partial-worker hire costs "
                    f"{s['hiring_cost']:,.0f} ÷ {s.hpd:.0f} = **{s['hiring_cost'] / s.hpd:,.2f}** per "
                    f"hour/day and a layoff {s['layoff_cost'] / s.hpd:,.2f}. Capacity is what you "
                    "*pay for*; production can be lower (idle paid capacity).")
