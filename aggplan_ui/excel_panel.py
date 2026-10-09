"""Excel hand-off panel: download the scenario workbook, upload the completed template, and
(later) export completed plans. Imported sheets are loaded into the SAME worksheet and graded
by the SAME checker as typed work — Excel never bypasses the learning requirements."""

from __future__ import annotations

import streamlit as st

from .common import Ctx, SS, STRETCH

LEVEL_ICON = {"error": "❌ Error", "warning": "⚠️ Warning", "info": "ℹ️ Note"}


def _xl():
    """Import the Excel layer on first use. It pulls in openpyxl (~270 ms and a chunk of
    memory); on a shared server most students never open Excel, and the app should not pay
    that on every cold start."""
    from aggplan import excel_io
    return excel_io


def _tpl_key(ctx: Ctx, whole: bool) -> str:
    return f"_xl_tpl_{ctx.h}_{whole}"


def template_bytes(ctx: Ctx, whole: bool) -> bytes:
    """Build (and remember) the student's workbook. Costs ~80 ms of CPU and tens of KB of
    session memory, so callers build it ONLY when the student asks — with ~30 students in one
    Streamlit process, doing it on every visit to a plan stage is pure waste for everyone who
    never opens Excel."""
    key = _tpl_key(ctx, whole)
    if key not in SS:
        for stale in [k for k in SS if k.startswith("_xl_tpl_") and k != key]:
            SS.pop(stale, None)          # keep at most one workbook per session
        SS[key] = _xl().build_template(ctx.scn, whole=whole, hybrid=True)
    return SS[key]


def template_download(ctx: Ctx, whole: bool, key: str, label: str) -> None:
    """Show a Prepare button first; the workbook is only built when it is clicked."""
    tkey = _tpl_key(ctx, whole)
    if tkey in SS:
        st.download_button(label, SS[tkey],
                           file_name=f"Aggregate_Anxiety_scenario_{ctx.scn.seed}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"dl_{key}")
        return
    if st.button("🧾 Prepare my Excel workbook", key=f"prep_{key}"):
        with st.spinner("Building your workbook…"):
            template_bytes(ctx, whole)
        st.rerun()
    st.caption("The workbook is built on request, so it only uses server time if you want it.")


def show_messages(res) -> None:
    for m in res.messages:
        where = f"{m.sheet}!{m.cell}" if m.cell else m.sheet
        txt = f"**{LEVEL_ICON.get(m.level, m.level)}** — {where}: {m.text}" if where else \
            f"**{LEVEL_ICON.get(m.level, m.level)}** — {m.text}"
        {"error": st.error, "warning": st.warning}.get(m.level, st.info)(txt)


def render(ctx: Ctx, kind: str, whole: bool, set_worksheet) -> None:
    with st.expander("📊 Work in Excel instead (download a template, then upload it)", expanded=False):
        st.markdown(
            "The workbook contains **your scenario** (units and assumptions, with named cells such "
            "as `LABOR` and `RATE`) and blank **Chase** and **Level** templates with exactly the "
            "same columns and rows as this worksheet — January is row 2, so your formulas are "
            "identical in Excel and here. Excel work is checked **exactly like** typed work.")
        template_download(ctx, whole, f"xl_{kind}", "⬇ Download Excel workbook for my scenario")
        up = st.file_uploader("Upload your completed workbook (.xlsx)", type=["xlsx"], key=f"xl_up_{kind}")
        if up is not None:
            res = _xl().import_workbook(up.getvalue(), ctx.scn, whole=whole, kinds=(kind,))
            show_messages(res)
            df = res.sheets.get(kind)
            if res.ok and df is not None:
                st.success(f"✅ The {kind} sheet can be loaded. Loading replaces what is in the "
                           "worksheet above; then press **Check my work**.")
                if st.button(f"Load the {kind} sheet into the worksheet", key=f"xl_load_{kind}"):
                    set_worksheet(ctx, kind, df)
                    st.rerun()
            elif not res.ok:
                st.caption("Fix the items marked ❌, save the workbook, and upload it again.")


def render_export(ctx: Ctx, plans: dict, whole: bool, key: str) -> None:
    """Download completed plans (values) as Excel. `plans` is excel_io.export_workbook's dict.

    Built only when asked: the workbook costs ~50 ms of CPU, and rebuilding it on every rerun
    of the compare and submit stages wasted that for every student on a shared server."""
    if not plans:
        return
    skey = f"_xl_exp_{key}_{ctx.h}"
    if skey in SS:
        st.download_button("⬇ Download my completed plans (Excel)", SS[skey],
                           file_name=f"Aggregate_Anxiety_plans_{ctx.scn.seed}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"dl_{skey}")
        return
    if st.button("🧾 Prepare my completed plans (Excel)", key=f"prep_{skey}"):
        with st.spinner("Building your workbook…"):
            for stale in [k for k in SS if k.startswith("_xl_exp_")]:
                SS.pop(stale, None)
            SS[skey] = _xl().export_workbook(ctx.scn, plans, whole=whole)
        st.rerun()
