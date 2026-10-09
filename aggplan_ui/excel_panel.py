"""Excel hand-off panel: download the scenario workbook, upload the completed template, and
(later) export completed plans. Imported sheets are loaded into the SAME worksheet and graded
by the SAME checker as typed work — Excel never bypasses the learning requirements."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from aggplan import excel_io
from aggplan.worksheet import blank_worksheet

from .common import Ctx, SS, STRETCH

LEVEL_ICON = {"error": "❌ Error", "warning": "⚠️ Warning", "info": "ℹ️ Note"}


def template_bytes(ctx: Ctx, whole: bool) -> bytes:
    key = f"_xl_tpl_{ctx.h}_{whole}"
    if key not in SS:
        SS[key] = excel_io.build_template(ctx.scn, whole=whole, hybrid=True)
    return SS[key]


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
        st.download_button("⬇ Download Excel workbook for my scenario", template_bytes(ctx, whole),
                           file_name=f"Aggregate_Anxiety_scenario_{ctx.scn.seed}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"xl_dl_{kind}")
        up = st.file_uploader("Upload your completed workbook (.xlsx)", type=["xlsx"], key=f"xl_up_{kind}")
        if up is not None:
            res = excel_io.import_workbook(up.getvalue(), ctx.scn, whole=whole, kinds=(kind,))
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
    """Download completed plans (values) as Excel. `plans` is excel_io.export_workbook's dict."""
    if not plans:
        return
    data = excel_io.export_workbook(ctx.scn, plans, whole=whole)
    st.download_button("⬇ Download my completed plans (Excel)", data,
                       file_name=f"Aggregate_Anxiety_plans_{ctx.scn.seed}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=key)
