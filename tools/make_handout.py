#!/usr/bin/env python
"""Regenerate the one-page student handout from the DEFAULT scenario parameters, so the
numbers on it can never drift from the app:   python tools/make_handout.py"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

from aggplan import MODEL_VERSION  # noqa: E402
from aggplan import text as T  # noqa: E402
from aggplan.scenario import resolve_scenario  # noqa: E402
from aggplan.workflow import GROUPS, STAGES  # noqa: E402

F = os.path.join(ROOT, "aggplan", "fonts")
pdfmetrics.registerFont(TTFont("DV", os.path.join(F, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DV-B", os.path.join(F, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV", boldItalic="DV-B")

S = resolve_scenario({}, 1)
body = ParagraphStyle("b", fontName="DV", fontSize=8.6, leading=11.2)
small = ParagraphStyle("s", parent=body, fontSize=7.6, leading=9.6, textColor=colors.HexColor("#333333"))
h1 = ParagraphStyle("h1", parent=body, fontName="DV-B", fontSize=16, leading=19, textColor=colors.HexColor("#1f3864"))
h2 = ParagraphStyle("h2", parent=body, fontName="DV-B", fontSize=9.5, leading=12, textColor=colors.HexColor("#1f3864"),
                    spaceBefore=6, spaceAfter=2)


def esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(path):
    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title="Aggregate Anxiety — student quick-start")
    p = S.params
    el = [Paragraph("Juicetification: Aggregate Anxiety", h1),
          Paragraph("Build a 12-month production plan for a juice bottler — student quick-start", body),
          Paragraph("Your mission", h2),
          Paragraph("You are the operations analyst at Juicetification Inc. Demand peaks in spring and summer. "
                    "Build a <b>chase</b> plan and a <b>level</b> plan yourself, compare them, recommend one in your "
                    "own words, then <b>download a PDF report and upload it to your LMS assignment</b>. "
                    "Everyone gets different numbers — the method is the same.", body)]
    rows = [[Paragraph("<b>5 steps in the sidebar</b>", body), Paragraph("<b>What you do</b>", body)]]
    what = {"understand": "Read the brief and frame the trade-off.",
            "prepare": "Pick the data and columns, choose formulas, work out capacity.",
            "build": "Fill the chase and level worksheets in the app — or in Excel, then upload.",
            "compare": "Read the metrics side by side; optional hybrid and advanced exercises.",
            "submit": "Write your recommendation and reflections; download the PDF."}
    for k, lab in GROUPS:
        rows.append([Paragraph(esc(lab), body), Paragraph(esc(what[k]), body)])
    t = Table(rows, colWidths=[2.2 * inch, 5.1 * inch])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde3ee")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [Paragraph("The roadmap", h2), t,
           Paragraph("Every stage shows <b>your task</b>, <b>what to do now</b>, your progress, and a "
                     "<b>Check my work</b> button. Feedback appears when you press it, so you attempt first. "
                     "If a stage is incomplete, the page tells you why. Your work is kept as you move around.", small)]
    ws = [
        "Type a number or a formula starting with <b>=</b>, e.g. <b>=E2/RATE</b> or <b>=MAX(0,C2+E2-D2-B2)</b>.",
        "<b>Row 1 holds the headings; January is row 2</b>; column A = Month, B = Forecast Demand — exactly as in the Excel template.",
        "Functions: MAX, MIN, SUM, ROUNDUP, ROUNDDOWN, ROUND, ABS. Powers use ^ . Names: LABOR, HIRE, HOLD, RATE, BEGIN, SAFETY …",
        "Status always has a symbol: ✔ correct · ✖ needs attention · ○ blank · ⚠ formula error (hover for the reason).",
        "Prefer Excel? Download the workbook for <i>your</i> scenario, fill it in, upload it; it is checked the same way.",
        "Enter the plan's total annual cost (sum of Total Monthly Cost) to finish each plan.",
    ]
    el += [Paragraph("Using the worksheet", h2)] + [Paragraph("• " + w, body) for w in ws]
    kf = [
        f"Capacity per worker = {p['bottles_per_hour']} /h × {p['hours_per_day']} h × {p['working_days']} d = {p['bottles_per_worker']:,}/month (you are paid for capacity even if you use less)",
        "Inventory and backlog are separate: physical stock is never negative; unfilled orders are backlog that carries forward, oldest first",
        "Ending Inventory = MAX(0, Begin Inv + Production − Begin Backlog − Demand)",
        "Backorders = MAX(0, Demand + Begin Backlog − Begin Inv − Production)",
        "Workers (whole) = ROUNDUP(Production ÷ capacity)   ·   Paid Labor Hours/Day = Workers × hours per shift",
        "Hires = MAX(0, this month − last month)   ·   Layoffs = MAX(0, last month − this month)",
        "Level rate = (Total demand + ending target − Beginning inventory) ÷ 12",
        "Safety stock is a <b>planning target</b>, not a hard floor: stock below it is still shipped and flagged",
        "Total Monthly Cost = Labor + Hiring + Layoff + Holding + Backorder",
    ]
    el += [Paragraph("Key formulas (defaults — your brief shows your numbers)", h2)] + [Paragraph("• " + k, body) for k in kf]
    cost = (f"Labor {T.usd(p['regular_labor_cost'])}/worker/mo · Hire {T.usd(p['hiring_cost'])} · Layoff "
            f"{T.usd(p['layoff_cost'])} · Holding {T.usd(p['holding_cost'], True)} · Backorder "
            f"{T.usd(p['backorder_cost'], True)} · Overtime {T.usd(p['overtime_cost'], True)} (≤{p['overtime_pct']:.0%}) · "
            f"Subcontract {T.usd(p['subcontract_cost'], True)} · Begin {p['beginning_inventory']:,.0f} · Start workforce {p['starting_workforce']:.0f}")
    el += [Paragraph("Default cost sheet (your instructor may change these)", h2), Paragraph(esc(cost), body),
           Paragraph("Stuck?", h2),
           Paragraph("Each wrong cell is explained. “I'm stuck” (if your instructor allows it) shows a worked solution and then "
                     "gives you a <b>replacement scenario</b> — different numbers — so you rebuild it yourself. "
                     "Help use is recorded neutrally in your report.", body),
           Paragraph("Submitting", h2),
           Paragraph("When every required activity is complete, press <b>Generate final report</b>, then <b>Download PDF for LMS "
                     "submission</b>. Download your report, review it, and upload the PDF to your LMS assignment. "
                     "A draft can be downloaded at any time but every page is marked DRAFT. The cost shown in your report is "
                     "<b>plan cost, not a grade</b>.", body),
           Spacer(1, 4),
           Paragraph(f"Run locally: streamlit run app.py · model {MODEL_VERSION}", small)]
    doc.build(el)


if __name__ == "__main__":
    out = os.path.join(ROOT, "Juicetification_Aggregate_Anxiety_Instructions.pdf")
    build(out)
    print("wrote", out)
