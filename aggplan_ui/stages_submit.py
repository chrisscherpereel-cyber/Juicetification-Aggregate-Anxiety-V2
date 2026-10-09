"""Stages 12-13: recommendation/reflection and the PDF completion report."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from aggplan import identity as ident
from aggplan import persistence as persist
from aggplan import report as R
from aggplan import workflow as W
from aggplan.hybrid import HybridInputs

from . import excel_panel
from .common import Ctx, SS, STRETCH, feedback, footer_nav, money, p_radio, p_text_area
from .stages_compare import COMPARE_ROWS, export_plans, metrics_table, need_both
from .stages_build import plan_state


def render_reflect(ctx: Ctx) -> None:
    plans = need_both(ctx)
    if not plans:
        return
    with st.expander("Plan comparison (for reference while you write)", expanded=True):
        st.markdown(metrics_table(plans, ["Total cost", "Number of workforce changes",
                                          "Highest inventory", "On-time fulfillment",
                                          "Months with shortages", "Ending backlog"]),
                    unsafe_allow_html=True)
    st.subheader("Your recommendation")
    p_radio(ctx, "Which plan would you recommend?", ["Chase", "Level"], "chosen_plan",
            optional=True, horizontal=True)
    st.markdown("Justify it with **both** a numerical and a managerial argument — in your own "
                "words. What you write here is copied into your report exactly as typed.")
    txt = p_text_area(ctx, "Your justification", "recommendation", height=170,
                      placeholder="e.g. I recommend the level plan: its total cost is $X lower, and "
                      "although it holds more inventory the steady workforce is easier to manage…")
    n = W.words(txt)
    st.caption(f"{n} words — at least {W.MIN_RECOMMENDATION_WORDS} needed "
               + ("✔" if n >= W.MIN_RECOMMENDATION_WORDS else "(not yet)"))
    st.subheader("Reflect")
    for key, prompt in R.REFLECTION_PROMPTS.items():
        t = p_text_area(ctx, prompt, key, height=90)
        k = W.words(t)
        st.caption(f"{k} words — at least {W.MIN_REFLECTION_WORDS} needed "
                   + ("✔" if k >= W.MIN_REFLECTION_WORDS else "(not yet)"))
    footer_nav(ctx)


def _hybrid_cost(ctx: Ctx):
    b = ((ctx.pg.get("hybrid") or {}).get("state") or {}).get("best_feasible")
    return b["cost"] if b else None


def build_report(ctx: Ctx, force_draft: bool) -> dict:
    """Snapshot -> PDF. Raises on failure (caller reports it; nothing is recorded then)."""
    from aggplan.report_pdf import build_pdf
    rep = ctx.pg.setdefault("report", {})
    fp = W.work_fingerprint(ctx.pg)
    missing = W.missing_requirements(ctx.pg, ctx.h)
    final = not missing and not force_draft
    reuse = final and rep.get("attempt_id") and rep.get("fp") == fp and rep.get("final_generated")
    attempt_id = rep["attempt_id"] if reuse else R.new_attempt_id(final)
    tzname = ctx.cfg.get("report_timezone", "") or ""
    now = R.now_in_zone(tzname)
    key = ident.signing_key()
    snap = R.build_snapshot(ctx.scn, ctx.pg, identity=ctx.identity,
                            assignment_name=ctx.cfg.get("assignment_name") or "Aggregate Anxiety",
                            attempt_id=attempt_id, now=now, tz_label=tzname, force_draft=force_draft,
                            seal_fn=lambda h, a: ident.seal(key, h, a))
    pdf = build_pdf(snap)
    return {"bytes": pdf, "snapshot": snap, "fp": fp, "status": snap["status"],
            "attempt_id": attempt_id,
            "filename": R.safe_filename(ctx.pg.get("student_name", ""), attempt_id),
            "hash": snap["integrity"]["snapshot_hash"], "seal": snap["integrity"]["seal"],
            "generated_utc": snap["attempt"]["generated_utc"]}


def record_extra(ctx: Ctx, built: dict) -> dict:
    snap = built["snapshot"]
    costs = {}
    for k in ("chase", "level"):
        p = (snap["plans"] or {}).get(k)
        if p:
            costs[k] = p["summary"]["Total cost"]
    hc = _hybrid_cost(ctx)
    if hc is not None:
        costs["hybrid_best_feasible"] = hc
    return {"attempt_id": built["attempt_id"], "snapshot_hash": built["hash"], "seal": built["seal"],
            "model_version": snap["app"]["model_version"], "scenario_seed": snap["scenario"]["seed"],
            "scenario_variant": snap["scenario"]["variant"],
            "scenario_config_hash": snap["scenario"]["config_hash"],
            "identity": ctx.identity.mode, "generated_utc": built["generated_utc"],
            "selected_strategy": snap["strategy"]["selected"],
            "plan_costs_usd": costs,
            "plan_cost_note": "Plan cost is modelled operating cost in USD — not an academic grade.",
            "report_status": built["status"]}


def render_submit(ctx: Ctx) -> None:
    pg = ctx.pg
    rep = pg.setdefault("report", {})
    st.write("Your report is a PDF built from your verified work. **Download your report, review it, "
             "and upload the PDF to your LMS assignment.**")
    reqs = W.requirement_list(pg, ctx.h)
    rows = []
    for r in reqs:
        rows.append({"Activity": r["label"], "Required": "Yes" if r["required"] else "Optional",
                     "Status": ("✅ Complete" if r["complete"] else ("❌ Incomplete" if r["required"]
                                                                  else "➕ Not completed")),
                     "Detail": r["detail"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, **STRETCH)
    missing = W.missing_requirements(pg, ctx.h)
    if missing:
        st.error("**Before you can generate the final report:**\n" + "\n".join(f"- {m}" for m in missing))
        st.caption("You can still download a **draft** — every page is marked DRAFT and it does not "
                   "count as your submission.")
    else:
        feedback("ok", "Every required activity is complete.")

    c1, c2 = st.columns(2)
    if c1.button("Generate final report", type="primary", disabled=bool(missing), key="gen_final"):
        try:
            with st.spinner("Building your PDF…"):
                built = build_report(ctx, force_draft=False)
        except Exception as e:                                  # noqa: BLE001
            SS.pop("_pdf", None)
            st.error(f"The PDF could not be generated ({type(e).__name__}). Nothing was recorded. "
                     "Try again; if it keeps failing, tell your instructor.")
        else:
            SS["_pdf"] = built
            rep.update({"final_generated": True, "attempt_id": built["attempt_id"], "fp": built["fp"],
                        "hash": built["hash"], "generated_utc": built["generated_utc"],
                        "submissions": int(rep.get("submissions", 0)) + 1})
            extra = record_extra(ctx, built)
            rep["record_extra"] = extra
            ok, msg = persist.record_completion_safe(ctx.store, ctx.game,
                                                     ctx.identity.sid if ctx.identity.recorded else None,
                                                     built["attempt_id"], extra)
            rep["record"] = {"ok": ok, "message": msg}
    if c2.button("Prepare a draft PDF (not for submission)", key="gen_draft"):
        try:
            with st.spinner("Building draft…"):
                SS["_pdf"] = build_report(ctx, force_draft=True)
        except Exception as e:                                  # noqa: BLE001
            st.error(f"The draft could not be generated ({type(e).__name__}).")

    built = SS.get("_pdf")
    if built:
        stale = built["fp"] != W.work_fingerprint(pg)
        if stale:
            st.warning("⚠ Your work changed after this PDF was generated. Generate it again before "
                       "submitting.")
        elif built["status"] == "Final":
            st.success(f"✅ Final report ready — attempt **{built['attempt_id']}**")
            st.download_button("⬇ Download PDF for LMS submission", built["bytes"],
                               file_name=built["filename"], mime="application/pdf", type="primary",
                               key="dl_final", **STRETCH)
            st.info("Download your report, review it, and upload the PDF to your LMS assignment.")
        else:
            st.warning("This is a **DRAFT** — incomplete, marked on every page, not for submission.")
            st.download_button("⬇ Download DRAFT PDF", built["bytes"], file_name=built["filename"],
                               mime="application/pdf", key="dl_draft")
    elif rep.get("final_generated") and rep.get("fp") == W.work_fingerprint(pg):
        st.info(f"A final report was generated earlier (attempt {rep.get('attempt_id')}), but the file "
                "isn't held in this session any more. Press **Generate final report** again to "
                "download it — the attempt ID stays the same while your work is unchanged.")

    rec = rep.get("record")
    if rec and rep.get("final_generated"):
        if rec["ok"]:
            st.success("🗂 " + rec["message"])
        else:
            if ctx.identity.recorded:
                st.error("🗂 **Completion could not be recorded on the server.** " + rec["message"]
                         + " Your PDF is unaffected — download it and submit to your LMS.")
                if st.button("Retry recording", key="retry_rec"):
                    ok, msg = persist.record_completion_safe(ctx.store, ctx.game, ctx.identity.sid,
                                                             rep.get("attempt_id", ""),
                                                             rep.get("record_extra", {}))
                    rep["record"] = {"ok": ok, "message": msg}
                    st.rerun()
            else:
                st.caption("🗂 " + rec["message"])
    if rep.get("final_generated"):
        plans = need_both(ctx)
        if plans:
            excel_panel.render_export(ctx, export_plans(ctx, plans), plan_state(ctx, "level")["whole"],
                                      "xl_export_submit")
    st.caption("Plan cost in your report is the modelled annual operating cost of a plan, not an "
               "academic grade.")
    footer_nav(ctx)
