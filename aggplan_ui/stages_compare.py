"""Stages 9-11: compare the plans, the hybrid design challenge, and optional advanced exercises."""

from __future__ import annotations

import html
from dataclasses import replace
from typing import Dict, Optional

import pandas as pd
import streamlit as st

from aggplan import benchmark, excel_io
from aggplan import text as T
from aggplan import workflow as W
from aggplan.engine import (METRIC_DEFINITIONS, Decision, check_feasibility, is_feasible,
                            reference_plan, requirements_for, run_decision, simulate, summarize,
                            assumption_gaps, Policy)
from aggplan.hybrid import (HybridInputs, compare_hybrid, default_workers, register_attempt,
                            resolve as resolve_hybrid)
from aggplan.uncertainty import evaluate_committed, realized_demands

from . import excel_panel
from .common import (Ctx, SS, STRETCH, feedback, footer_nav, money, moneym, p_checkbox, p_multiselect, p_number,
                     p_radio, p_text_input)
from .stages_build import plan_state
from .stages_early import model_rules


def plan_bundle(ctx: Ctx, kind: str) -> Optional[dict]:
    """The verified plan for `kind` (None until the student has passed its check)."""
    if not W.plan_complete(ctx.pg, kind, ctx.h):
        return None
    stt = plan_state(ctx, kind)
    dec, df = reference_plan(ctx.scn, kind, policy_key=stt["policy"], whole=stt["whole"],
                             maintain=stt["maintain"])
    checks = check_feasibility(df, requirements_for(ctx.scn, dec.policy, False, False))
    return {"dec": dec, "df": df, "summary": summarize(df, ctx.scn, dec.policy),
            "checks": checks, "feasible": is_feasible(checks), "policy": dec.policy,
            "label": "Chase" if kind == "chase" else "Level"}


def need_both(ctx: Ctx) -> Optional[Dict[str, dict]]:
    c, l = plan_bundle(ctx, "chase"), plan_bundle(ctx, "level")
    if not (c and l):
        feedback("warn", "Finish and pass the check on **both** plans (Build chase and level "
                 "plans) first.")
        return None
    return {"chase": c, "level": l}


def fmtv(metric: str, v) -> str:
    if "cost" in metric.lower():
        return money(v)
    if metric in ("On-time fulfillment", "Demand fulfilled by year end"):
        return f"{v:.1f}%"
    if metric in ("Highest workforce", "Lowest workforce", "Total hires", "Total layoffs"):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,.0f}"


def metrics_table(plans: Dict[str, dict], rows) -> str:
    head = "".join(f"<th>{html.escape(p['label'])}</th>" for p in plans.values())
    body = ""
    for r in rows:
        tip = html.escape(METRIC_DEFINITIONS.get(r, ""), quote=True)
        body += (f"<tr><td class='l' title='{tip}'>{html.escape(r)}" + (" ⓘ" if tip else "") + "</td>"
                 + "".join(f"<td>{fmtv(r, p['summary'][r])}</td>" for p in plans.values()) + "</tr>")
    feas = "".join("<td>" + ("✔ Meets all requirements" if p["feasible"] else
                             "✖ Does NOT meet: " + html.escape("; ".join(
                                 c["label"] for c in p["checks"] if not c["ok"]))) + "</td>"
                   for p in plans.values())
    body += f"<tr><td class='l'>Requirements (terminal, storage, service)</td>{feas}</tr>"
    return ("<div class='aa-wrap'><table class='aa-table'><thead><tr><th class='l'>Metric (hover ⓘ)</th>"
            f"{head}</tr></thead><tbody>{body}</tbody></table></div>")


COMPARE_ROWS = ["Total regular labor cost", "Total hiring cost", "Total layoff cost",
                "Total holding cost", "Total backorder cost", "Total cost", "Highest inventory",
                "Number of workforce changes", "On-time fulfillment", "Months with shortages",
                "Months ending with backlog", "Ending backlog", "Idle capacity (bottles)"]


def whatif(ctx: Ctx, plans: Dict[str, dict]) -> None:
    with st.expander("🧪 What-if: how do the plans respond to a demand change?"):
        st.caption("Scale **every** month's demand and re-run both of your strategies, with your own "
                   "settings, on the new demand.")
        f = st.slider("Demand change", 0.7, 1.3, 1.0, 0.05, format="%.2fx", key="sens_factor")
        scaled = replace(ctx.scn, demand=tuple(max(0.0, round(d * f)) for d in ctx.scn.demand))
        out = {}
        for k in ("chase", "level"):
            stt = plan_state(ctx, k)
            dec, df = reference_plan(scaled, k, policy_key=stt["policy"], whole=stt["whole"],
                                     maintain=stt["maintain"])
            out[k] = summarize(df, scaled, dec.policy)
        c1, c2 = st.columns(2)
        for col, k in ((c1, "chase"), (c2, "level")):
            base = plans[k]["summary"]["Total cost"]
            col.metric(f"{k.title()} total cost", money(out[k]["Total cost"]),
                       delta=money(out[k]["Total cost"] - base), delta_color="inverse")
            col.caption(f"On-time fulfillment {out[k]['On-time fulfillment']:.1f}% · ending backlog "
                        f"{out[k]['Ending backlog']:,.0f}")
        if abs(f - 1.0) > 1e-9:
            cheaper = "Chase" if out["chase"]["Total cost"] < out["level"]["Total cost"] else "Level"
            st.caption(f"At **{f:.2f}×** demand the cheaper plan is **{cheaper}**. Did the ranking "
                       "change? What does that say about robustness?")


def render_compare(ctx: Ctx) -> None:
    plans = need_both(ctx)
    if not plans:
        return
    cs, ls = plans["chase"], plans["level"]
    p_radio(ctx, "Before you study the table — which plan do you **predict** is cheaper overall?",
            ["Chase", "Level"], "cmp_pred", horizontal=True, optional=True)
    st.markdown(metrics_table(plans, COMPARE_ROWS), unsafe_allow_html=True)
    st.caption("Hover ⓘ on a metric for its definition. **On-time fulfillment** counts only demand "
               "shipped in the month it was due; **Ending backlog** is what is still unfilled in "
               "December.")
    notes = assumption_gaps({"Chase": cs["policy"], "Level": ls["policy"]}, ctx.scn)
    for n in notes:
        st.info("⚖️ **Not like-for-like:** " + n)
    with st.expander("📏 Model rules"):
        model_rules(ctx)

    st.divider()
    st.subheader("Interpret the numbers")

    def pick(metric, mode):
        a, b = cs["summary"][metric], ls["summary"][metric]
        if abs(a - b) < 1e-9:
            return "About the same"
        lower = a < b
        return ("Chase" if lower else "Level") if mode == "lower" else ("Level" if lower else "Chase")

    qdefs = [("q_cost", "Which plan has the **lower total annual cost**?", pick("Total cost", "lower"),
              "Read the **Total cost** row again."),
             ("q_wf", "Which plan **changes its workforce more**?",
              pick("Number of workforce changes", "higher"),
              "Compare hiring, layoff cost and **Number of workforce changes**."),
             ("q_inv", "Which plan **keeps more bottles sitting in inventory**?",
              pick("Highest inventory", "higher"), "Compare **Highest inventory** and holding cost."),
             ("q_serv", "Which plan has **worse on-time fulfillment** (leaves more demand late)?",
              pick("On-time fulfillment", "lower"), "Compare **On-time fulfillment** and **Months with shortages**.")]
    opts = ["Chase", "Level", "About the same"]
    for key, q, correct, hint in qdefs:
        ans = p_radio(ctx, q, opts, key, optional=True, horizontal=True)
        if ans is not None and (ctx.live or SS.get("cmp_checked")):
            feedback("ok", "That's what the numbers show.") if ans == correct else \
                st.caption("🔎 Not quite — " + hint)
    if st.button("Check my work", key="chk_compare", type="primary"):
        SS["cmp_checked"] = True
        st.rerun()
    if ctx.ans.get("q_cost") is not None and ctx.ans.get("cmp_pred"):
        cheaper = pick("Total cost", "lower")
        st.caption(f"🔮 You predicted **{ctx.ans['cmp_pred']}** would be cheaper; the Total cost row "
                   f"shows **{cheaper}** — " + ("your prediction held." if ctx.ans["cmp_pred"] == cheaper
                                                else "the opposite of your guess."))
    p_text_input(ctx, "In your own words, what does the **cheaper** plan give up to be cheaper?", "cmp_tradeoff")

    st.divider()
    st.subheader("Concept check — why?")
    cc = [("cc1", "A level plan builds up inventory in the spring. **Why?**",
           "To cover the summer peak with steady production",
           ["To cover the summer peak with steady production", "Because it hired extra summer workers",
            "To use up the beginning inventory faster"]),
          ("cc2", "A chase plan has very little holding cost. **Why?**",
           "It produces close to each month's demand, so little is left over",
           ["It produces close to each month's demand, so little is left over", "It never hires anyone",
            "It has no workers in slow months"]),
          ("cc3", "Frequent hiring and layoffs mostly drive up **which** cost?", "Hiring + layoff cost",
           ["Hiring + layoff cost", "Holding cost", "Backorder cost"])]
    for key, q, correct, options in cc:
        a = p_radio(ctx, q, options, key, optional=True)
        if a is not None and (ctx.live or SS.get("cmp_checked")):
            st.success("✅ Correct.") if a == correct else st.caption("🔎 Not quite — think about what each plan does month to month.")
    whatif(ctx, plans)
    st.divider()
    excel_panel.render_export(ctx, export_plans(ctx, plans), plan_state(ctx, "level")["whole"], "xl_export_cmp")
    footer_nav(ctx)


def export_plans(ctx: Ctx, plans: Dict[str, dict]) -> dict:
    out = {}
    for k, p in plans.items():
        stt = plan_state(ctx, k)
        out[p["label"]] = {"df": p["df"], "summary": p["summary"], "checks": p["checks"],
                           "settings": {"Worker model": "Whole workers" if stt["whole"] else "Partial workers",
                                        "Safety-stock target": f"{p['policy'].safety_target:,.0f}"}}
    return out


# --------------------------------------------------------------------------- #
# Hybrid design challenge
# --------------------------------------------------------------------------- #
def hybrid_state(ctx: Ctx) -> dict:
    h = ctx.pg.setdefault("hybrid", {})
    h.setdefault("state", {})
    return h


def feasibility_panel(res) -> None:
    st.markdown("**Feasibility panel** — every requirement your plan must meet")
    rows = [{"Status": "✔ Met" if c["ok"] else "✖ NOT met", "Requirement": c["label"],
             "Detail": c["detail"]} for c in res.checks]
    st.dataframe(pd.DataFrame(rows), hide_index=True, **STRETCH)
    if res.feasible:
        feedback("ok", "This plan meets every requirement, so it is feasible.")
    else:
        feedback("bad", "This plan is **not feasible**: " + "; ".join(c["label"] for c in res.failed_checks()) + ".")


def render_hybrid(ctx: Ctx) -> None:
    s = ctx.scn
    plans = need_both(ctx)
    if not plans:
        return
    h = hybrid_state(ctx)
    cs, ls = plans["chase"], plans["level"]
    st.write("Neither pure chase nor pure level is always best — but a hybrid is **not guaranteed** to "
             "beat them either. **You** set the workforce (and optionally overtime and "
             "subcontracting) and judge the result under the **same rules**.")
    model_rules(ctx)

    c1, c2 = st.columns(2)
    with c1:
        wm = p_radio(ctx, "Worker model for this comparison",
                     ["Whole workers", "Partial workers (worker-equivalents)"], "hy_wmodel",
                     horizontal=True)
    with c2:
        maintain = p_checkbox(ctx, f"Keep the {s['safety_stock']:,.0f}-bottle safety-stock target", "hy_maintain",
                              default=ls["policy"].maintains_safety)
    whole = wm.startswith("Whole")
    mode = p_radio(ctx, "Workforce decisions", ["Quarterly (introductory)", "Monthly (optional, advanced)"],
                   "hy_mode", horizontal=True)
    monthly = mode.startswith("Monthly")

    default_w = default_workers(s, whole)
    qlabels = s.quarter_labels()
    use_ot = p_checkbox(ctx, f"Use overtime — up to {s['overtime_pct']:.0%} of regular capacity, "
                        f"{moneym(s['overtime_cost'])} per overtime bottle (extra), only when regular "
                        "capacity is fully used", "hy_use_ot")
    auto_ot = False
    if use_ot:
        auto_ot = p_radio(ctx, "Overtime quantities", ["I choose the quantities",
                          "Assistance: fill shortages automatically"], "hy_ot_mode",
                          horizontal=True).startswith("Assistance")
    use_sub = p_checkbox(ctx, f"Use subcontracting — up to {s['subcontract_capacity']:,.0f} bottles/month at "
                         f"{moneym(s['subcontract_cost'])}/bottle", "hy_use_sub")
    auto_sub = False
    if use_sub:
        auto_sub = p_radio(ctx, "Subcontract quantities", ["I choose the quantities",
                           "Assistance: fill remaining shortages automatically"], "hy_sub_mode",
                           horizontal=True).startswith("Assistance")
    if auto_ot or auto_sub:
        st.info("ℹ️ **Assistance is on:** the app adds overtime/subcontracting only to cover "
                "shortages. It does not look for the cheapest plan or protect safety stock — that "
                "judgement is yours.")

    qw, mw, reg, ot, sub = [], [], [], [], []
    if not monthly:
        st.markdown("**Workers each quarter** (carried across that quarter's 3 months)")
        cols = st.columns(4)
        for i, (col, lab) in enumerate(zip(cols, qlabels)):
            with col:
                qw.append(p_number(ctx, lab, f"dc_q{i}", default=int(default_w), min_value=0,
                                   max_value=200, step=1 if whole else 0.5))
    else:
        tok = SS.get("_nav_token", 0)
        if SS.get("hy_tok") != tok or "hy_base" not in SS:
            saved = h.get("monthly") or {}
            base = pd.DataFrame({"Month": list(s.months),
                                 "Workers": saved.get("Workers", [float(default_w)] * 12),
                                 "Regular production (blank = full capacity)": saved.get("Regular", [None] * 12),
                                 "Overtime": saved.get("Overtime", [0.0] * 12),
                                 "Subcontract": saved.get("Subcontract", [0.0] * 12)})
            SS["hy_base"], SS["hy_tok"] = base, tok
            SS["hy_ver"] = SS.get("hy_ver", 0) + 1
        cfgc = {"Month": st.column_config.Column(disabled=True),
                "Workers": st.column_config.NumberColumn(min_value=0, step=1 if whole else 0.25),
                "Regular production (blank = full capacity)": st.column_config.NumberColumn(min_value=0, step=100),
                "Overtime": st.column_config.NumberColumn(min_value=0, step=100, disabled=not use_ot or auto_ot),
                "Subcontract": st.column_config.NumberColumn(min_value=0, step=100, disabled=not use_sub or auto_sub)}
        st.markdown("**Monthly decisions** — one row per month")
        ed = st.data_editor(SS["hy_base"], hide_index=True, **STRETCH, height=35 * 13 + 3,
                            column_config=cfgc, key=f"hy_ed_{SS['hy_ver']}")
        def lst(c):
            return [None if pd.isna(v) else float(v) for v in ed[c].tolist()]
        mw = [0.0 if v is None else v for v in lst("Workers")]
        reg = lst("Regular production (blank = full capacity)")
        ot, sub = lst("Overtime"), lst("Subcontract")
        h["monthly"] = {"Workers": mw, "Regular": reg, "Overtime": ot, "Subcontract": sub}

    inp = HybridInputs(mode="monthly" if monthly else "quarterly", quarter_workers=qw,
                       month_workers=mw, regular=reg, overtime=ot, subcontract=sub,
                       use_overtime=use_ot, use_subcontract=use_sub, auto_overtime=auto_ot,
                       auto_subcontract=auto_sub, worker_mode="whole" if whole else "partial",
                       maintain_safety=maintain)
    h["inputs"] = inp.to_json()
    res = resolve_hybrid(s, inp)
    cmp_ = compare_hybrid(s, res)

    m1, m2, m3 = st.columns(3)
    m1.metric("Your plan's total cost", money(res.total_cost))
    m2.metric("On-time fulfillment", f"{res.summary['On-time fulfillment']:.1f}%")
    m3.metric("Meets every requirement?", "✔ Yes" if res.feasible else "✖ No")
    for n in res.auto_notes:
        st.caption("🤖 " + n)
    feasibility_panel(res)

    st.markdown("**Fair comparison — chase, level and your hybrid under identical assumptions** "
                f"({'whole' if whole else 'partial'} workers; "
                + ("safety-stock target kept" if maintain else "no safety-stock target")
                + "; same terminal rules)")
    st.dataframe(pd.DataFrame([{"Plan": r["plan"], "Total cost": money(r["cost"]),
                                "On-time fulfillment": f"{r['on_time']:.1f}%",
                                "Meets every requirement": "✔ Yes" if r["feasible"] else "✖ No"}
                               for r in cmp_["rows"]]), hide_index=True, **STRETCH)
    st.write(cmp_["verdict"])
    st.caption("A hybrid does not always beat both reference plans — if it doesn't, that is a "
               "finding, not a failure.")
    gaps = assumption_gaps({"Your chase": cs["policy"], "Your level": ls["policy"], "Hybrid": res.policy}, s)
    if gaps:
        st.info("⚖️ **Your own chase/level plans used different assumptions from this hybrid** — "
                "that is why the numbers above (re-run on the hybrid's basis) can differ from "
                "your Compare table. " + " ".join(gaps))

    with st.expander("Monthly detail (regular and overtime shown separately)"):
        cols = ["Month", "Forecast Demand", "Workers", "Regular Capacity", "Regular Production",
                "Idle Capacity", "Overtime Production", "Subcontract Production", "Ending Inventory",
                "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Overtime Cost",
                "Subcontract Cost", "Holding Cost", "Backorder Cost", "Total Monthly Cost"]
        st.dataframe(res.df[cols], hide_index=True, **STRETCH)
        st.caption(f"Plan total = {moneym(res.total_cost)} (every cost column above, summed).")

    if st.button("💾 Save this plan as an attempt", type="primary", key="hy_save"):
        register_attempt(h["state"], res)
        st.rerun()
    stt = h["state"]
    st.markdown(f"**Saved attempts: {int(stt.get('attempts', 0))}**")
    b, i = stt.get("best_feasible"), stt.get("lowest_cost_infeasible")
    if b:
        st.success(f"🏆 **Best FEASIBLE plan so far: {moneym(b['cost'])}** (on-time {b['on_time']:.1f}%). "
                   "This is the one reported.")
    else:
        st.info("No feasible plan saved yet — only feasible plans can be your 'best'.")
    if i:
        st.warning(f"Lowest-cost attempt that is **NOT feasible**: {moneym(i['cost'])} — fails: "
                   + "; ".join(i.get("failed", [])) + ". It is kept separately and never replaces "
                   "your best feasible plan.")
    if b and abs(b["cost"] - min(cs["summary"]["Total cost"], ls["summary"]["Total cost"])) > 0:
        st.caption("Compare with the cheaper of your reference plans using the fair-comparison table above.")
    excel_import_hybrid(ctx)
    footer_nav(ctx)


def excel_import_hybrid(ctx: Ctx) -> None:
    with st.expander("📊 Monthly hybrid plan in Excel"):
        data = excel_panel.template_bytes(ctx, True)
        st.download_button("⬇ Download Excel workbook (Hybrid Plan sheet included)", data,
                           file_name=f"Aggregate_Anxiety_scenario_{ctx.scn.seed}.xlsx",
                           key="xl_dl_hybrid",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        up = st.file_uploader("Upload completed workbook", type=["xlsx"], key="xl_up_hybrid")
        if up is not None:
            res = excel_io.import_workbook(up.getvalue(), ctx.scn, whole=True, kinds=("hybrid",))
            excel_panel.show_messages(res)
            if res.ok and res.hybrid:
                if st.button("Load these monthly decisions", key="xl_load_hybrid"):
                    hy = res.hybrid
                    ctx.pg["hybrid"]["monthly"] = {"Workers": hy["workers"], "Regular": hy["regular"],
                                                   "Overtime": hy["overtime"], "Subcontract": hy["subcontract"]}
                    SS["hy_tok"] = None
                    SS["hy_mode_force"] = True
                    ctx.ans["hy_mode"] = "Monthly (optional, advanced)"
                    SS.pop("w_hy_mode", None)
                    st.rerun()


# --------------------------------------------------------------------------- #
# Advanced
# --------------------------------------------------------------------------- #
def decision_options(ctx: Ctx, plans) -> Dict[str, Decision]:
    opts = {}
    for k, p in (plans or {}).items():
        opts[f"My {p['label'].lower()} plan"] = p["dec"]
    b = (ctx.pg.get("hybrid") or {}).get("state", {}).get("best_feasible")
    if b:
        res = resolve_hybrid(ctx.scn, HybridInputs.from_json(b["inputs"]))
        w = [float(x) for x in res.df["Workers"]]
        opts["My best feasible hybrid"] = Decision(w, list(res.df["Regular Production"]),
                                                  res.overtime, res.subcontract, res.policy)
    return opts


def render_advanced(ctx: Ctx) -> None:
    s = ctx.scn
    ctx.pg["advanced_visited"] = True
    plans = {k: v for k, v in (("chase", plan_bundle(ctx, "chase")), ("level", plan_bundle(ctx, "level"))) if v}
    opts = decision_options(ctx, plans)
    shown = False
    if s["forecast_error_pct"] > 0:
        shown = True
        forecast_error(ctx, opts)
    if s["shelf_life_months"] > 0:
        shown = True
        perishability(ctx, opts)
    rep = ctx.pg.get("report", {})
    reveal = ctx.cfg.get("benchmark_reveal", "after_submission")
    if reveal == "always" or (reveal == "after_submission" and rep.get("final_generated")):
        shown = True
        benchmark_section(ctx)
    elif reveal == "after_submission":
        st.info("🔒 An optimization benchmark is available **after you submit your final report**, so "
                "it can't shortcut your own analysis.")
    if not shown and reveal != "after_submission":
        st.info("Your instructor hasn't enabled advanced exercises for this assignment.")
    footer_nav(ctx)


def forecast_error(ctx: Ctx, opts: Dict[str, Decision]) -> None:
    s = ctx.scn
    u = ctx.pg.setdefault("uncertainty", {})
    st.subheader("Forecast error — judge a committed plan")
    st.write(f"Your forecast is uncertain (typical monthly error about **{s['forecast_error_pct']:.0%}**). "
             "Commit to **one** plan using only the forecast; then see how it performs across "
             f"{200} reproducible demand outcomes you have **not seen**. A committed plan can't react "
             "— workforce and production quantities are fixed in advance.")
    if not opts:
        st.info("Build your plans first.")
        return
    if not u.get("committed"):
        choice = st.radio("Plan to commit", list(opts), key="unc_choice")
        if st.button("🔒 Commit this plan and reveal how it performs", type="primary", key="unc_commit"):
            ev = evaluate_committed(s, opts[choice])
            u.update({"committed": True, "plan_name": choice, "eval": ev.as_dict(),
                      "commits": int(u.get("commits", 0)) + 1})
            st.rerun()
        return
    e = u["eval"]
    st.success(f"Committed: **{u['plan_name']}** (commitment #{u.get('commits', 1)}).")
    c1, c2, c3 = st.columns(3)
    c1.metric("Cost if the forecast is right", money(e["forecast_cost"]))
    c2.metric("Expected cost across outcomes", money(e["mean_cost"]),
              delta=money(e["mean_cost"] - e["forecast_cost"]), delta_color="inverse")
    c3.metric("Bad-year cost (90th percentile)", money(e["p90_cost"]))
    d1, d2, d3 = st.columns(3)
    d1.metric("Average on-time fulfillment", f"{e['mean_on_time']:.1f}%")
    d2.metric("Chance of at least one shortage month", f"{e['prob_any_shortage']:.0%}")
    if e["prob_meets_service"] is not None:
        d3.metric(f"Chance of ≥{e['service_requirement']:g}% on-time", f"{e['prob_meets_service']:.0%}")
    st.bar_chart(pd.DataFrame({"Chance of shortage": e["month_shortage_prob"]}, index=list(s.months)))
    st.caption("Think about: which month is riskiest, and what would you change (safety stock, "
               "overtime, subcontracting) to cut that risk — and at what cost?")
    if st.button("Withdraw commitment and re-plan", key="unc_reset"):
        u["committed"] = False
        st.rerun()


def perishability(ctx: Ctx, opts: Dict[str, Decision]) -> None:
    s = ctx.scn
    L = int(s["shelf_life_months"])
    st.subheader("Perishable inventory — age tracking and disposal")
    st.write(f"Bottles stay sellable for **{L} months**. The app tracks how old each bottle is "
             "(oldest shipped first) and **disposes** stock that expires unsold, at "
             f"{moneym(s['disposal_cost'])} per bottle. The 'above two months' demand' warning in the "
             "worksheets is only a heuristic; this test uses real inventory age. Beginning inventory "
             "is assumed fresh on 1 January.")
    if not opts:
        st.info("Build your plans first.")
        return
    rows = []
    for name, dec in opts.items():
        base = summarize(run_decision(s, dec), s, dec.policy)
        df = run_decision(s, dec, shelf_life=L)
        sm = summarize(df, s, dec.policy)
        rows.append({"Plan": name, "Cost (no spoilage)": money(base["Total cost"]),
                     "Cost (with spoilage)": money(sm["Total cost"]),
                     "Bottles disposed": f"{sm['Total disposed bottles']:,.0f}",
                     "Disposal cost": money(sm["Total disposal cost"]),
                     "On-time fulfillment": f"{sm['On-time fulfillment']:.1f}%",
                     "Ending backlog": f"{sm['Ending backlog']:,.0f}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, **STRETCH)
    st.caption("Plans that build inventory early lose bottles to expiry and may then run short at the peak.")


def benchmark_section(ctx: Ctx) -> None:
    s = ctx.scn
    st.subheader("Optimization benchmark")
    if not benchmark.available():
        st.info("The benchmark needs SciPy (`pip install scipy`), which isn't installed on this server.")
        return
    h = (ctx.pg.get("hybrid") or {}).get("inputs") or {}
    whole = h.get("worker_mode", "whole") == "whole"
    maintain = bool(h.get("maintain_safety"))
    ot = bool(h.get("use_overtime", True))
    sub = bool(h.get("use_subcontract", False))
    st.write("A mathematical optimizer searches for the cheapest plan that meets **the same requirements "
             "as your model** (integer workers when whole workers are used, storage and terminal "
             "rules, on-time service, overtime/subcontract limits). Settings follow your hybrid plan "
             f"({'whole' if whole else 'partial'} workers, safety stock {'kept' if maintain else 'not kept'}, "
             f"overtime {'on' if ot else 'off'}, subcontracting {'on' if sub else 'off'}).")
    if st.button("Compute benchmark", key="bm_go"):
        with st.spinner("Solving…"):
            SS["_bench"] = benchmark.solve(s, whole=whole, maintain=maintain, use_overtime=ot,
                                           use_subcontract=sub)
    r = SS.get("_bench")
    if not r:
        return
    if r.cost is None:
        st.warning(r.message)
        return
    (st.success if r.status == "optimal" else st.info)(
        ("✔ Proven optimal. " if r.status == "optimal" else "A feasible benchmark (NOT proven optimal). ") + r.message)
    b = (ctx.pg.get("hybrid") or {}).get("state", {}).get("best_feasible")
    c1, c2 = st.columns(2)
    c1.metric("Benchmark plan cost", money(r.cost))
    if b:
        c2.metric("Your best feasible hybrid", money(b["cost"]), delta=money(b["cost"] - r.cost),
                  delta_color="inverse")
    st.caption("The benchmark shows what is achievable under these simplified rules with perfect "
               "foresight of the forecast. It is a yardstick for discussion, not an answer key, and "
               "real plants face uncertainty the model leaves out.")
    with st.expander("Benchmark monthly decisions"):
        st.dataframe(r.df[["Month", "Workers", "Regular Production", "Overtime Production",
                           "Subcontract Production", "Ending Inventory", "Backorders",
                           "Total Monthly Cost"]], hide_index=True, **STRETCH)
