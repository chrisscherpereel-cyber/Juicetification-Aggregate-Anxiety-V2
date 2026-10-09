"""Stages 7-8: the student builds the chase and level worksheets (in the app or in Excel)."""

from __future__ import annotations

import html
import re
from typing import Dict, Optional

import pandas as pd
import streamlit as st

from aggplan import text as T
from aggplan import workflow as W
from aggplan.engine import BINV_POLICIES, level_rate, reference_plan, summarize
from aggplan.formula import column_letter, shift_refs
from aggplan.scenario import resolve_scenario
from aggplan.worksheet import (EDIT_COLS, MONEY_COLS, ROW_BASE, STATUS_LABEL, WS_COLS, Grade,
                               blank_worksheet, cell_raw, col_letter, column_sums, fmt,
                               grade_worksheet, truth_worksheet, units)

from .common import Ctx, SS, STRETCH, feedback, footer_nav, money, moneym, p_number, p_radio
from . import excel_panel
from .stages_early import brief_tables

ICON = {"ok": "✔", "wrong": "✖", "blank": "○", "error": "⚠"}


def plan_state(ctx: Ctx, kind: str) -> dict:
    plans = ctx.pg.setdefault("plans", {})
    st_ = plans.setdefault(kind, {})
    st_.setdefault("ws", None)
    st_.setdefault("policy", "use_first")
    st_.setdefault("whole", True)
    st_.setdefault("maintain", False)
    st_.setdefault("total_in", 0.0)
    st_.setdefault("completed", False)
    st_.setdefault("n_checks", 0)
    st_.setdefault("tries", 1)
    st_.setdefault("stuck", 0)
    return st_


def df_from_state(ctx: Ctx, kind: str) -> pd.DataFrame:
    base = blank_worksheet(ctx.scn)
    ws = plan_state(ctx, kind).get("ws")
    if isinstance(ws, dict):
        for c in EDIT_COLS:
            vals = ws.get(c)
            if isinstance(vals, list) and len(vals) == len(base):
                base[c] = ["" if v is None else str(v) for v in vals]
    return base


def state_from_df(df: pd.DataFrame) -> Dict[str, list]:
    out = {}
    for c in EDIT_COLS:
        out[c] = ["" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)
                  for v in df[c].tolist()]
    return out


def reset_worksheet(ctx: Ctx, kind: str) -> None:
    plan_state(ctx, kind)["ws"] = None
    SS[f"{kind}_ver"] = SS.get(f"{kind}_ver", 0) + 1
    SS[f"{kind}_tok"] = None


def set_worksheet(ctx: Ctx, kind: str, df: pd.DataFrame) -> None:
    plan_state(ctx, kind)["ws"] = state_from_df(df)
    SS[f"{kind}_ver"] = SS.get(f"{kind}_ver", 0) + 1
    SS[f"{kind}_tok"] = None


def new_scenario(ctx: Ctx, kind: str) -> None:
    """Replacement scenario after the worked solution was opened. Deterministic (derived from
    the base seed and a variant counter, so it is reproducible and auditable); with an
    instructor-fixed demand curve the replacement is a seeded variant of that curve."""
    ctx.pg["variant"] = int(ctx.pg.get("variant", 0)) + 1
    ctx.pg.setdefault("lineage", []).append(
        {"variant": ctx.pg["variant"], "reason": f"replacement after worked solution ({kind} plan)"})
    plan_state(ctx, kind)["tries"] += 1
    for k in ("chase", "level"):
        stt = plan_state(ctx, k)
        stt["completed"] = False
        stt["sig"] = ""
        stt["last"] = None
        stt["total_in"] = 0.0
        SS.pop(f"w_{k}_totalcost", None)
        reset_worksheet(ctx, k)
    for k in ("chase_reveal", "level_reveal"):
        SS.pop(k, None)
    # everything computed from the old numbers is stale: self-checks, comparison answers,
    # the hybrid attempts and any committed forecast-error plan
    for k in list(ctx.ans):
        if k in ("lp_in", "lw_in", "cmp_pred", "cmp_tradeoff", "cc1", "cc2", "cc3") or k.startswith("q_"):
            ctx.ans.pop(k, None)
    ctx.pg["hybrid"] = {}
    ctx.pg["uncertainty"] = {}
    ctx.pg.pop("report", None)
    for k in list(SS.keys()):
        if k.startswith(("w_lp_in", "w_lw_in", "w_q_", "w_cc", "w_cmp_", "hy_", "w_hy_", "w_dc_q")):
            SS.pop(k, None)
    SS.pop("cmp_checked", None)
    SS.pop("_pdf", None)


def review_html(df: pd.DataFrame, grade: Grade, show_status: bool, totals: Dict[str, str]) -> str:
    cols = list(df.columns)
    head1 = "<th></th>" + "".join(f"<th>{column_letter(i)}</th>" for i in range(len(cols)))
    head2 = "<th>Row</th>" + "".join(
        f"<th class='{'l' if c == 'Month' else ''}'>{html.escape(c)}</th>" for c in cols)
    body = ""
    n = len(df)
    for i in range(n):
        cells = f"<td>{i + ROW_BASE}</td>"
        for c in cols:
            if c in ("Month", "Forecast Demand"):
                v = df[c].iloc[i]
                cells += (f"<td class='l'>{html.escape(str(v))}</td>" if c == "Month"
                          else f"<td>{float(v):,.0f}</td>")
                continue
            shown = grade.display[c][i]
            s = grade.status[c][i]
            err = grade.result.errors.get((c, i))
            cls, mark, title = "", "", ""
            if err is not None:
                cls, mark, title = "err", "⚠ ", html.escape(err.message)
                shown = html.escape(grade.result.raw.get((c, i), ""))
            elif show_status and s in ("ok", "wrong"):
                cls = "ok" if s == "ok" else "bad"
                mark = ICON[s] + " "
                title = STATUS_LABEL[s]
            elif show_status and s == "blank":
                cls, mark, title = "blank", "○ ", STATUS_LABEL["blank"]
            elif s != "blank":
                shown = grade.result.values[c][i]
                shown = fmt(shown) if shown is not None else ""
            cells += f"<td class='{cls}' title='{title}'>{mark}{shown}</td>"
        body += f"<tr>{cells}</tr>"
    foot = "<td>Σ</td>" + "".join(
        ("<td class='l'>12-month total</td>" if c == "Month" else f"<td>{totals.get(c, '')}</td>")
        for c in cols)
    return (f"<div class='aa-wrap'><table class='aa-table' role='table'>"
            f"<caption>Calculated values (formulas resolved)</caption>"
            f"<thead><tr>{head1}</tr><tr>{head2}</tr></thead><tbody>{body}"
            f"<tr class='aa-sum'>{foot}</tr></tbody></table></div>")


def wrong_cell_explanations(ctx: Ctx, kind: str, grade: Grade, truth: pd.DataFrame, whole: bool,
                            limit: int = 12):
    diag = T.diagnostics(ctx.scn, kind, whole)
    out = []
    for c, i in grade.wrong_cells()[:limit]:
        row = i + ROW_BASE
        ref = f"{col_letter(c)}{row}"
        err = grade.result.errors.get((c, i))
        if err is not None:
            out.append(f"⚠ **{ref} · {ctx.scn.months[i]} · {c}** — formula problem: {err.message}")
            continue
        val, tv = grade.values[c][i], float(truth[c].iloc[i])
        direction = "isn't a number" if val is None else (
            "is **too high**" if val > tv else "is **too low**")
        rule, cause = diag.get(c, ("", ""))
        shown = f" (you have {fmt(val)})" if val is not None else ""
        out.append(f"✖ **{ref} · {ctx.scn.months[i]} · {c}** {direction}{shown}. Rule: {rule}. "
                   f"Likely cause: {cause}.")
    return out, max(0, len(grade.wrong_cells()) - limit)


def formula_builder(ctx: Ctx, kind: str, df: pd.DataFrame) -> None:
    s = ctx.scn
    expr_key = f"{kind}_expr"
    SS.setdefault(expr_key, "=")
    name_to_letter = {c: column_letter(i) for i, c in enumerate(WS_COLS)}
    st.caption("Build a formula one piece at a time and watch it grow. (You can also type "
               "formulas straight into the grid.)")
    st.markdown(f"### `{SS[expr_key] or '='}`")
    p1, p2 = st.columns(2)
    if p1.button("⌫ Delete last", key=f"{kind}_back"):
        e = SS[expr_key]
        m = re.search(r"([A-Za-z]{1,2}\d{1,3}|[A-Za-z]+|.)$", e[1:]) if len(e) > 1 else None
        SS[expr_key] = e[:-len(m.group(1))] if m else "="
        st.rerun()
    if p2.button("🗑 Clear", key=f"{kind}_clear"):
        SS[expr_key] = "="
        st.rerun()
    st.markdown("**① Add a cell**")
    a, b, c = st.columns([3, 2, 2])
    rc = a.selectbox("Column", [x for x in WS_COLS if x != "Month"], key=f"{kind}_rcol",
                     format_func=lambda cn: f"{cn} (col {name_to_letter[cn]})")
    rm = b.selectbox("Month", list(s.months), key=f"{kind}_rrow",
                     format_func=lambda m: f"{m} (row {list(s.months).index(m) + ROW_BASE})")
    if c.button("Add cell ➕", key=f"{kind}_insref", **STRETCH):
        SS[expr_key] += f"{name_to_letter[rc]}{list(s.months).index(rm) + ROW_BASE}"
        st.rerun()
    st.markdown("**② Add an operator or function**")
    ops = [("+", "+"), ("−", "-"), ("×", "*"), ("÷", "/"), ("(", "("), (")", ")"),
           (",", ","), ("MAX(", "MAX("), ("ROUNDUP(", "ROUNDUP(")]
    for (lab, op), oc in zip(ops, st.columns(len(ops))):
        if oc.button(lab, key=f"{kind}_op_{op}", **STRETCH):
            SS[expr_key] += op
            st.rerun()
    st.markdown("**③ Add a rate or number from the brief**")
    v1, v2 = st.columns([3, 2])
    consts = s.named_constants()
    nm = v1.selectbox("Value", list(consts), key=f"{kind}_nm",
                      format_func=lambda k: f"{k} = {consts[k]:,.4g}")
    if v2.button("Add value ➕", key=f"{kind}_insnm", **STRETCH):
        SS[expr_key] += nm
        st.rerun()
    st.markdown("**④ Put the formula into a cell**")
    w1, w2, w3 = st.columns([3, 2, 2])
    tc = w1.selectbox("Into column", EDIT_COLS, key=f"{kind}_tcol")
    tm = w2.selectbox("Into row", list(s.months), key=f"{kind}_trow",
                      format_func=lambda m: f"{m} (row {list(s.months).index(m) + ROW_BASE})")
    if w3.button("✍️ Write", key=f"{kind}_write", **STRETCH):
        d = df_from_state(ctx, kind)
        d.at[list(s.months).index(tm) + ROW_BASE, tc] = SS[expr_key]
        set_worksheet(ctx, kind, d)
        SS[expr_key] = "="
        st.rerun()


def fill_down(ctx: Ctx, kind: str) -> None:
    c1, c2 = st.columns(2)
    col = c1.selectbox("Column", EDIT_COLS, key=f"{kind}_fillcol")
    tmpl = c2.text_input("January (row 2) formula", key=f"{kind}_filltmpl", placeholder="=E2*LABOR")
    if st.button("Fill down all 12 months", key=f"{kind}_fillbtn") and tmpl.strip():
        d = df_from_state(ctx, kind)
        for i in range(len(d)):
            d.at[i + ROW_BASE, col] = shift_refs(tmpl.strip(), i)
        set_worksheet(ctx, kind, d)
        st.rerun()


def plan_signature_now(ctx: Ctx, kind: str) -> str:
    return W.plan_signature(ctx.h, plan_state(ctx, kind))


def render_plan(ctx: Ctx, kind: str) -> None:
    s = ctx.scn
    stt = plan_state(ctx, kind)
    is_chase = kind == "chase"
    cap_ok = W.check_passed(ctx.pg, "capacity")

    # ---------------- settings ------------------------------------------------
    if is_chase:
        st.write("**You** fill in the whole worksheet. Work out each month's **Regular "
                 "Production** first, then the **Workers** it needs, then the rest. Cells accept "
                 "numbers, formulas and cell references — the same formulas you would write in "
                 f"Excel (row 1 holds the headings; **January is row {ROW_BASE}**).")
    else:
        st.write("Level = **constant** production and a **constant** workforce. In **January** you "
                 f"hire or lay off from the starting {s['starting_workforce']:,.0f} workers to the "
                 f"level crew, then hold it steady all year. (January is row {ROW_BASE}.)")
    model = p_radio(ctx, "Worker model", ["Whole workers (round up)", "Partial workers (worker-equivalents)"],
                    f"{kind}_wmodel", horizontal=True)
    whole = model.startswith("Whole")
    st.caption(T.workforce_model_text(s, whole))
    if is_chase:
        key = p_radio(ctx, "How should the beginning inventory be handled?", list(BINV_POLICIES),
                      "chase_policy_in", format_func=lambda k: BINV_POLICIES[k])
        maintain = key == "maintain"
        st.caption(T.policy_caption(s, key))
    else:
        pol = p_radio(ctx, "Inventory policy — this sets the ending target:",
                      ["No safety stock (consume the beginning inventory, end at 0)",
                       f"Maintain the {s['safety_stock']:,.0f}-bottle safety-stock target"],
                      "level_policy_in")
        maintain, key = pol.startswith("Maintain"), "use_first"
        target = s["safety_stock"] if maintain else 0
        st.latex(r"\text{Level rate}=\frac{\text{Total demand}+" + f"{target:,.0f}".replace(",", "{,}")
                 + r"-\text{Beginning inventory }" + f"{s['beginning_inventory']:,.0f}".replace(",", "{,}")
                 + r"}{12}")
        st.caption("Terminal requirement: " + T.term_inventory_text(s, maintain) + ".")
    stt["whole"], stt["policy"], stt["maintain"] = whole, key, maintain

    dec, plan = reference_plan(s, kind, policy_key=key, whole=whole, maintain=maintain)
    truth = truth_worksheet(plan)

    if not cap_ok:
        feedback("warn", "Finish the **Capacity** stage first — its result feeds this plan and is "
                 "needed to complete it.")

    # ---------------- reference data beside the worksheet -----------------------
    left, right = st.columns(2)
    with left.expander("📋 Scenario data (forecast, costs, rules)", expanded=False):
        brief_tables(ctx, height=220)
    with right.expander("📐 Formulas for this plan", expanded=False):
        if W.check_passed(ctx.pg, "formulas"):
            st.dataframe(pd.DataFrame(T.formula_reference(s, kind, whole), columns=["Column", "Formula"]),
                         hide_index=True, **STRETCH, height=300)
            st.caption("Brief-data names usable in formulas: "
                       + ", ".join(f"`{k}`={v:,.4g}" for k, v in s.named_constants().items()))
        else:
            st.caption("🔒 Complete the **Build the formulas** stage to unlock the formula reference.")

    if not is_chase:
        c1, c2 = st.columns(2)
        with c1:
            lp = p_number(ctx, "Level production / month (compute it first)", "lp_in",
                          min_value=0.0, max_value=1e7, step=50.0)
        with c2:
            lw = p_number(ctx, "Level workforce" + (" (whole workers)" if whole else " (may be fractional)"),
                          "lw_in", min_value=0.0, max_value=500.0, step=1.0 if whole else 0.5)
        stt["level_prod_in"], stt["level_workers_in"] = lp, lw

    with st.expander("🔮 Predict first (optional)", expanded=False):
        p_radio(ctx, f"A **{kind}** plan {'follows demand' if is_chase else 'holds output steady'}. "
                "Which cost do you think will dominate it?",
                ["Hiring + layoff (workforce changes)", "Holding inventory"], f"{kind}_pred",
                horizontal=True)

    # ---------------- worksheet editor -------------------------------------------
    tok = SS.get("_nav_token", 0)
    if SS.get(f"{kind}_tok") != tok or f"{kind}_base" not in SS:
        SS[f"{kind}_base"] = df_from_state(ctx, kind)
        SS[f"{kind}_ver"] = SS.get(f"{kind}_ver", 0) + 1
        SS[f"{kind}_tok"] = tok
    editor_key = f"{kind}_ed_{SS[f'{kind}_ver']}"
    cfgc = {}
    un = units(s, whole)
    for i, c in enumerate(WS_COLS):
        label = f"{column_letter(i)} · {c}"
        if c in EDIT_COLS:
            cfgc[c] = st.column_config.TextColumn(label, help=f"{un[c]}. Number, formula or cell "
                                                  "reference (e.g. =E2/RATE).")
        else:
            cfgc[c] = st.column_config.Column(label, disabled=True, help=un[c])
    st.markdown("**Your worksheet** — type here. Press Enter to commit and move down, Tab to move right.")
    edited = st.data_editor(SS[f"{kind}_base"], **STRETCH, hide_index=False,
                            height=35 * 13 + 3, column_config=cfgc, key=editor_key)
    edited = edited.copy()
    edited.index = list(range(ROW_BASE, ROW_BASE + len(edited)))
    stt["ws"] = state_from_df(edited)

    grade = grade_worksheet(edited, s, truth, kind=kind, whole=whole, chase_key=key)
    sig = plan_signature_now(ctx, kind)

    truth_total = float(truth["Total Monthly Cost"].sum())
    total_in = p_number(ctx, "Total annual cost of your plan (add up all 12 Total Monthly Cost cells)",
                        f"{kind}_totalcost", min_value=0.0, step=1000.0, format="%.2f")
    stt["total_in"] = total_in
    sig = plan_signature_now(ctx, kind)

    # ---------------- check -----------------------------------------------------
    def do_check():
        stt["n_checks"] = int(stt.get("n_checks", 0)) + 1
        total_ok = abs(total_in - truth_total) < 0.5
        stt["last"] = {"sig": sig, "n_ok": grade.n_ok, "n_wrong": grade.n_wrong,
                       "n_blank": grade.n_blank, "total_ok": total_ok, "total_entered": total_in,
                       "status": grade.status, "display": grade.display}
        passed = grade.complete and total_ok and cap_ok
        stt["completed"] = bool(passed)
        stt["sig"] = sig if passed else ""

    bc1, bc2 = st.columns([1, 3])
    if bc1.button("Check my work", key=f"chk_{kind}", type="primary"):
        do_check()
    live_pass = False
    if ctx.live and grade.complete and abs(total_in - truth_total) < 0.5 and cap_ok \
            and not (stt.get("completed") and stt.get("sig") == sig):
        do_check()
        live_pass = True
    last = stt.get("last")
    fresh = bool(last and last.get("sig") == sig)
    show_status = ctx.live or fresh
    if ctx.live:
        use = {"n_ok": grade.n_ok, "n_wrong": grade.n_wrong, "n_blank": grade.n_blank,
               "total_ok": abs(total_in - truth_total) < 0.5}
    else:
        use = last if fresh else None

    if not ctx.live and last and not fresh:
        bc2.info("ℹ️ You've changed the worksheet or settings since the last check — press "
                 "**Check my work** to see where you stand.")
    elif not ctx.live and not last:
        bc2.caption("Your instructor has set feedback to appear when you press **Check my work**, "
                    "so you can attempt the work first. Calculated values and formula errors "
                    "still update as you type.")

    totals = column_sums(grade.values)
    st.markdown(review_html(edited, grade, show_status, totals), unsafe_allow_html=True)
    st.caption("Legend — ✔ correct · ✖ needs attention · ○ blank · ⚠ formula error "
               "(hover a cell for details). Colors are always paired with these symbols.")

    if use:
        st.markdown(
            f"**✔ Correct cells:** {use['n_ok']} of {grade.total} · **✖ Need attention:** "
            f"{use['n_wrong']} · **○ Blank:** {use['n_blank']}")
        lines, more = wrong_cell_explanations(ctx, kind, grade, truth, whole)
        if lines and (ctx.live or fresh):
            st.markdown("**Why cells need attention**")
            for ln in lines:
                st.warning(ln)
            if more:
                st.caption(f"…and {more} more — fix these first.")
        if stt.get("last") and (fresh or ctx.live):
            tin = total_in
            if tin:
                if use["total_ok"]:
                    feedback("ok", "Your annual total cost matches the sum of the Total Monthly Cost column.")
                else:
                    feedback("warn", f"Your annual total looks too {'high' if tin > truth_total else 'low'} "
                             "— it is the sum of every month's Total Monthly Cost (see the Σ row).")
            else:
                st.caption("Enter the annual total cost above to finish.")
    for ln in sorted({n for (c, i), notes in grade.result.notes.items() for n in notes})[:4]:
        st.caption(f"ℹ️ {ln}")

    done = stt.get("completed") and stt.get("sig") == sig
    if done:
        summ = summarize(plan, s, dec.policy)
        feedback("ok", f"{kind.title()} plan complete and verified. Total cost "
                 f"**{moneym(summ['Total cost'])}**; on-time fulfillment "
                 f"**{summ['On-time fulfillment']:.1f}%**; ending backlog "
                 f"**{summ['Ending backlog']:,.0f}** bottles.")
        hf = summ["Total hiring cost"] + summ["Total layoff cost"]
        hold = summ["Total holding cost"]
        actual = "Hiring + layoff (workforce changes)" if hf >= hold else "Holding inventory"
        pred = ctx.ans.get(f"{kind}_pred")
        if pred:
            st.info(f"🔮 Prediction check: the larger of the two was **{actual}** (hiring+layoff "
                    f"{moneym(hf)} vs holding {moneym(hold)}) — "
                    + ("matches" if pred == actual else "differs from") + " your prediction.")
    elif stt.get("completed") and stt.get("sig") != sig:
        feedback("warn", "This plan was verified earlier, but you changed it since. Press "
                 "**Check my work** to re-verify.")

    # ---------------- tools -------------------------------------------------------
    t1, t2 = st.columns(2)
    with t1.expander("🖱️ Build a formula", expanded=False):
        formula_builder(ctx, kind, edited)
    with t2.expander("📋 Fill a formula down (all 12 months)", expanded=False):
        fill_down(ctx, kind)
    excel_panel.render(ctx, kind, whole, set_worksheet)

    # ---------------- stuck --------------------------------------------------------
    if ctx.cfg.get("allow_replacement", True):
        st.divider()
        rev = st.checkbox("🧑‍🏫 I'm stuck — show me how it's done", key=f"{kind}_reveal")
        if rev and not SS.get(f"{kind}_reveal_counted"):
            stt["stuck"] = int(stt.get("stuck", 0)) + 1
            SS[f"{kind}_reveal_counted"] = True
        if not rev:
            SS[f"{kind}_reveal_counted"] = False
        if rev:
            if kind == "chase":
                st.markdown(T.chase_how_to(s, key, whole))
            else:
                st.info(f"Level rate = **{level_rate(s, maintain):,.2f}** bottles/month; workforce = "
                        f"**{dec.workers[0]:g}**.")
                st.markdown(T.level_how_to(s, whole, maintain))
            st.dataframe(truth, hide_index=True, **STRETCH, height=460)
            st.warning("Now prove you learned it: rebuild the plan on a **replacement scenario** "
                       "(different numbers — " + (
                           "a reproducible variant of your instructor's demand curve" if s.fixed_demand
                           else "a new reproducible demand pattern") + "). Your earlier work on this "
                       "scenario is cleared; the replacement and your help usage are recorded.")
            if st.button("🎲 Start the replacement scenario", key=f"{kind}_newscen"):
                new_scenario(ctx, kind)
                st.rerun()
    footer_nav(ctx)
