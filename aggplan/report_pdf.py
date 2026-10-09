"""PDF completion report for Juicetification: Aggregate Anxiety.

`build_pdf(snapshot) -> bytes` is a pure function of the snapshot produced by
`aggplan.report.build_snapshot`: no Streamlit, no network, no ambient process state, and the
only file access is reading the bundled DejaVu fonts in `aggplan/fonts/`.

Design notes
  * Portrait US Letter for the body, landscape US Letter for the appendix tables
    (two PageTemplates, switched with NextPageTemplate).
  * "Page X of Y" via a two-pass canvas; header/footer/DRAFT watermark are drawn by that
    canvas so every page of a draft is marked.
  * ALL snapshot text goes through `_esc` (sanitise -> XML-escape -> newline to <br/>) before
    it reaches a Paragraph, so student markup is displayed literally and never interpreted.
  * Characters the bundled font cannot show (CJK, emoji, right-to-left scripts that need
    shaping, ...) are replaced by a visible placeholder and the section carries one notice.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

__all__ = ["build_pdf", "DRAFT_BANNER"]

# --------------------------------------------------------------------------- #
# Constants (module level so tests can shrink the landscape frame to force splits)
# --------------------------------------------------------------------------- #
DRAFT_BANNER = "DRAFT — incomplete — do not submit"
PLACEHOLDER = "□"
UNSUPPORTED_NOTICE = ("Some characters in this response could not be displayed in the PDF; "
                      "the full original text is preserved in the submission record.")
FINAL_INSTRUCTION = "Download your report, review it, and upload the PDF to your LMS assignment."
DRAFT_INSTRUCTION = ("This report is incomplete and must not be submitted. Finish every required "
                     "activity, then download a new report.")
VERIFY_SENTENCE = ("Instructors can verify this report against the submission record using the "
                   "attempt ID and hash.")
NOT_COMPLETED = "Not completed — no verified plan yet"

PORT_LR = 0.75 * inch
PORT_TOP = 0.95 * inch
PORT_BOTTOM = 0.85 * inch
LAND_LR = 0.5 * inch
LAND_TOP = 0.85 * inch
LAND_BOTTOM = 0.75 * inch

INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#4B5563")
GRID = colors.HexColor("#B8BFC9")
HEAD_BG = colors.HexColor("#D9E1EC")
GROUP_BG = colors.HexColor("#ECEFF4")
ZEBRA = colors.HexColor("#F6F7F9")
GREEN = "#1B5E20"
RED = "#A31515"
AMBER = "#6B4E00"
NAVY = colors.HexColor("#1F3A5F")

# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONTS = {"DejaVu": "DejaVuSans.ttf", "DejaVu-Bold": "DejaVuSans-Bold.ttf",
          "DejaVu-Oblique": "DejaVuSans-Oblique.ttf",
          "DejaVu-BoldOblique": "DejaVuSans-BoldOblique.ttf", "DejaVuMono": "DejaVuSansMono.ttf"}
_CMAP: Optional[set] = None


def _register_fonts() -> None:
    global _CMAP
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        for name, fn in _FONTS.items():
            path = os.path.join(_FONT_DIR, fn)
            if not os.path.isfile(path):
                raise RuntimeError("Report font missing: %s (expected in aggplan/fonts)" % fn)
            pdfmetrics.registerFont(TTFont(name, path))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold",
                                      italic="DejaVu-Oblique", boldItalic="DejaVu-BoldOblique")
    if _CMAP is None:
        _CMAP = set(pdfmetrics.getFont("DejaVu").face.charToGlyph.keys())


# --------------------------------------------------------------------------- #
# Text sanitising / escaping
# --------------------------------------------------------------------------- #
def _is_rtl_or_complex(cp: int) -> bool:
    # Hebrew .. Arabic extended, Arabic presentation forms: need bidi/shaping we do not do.
    return (0x0590 <= cp <= 0x08FF) or (0xFB1D <= cp <= 0xFDFF) or (0xFE70 <= cp <= 0xFEFF)


def _clean(value) -> Tuple[str, bool]:
    """Return (display text, substituted?). Keeps newlines, strips control characters."""
    if value is None:
        return "", False
    s = value if isinstance(value, str) else str(value)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = unicodedata.normalize("NFC", s)
    _register_fonts()
    out: List[str] = []
    changed = False
    for ch in s:
        cp = ord(ch)
        if ch == "\n" or ch == " " or ch == " " or ch == "\x85":
            out.append("\n")
        elif ch == "\t":
            out.append("    ")
        elif ch == " ":
            out.append(" ")
        else:
            cat = unicodedata.category(ch)
            if cat in ("Cc", "Cf"):
                continue                      # control / invisible format characters
            if cat == "Zs":
                out.append(" " if cp == 0xA0 else " ")
            elif cp > 0xFFFF or cat in ("Cs", "Co", "Cn") or _is_rtl_or_complex(cp) \
                    or cp not in _CMAP:
                out.append(PLACEHOLDER)
                changed = True
            else:
                out.append(ch)
    return "".join(out), changed


class _Flag:
    """Collects whether any substitution happened in a section."""
    def __init__(self) -> None:
        self.hit = False


def _esc(value, flag: Optional[_Flag] = None, *, keep_spaces: bool = False) -> str:
    """Escape for Paragraph markup. Newlines -> <br/> (after escaping)."""
    text, changed = _clean(value)
    if changed and flag is not None:
        flag.hit = True
    lines = []
    for line in text.split("\n"):
        line = _xml_escape(line)
        if keep_spaces:
            line = re.sub(r"^ +", lambda m: " " * len(m.group(0)), line)
            line = re.sub(r" {2,}", lambda m: " " * (len(m.group(0)) - 1) + " ", line)
        lines.append(line)
    return "<br/>".join(lines)


# --------------------------------------------------------------------------- #
# Number formatting
# --------------------------------------------------------------------------- #
def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def money(v) -> str:
    f = round(_num(v))
    return "$%s" % format(int(f), ",")


def qty(v) -> str:
    return format(int(round(_num(v))), ",")


def dec2(v) -> str:
    f = round(_num(v), 2)
    if f == 0:
        return "0"
    return format(f, ",.2f").rstrip("0").rstrip(".")


def pct(v) -> str:
    return "%.1f%%" % _num(v)


def _col_fmt(name: str):
    if name == "Month":
        return str
    if name.endswith("Cost"):
        return money
    if name in ("Workers", "Paid Labor Hours/Day", "Hires", "Layoffs"):
        return dec2
    return qty


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
def _styles() -> Dict[str, ParagraphStyle]:
    base = dict(fontName="DejaVu", textColor=INK, splitLongWords=1, allowWidows=1)
    S = {}
    S["body"] = ParagraphStyle("body", fontSize=10, leading=13.5, spaceAfter=5, **base)
    S["small"] = ParagraphStyle("small", fontSize=8.5, leading=11, spaceAfter=3, **base)
    S["note"] = ParagraphStyle("note", fontSize=8.5, leading=11, spaceAfter=4,
                               **dict(base, textColor=MUTED, fontName="DejaVu-Oblique"))
    S["title"] = ParagraphStyle("title", fontSize=20, leading=24, spaceAfter=2,
                                **dict(base, fontName="DejaVu-Bold", textColor=NAVY))
    S["subtitle"] = ParagraphStyle("subtitle", fontSize=11, leading=14, spaceAfter=8,
                                   **dict(base, textColor=MUTED))
    S["h1"] = ParagraphStyle("h1", fontSize=13.5, leading=17, spaceBefore=14, spaceAfter=6,
                             **dict(base, fontName="DejaVu-Bold", textColor=NAVY))
    S["h2"] = ParagraphStyle("h2", fontSize=11, leading=14, spaceBefore=9, spaceAfter=4,
                             **dict(base, fontName="DejaVu-Bold"))
    S["prompt"] = ParagraphStyle("prompt", fontSize=9.5, leading=12.5, spaceBefore=8, spaceAfter=4,
                                 **dict(base, fontName="DejaVu-Bold"))
    S["student"] = ParagraphStyle("student", fontSize=10, leading=13.5, spaceBefore=6, spaceAfter=8,
                                  leftIndent=8, rightIndent=8, borderPadding=(4, 4, 4, 4),
                                  backColor=colors.HexColor("#F7F8FA"), borderColor=GRID,
                                  borderWidth=0.5, **base)
    S["cell"] = ParagraphStyle("cell", fontSize=8.5, leading=10.5, **base)
    S["cell_r"] = ParagraphStyle("cell_r", parent=S["cell"], alignment=TA_RIGHT)
    S["cell_c"] = ParagraphStyle("cell_c", parent=S["cell"], alignment=TA_CENTER)
    S["cell_b"] = ParagraphStyle("cell_b", parent=S["cell"], fontName="DejaVu-Bold")
    S["cell_br"] = ParagraphStyle("cell_br", parent=S["cell_b"], alignment=TA_RIGHT)
    S["hdr"] = ParagraphStyle("hdr", parent=S["cell"], fontName="DejaVu-Bold")
    S["hdr_r"] = ParagraphStyle("hdr_r", parent=S["hdr"], alignment=TA_RIGHT)
    S["mono"] = ParagraphStyle("mono", parent=S["cell"], fontName="DejaVuMono", fontSize=8,
                               leading=10.5)
    S["a_cell"] = ParagraphStyle("a_cell", fontSize=7, leading=8.6, alignment=TA_RIGHT, **base)
    S["a_cell_l"] = ParagraphStyle("a_cell_l", parent=S["a_cell"], alignment=TA_LEFT)
    S["a_hdr"] = ParagraphStyle("a_hdr", parent=S["a_cell"], fontName="DejaVu-Bold",
                                alignment=TA_CENTER)
    S["a_tot"] = ParagraphStyle("a_tot", parent=S["a_cell"], fontName="DejaVu-Bold")
    S["a_tot_l"] = ParagraphStyle("a_tot_l", parent=S["a_tot"], alignment=TA_LEFT)
    S["status_big"] = ParagraphStyle("status_big", fontSize=16, leading=20, alignment=TA_CENTER,
                                     **dict(base, fontName="DejaVu-Bold", textColor=colors.white))
    return S


ST: Dict[str, ParagraphStyle] = {}


def _P(markup: str, style: str = "body") -> Paragraph:
    return Paragraph(markup, ST[style])


def _PE(text, style: str = "cell", flag: Optional[_Flag] = None) -> Paragraph:
    return Paragraph(_esc(text, flag), ST[style])


def _student_blocks(text, flag: _Flag) -> List:
    """Verbatim student text as one Paragraph per blank-line-separated block."""
    cleaned, changed = _clean(text)
    if changed:
        flag.hit = True
    if not cleaned.strip():
        return [_P("<i>(no response)</i>", "note")]
    blocks, cur = [], []
    for line in cleaned.split("\n"):
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        out.append(Paragraph("<br/>".join(_esc(l, None, keep_spaces=True) for l in b), ST["student"]))
    return out


def _notice(flag: _Flag) -> List:
    return [_P(_xml_escape(UNSUPPORTED_NOTICE), "note")] if flag.hit else []


# --------------------------------------------------------------------------- #
# Table helpers
# --------------------------------------------------------------------------- #
def _tbl(rows: List[List], widths: Sequence[float], *, header: bool = True, zebra: bool = True,
         extra: Sequence = (), pad: float = 3.0) -> Table:
    t = Table(rows, colWidths=list(widths), repeatRows=1 if header else 0)
    cmds = [("GRID", (0, 0), (-1, -1), 0.4, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), pad + 1), ("RIGHTPADDING", (0, 0), (-1, -1), pad + 1),
            ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), HEAD_BG))
    if zebra:
        cmds.append(("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, ZEBRA]))
    cmds.extend(extra)
    t.setStyle(TableStyle(cmds))
    return t


def _kv_table(pairs: List[Tuple[str, str]], width: float, label_w: float = 1.7 * inch) -> Table:
    """pairs: (plain label, ALREADY-ESCAPED markup)."""
    rows = [[_P(_xml_escape(k), "cell_b"), _P(v, "cell")] for k, v in pairs]
    t = _tbl(rows, [label_w, width - label_w], header=False, zebra=False,
             extra=[("BACKGROUND", (0, 0), (0, -1), GROUP_BG)])
    return t


def _status_markup(label: str, kind: str) -> str:
    """Text + symbol + colour; never colour alone."""
    sym, col = {"ok": ("✔", GREEN), "bad": ("✖", RED), "none": ("○", AMBER)}[kind]
    return '<font color="%s"><b>%s %s</b></font>' % (col, sym, _xml_escape(label))


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def _sec_header(snap: dict, final: bool, W: float) -> List:
    flag = _Flag()
    stu = snap.get("student") or {}
    att = snap.get("attempt") or {}
    asg = snap.get("assignment") or {}
    out: List = [_P("Aggregate Anxiety — Completion Report", "title"),
                 _PE(((snap.get("app") or {}).get("name") or ""), "subtitle")]
    # Status banner (text + symbol + fill; the fill is dark so it also reads in grayscale)
    label = "STATUS: ✔ FINAL" if final else "STATUS: ✖ DRAFT — NOT FOR SUBMISSION"
    bt = Table([[_P(_xml_escape(label), "status_big")]], colWidths=[W])
    bt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1),
                             colors.HexColor("#14532D") if final else colors.HexColor("#8B1A1A")),
                            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    out += [bt, Spacer(1, 7)]
    out.append(_P("<b>%s</b>" % _xml_escape(FINAL_INSTRUCTION if final else DRAFT_INSTRUCTION), "body"))
    if not final:
        missing = snap.get("missing") or []
        items = [_P("<b>Missing requirements</b>", "body")]
        if missing:
            for m in missing:
                items.append(Paragraph("✖ " + _esc(m), ParagraphStyle(
                    "miss", parent=ST["small"], leftIndent=12, firstLineIndent=-12, spaceAfter=2)))
        else:
            items.append(_P("No individual requirement is listed, but the report was generated as "
                            "a draft.", "small"))
        mt = Table([[items]], colWidths=[W])
        mt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#8B1A1A")),
                                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDF3F3")),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        out += [mt, Spacer(1, 6)]
    when_label = "Completed" if final else "Generated"
    gen = _esc(att.get("generated", ""), flag)
    utc = _esc(att.get("generated_utc", ""), flag)
    tz = _esc(att.get("tz", ""), flag)
    when = gen + (" &nbsp;|&nbsp; " + utc if utc else "") + \
        (" &nbsp;|&nbsp; time zone setting: " + tz if tz else "")
    pairs = [("Student name", _esc(stu.get("name", ""), flag) or "<i>(not entered)</i>"),
             ("Course section", _esc(stu.get("section", ""), flag) or "<i>(not entered)</i>"),
             ("Assignment", _esc(asg.get("name", ""), flag)),
             (when_label, when),
             ("Identity / mode", _esc(stu.get("identity", ""), flag))]
    out.append(_kv_table(pairs, W))
    out += _notice(flag)
    return out


def _sec_scenario(snap: dict, W: float) -> List:
    sc = snap.get("scenario") or {}
    att = snap.get("attempt") or {}
    app = snap.get("app") or {}
    out: List = [_P("1. Attempt and scenario", "h1")]
    pairs = [("Attempt ID", _esc(att.get("id", ""))),
             ("Scenario seed", _esc(sc.get("seed", "")))]
    if _num(sc.get("variant")) > 0:
        pairs.append(("Scenario variant", _esc(sc.get("variant"))))
        pairs.append(("Base seed", _esc(sc.get("base_seed"))))
    pairs += [("Model version", _esc(app.get("model_version", ""))),
              ("Scenario preset", _esc(sc.get("preset_label") or sc.get("preset") or "")),
              ("Scenario config hash", '<font name="DejaVuMono">%s</font>' % _esc(sc.get("config_hash", "")))]
    out.append(_kv_table(pairs, W))
    out.append(_P("Scenario parameters", "h2"))
    rows = [[_P("Parameter", "hdr"), _P("Value", "hdr_r"), _P("Unit", "hdr")]]
    for r in sc.get("params") or []:
        r = list(r) + ["", "", ""]
        rows.append([_PE(r[0], "cell"), _PE(r[1], "cell_r"), _PE(r[2], "cell")])
    out.append(_tbl(rows, [3.7 * inch, 1.2 * inch, W - 4.9 * inch]))
    out.append(_P("12-month demand forecast", "h2"))
    dem = sc.get("demand") or []
    half = (len(dem) + 1) // 2
    hdr = [_P("Month", "hdr"), _P("Demand (bottles)", "hdr_r")] * 2
    rows = [hdr]
    for i in range(half):
        row = []
        for j in (i, i + half):
            if j < len(dem):
                row += [_PE(dem[j][0], "cell"), _P(qty(dem[j][1]), "cell_r")]
            else:
                row += ["", ""]
        rows.append(row)
    total = sum(_num(d[1]) for d in dem)
    rows.append([_P("Total demand", "cell_b"), _P(qty(total), "cell_br"), "", ""])
    cw = [W * 0.28, W * 0.22, W * 0.28, W * 0.22]
    out.append(_tbl(rows, cw, extra=[("BACKGROUND", (0, -1), (-1, -1), GROUP_BG),
                                     ("SPAN", (1, -1), (1, -1))]))
    if sc.get("notices"):
        out.append(_P("Scenario notices", "h2"))
        for n in sc["notices"]:
            out.append(Paragraph("• " + _esc(n), ParagraphStyle(
                "ntc", parent=ST["small"], leftIndent=10, firstLineIndent=-10)))
    out.append(Spacer(1, 4))
    out.append(_PE(sc.get("safety_note") or "Safety stock is a planning target, not a hard floor.",
                   "note"))
    out.append(_PE(snap.get("plan_cost_note") or
                   "Plan cost is the modelled annual operating cost of a plan. It is not an "
                   "academic grade.", "note"))
    return out


def _activity_state(a: dict) -> Tuple[str, str]:
    if a.get("complete"):
        return "Complete", "ok"
    d = str(a.get("detail") or "").strip().lower()
    if d.startswith("not started") or d.startswith("not attempted"):
        return "Not attempted", "none"
    return "Incomplete", "bad"


def _sec_activities(snap: dict, W: float) -> List:
    out: List = [_P("2. Completion status of activities", "h1")]
    rows = [[_P("Activity", "hdr"), _P("Required?", "hdr"), _P("Status", "hdr"),
             _P("Detail", "hdr")]]
    for a in snap.get("activities") or []:
        label, kind = _activity_state(a)
        rows.append([_PE(a.get("label", "")), _P("Yes" if a.get("required") else "Optional", "cell"),
                     _P(_status_markup(label, kind), "cell"), _PE(a.get("detail", ""))])
    out.append(_tbl(rows, [2.3 * inch, 0.8 * inch, 1.2 * inch, W - 4.3 * inch]))
    return out


def _plan_rows(snap: dict) -> List[Tuple]:
    """Comparison rows: ('group', title) or (label, fn(plan)->markup/str/list, kind)."""
    def S(key, f):
        return lambda p: f(p["summary"].get(key))

    def txt(s):
        return _esc(s)

    def settings(key):
        return lambda p: txt((p.get("settings") or {}).get(key, ""))

    def months_below(p):
        if _num((p.get("policy") or {}).get("safety_target")) <= 0:
            return "n/a (no safety-stock target)"
        return qty(p["summary"].get("Months below safety stock"))

    rows = [
        ("group", "Plan settings"),
        ("Worker model", settings("Worker model")),
        ("Inventory policy", settings("Inventory policy")),
        ("Safety-stock target", settings("Safety-stock target")),
        ("Terminal requirement", settings("Terminal requirement")),
        ("group", "Plan cost (US dollars; not a grade)"),
        ("Regular labor cost", S("Total regular labor cost", money)),
        ("Hiring cost", S("Total hiring cost", money)),
        ("Layoff cost", S("Total layoff cost", money)),
        ("Overtime cost", S("Total overtime cost", money)),
        ("Subcontract cost", S("Total subcontract cost", money)),
        ("Holding cost", S("Total holding cost", money)),
        ("Backorder cost", S("Total backorder cost", money)),
        ("Disposal cost", S("Total disposal cost", money)),
        ("bold:Total plan cost", S("Total cost", money)),
        ("group", "Workforce"),
        ("Lowest workforce (workers)", S("Lowest workforce", dec2)),
        ("Highest workforce (workers)", S("Highest workforce", dec2)),
        ("Total hires", S("Total hires", dec2)),
        ("Total layoffs", S("Total layoffs", dec2)),
        ("Number of workforce changes (months)", S("Number of workforce changes", qty)),
        ("group", "Inventory and backlog (bottles)"),
        ("Highest inventory", S("Highest inventory", qty)),
        ("Ending inventory (December)", S("Ending inventory", qty)),
        ("Highest backlog", S("Highest backlog", qty)),
        ("Ending backlog (December)", S("Ending backlog", qty)),
        ("Backlog bottle-months", S("Backlog bottle-months", qty)),
        ("group", "Service measures"),
        ("On-time fulfillment", S("On-time fulfillment", pct)),
        ("Months with shortages", S("Months with shortages", qty)),
        ("Months ending with backlog", S("Months ending with backlog", qty)),
        ("Demand fulfilled by year end", S("Demand fulfilled by year end", pct)),
        ("group", "Capacity and safety stock"),
        ("Idle capacity (bottles)", S("Idle capacity (bottles)", qty)),
        ("Months below safety stock", months_below),
    ]
    return rows


def _check_list(checks: List[dict]) -> List:
    out = []
    for c in checks or []:
        ok = bool(c.get("ok"))
        mk = _status_markup("Met" if ok else "Not met", "ok" if ok else "bad")
        out.append(Paragraph("%s &nbsp;<b>%s</b><br/>%s" % (mk, _esc(c.get("label", "")),
                                                           _esc(c.get("detail", ""))),
                             ParagraphStyle("chk", parent=ST["cell"], spaceAfter=3)))
    return out or [_P("—", "cell")]


def _overall_markup(plan: dict) -> str:
    failed = [c.get("label", "") for c in plan.get("checks") or [] if not c.get("ok")]
    if plan.get("feasible") and not failed:
        return _status_markup("Meets all requirements", "ok")
    return _status_markup("Does NOT meet: " + "; ".join(failed), "bad") if failed else \
        _status_markup("Does NOT meet all requirements", "bad")


def _sec_compare(snap: dict, W: float) -> List:
    plans = snap.get("plans") or {}
    chase, level = plans.get("chase"), plans.get("level")
    out: List = [_P("3. Chase vs. level plan comparison", "h1")]
    cw = [2.45 * inch, (W - 2.45 * inch) / 2, (W - 2.45 * inch) / 2]
    rows = [[_P("Measure", "hdr"), _P("Chase plan", "hdr"), _P("Level plan", "hdr")]]
    extra = []

    def cellfor(plan, fn, bold=False):
        if plan is None:
            return _P("—", "cell")
        v = fn(plan)
        if isinstance(v, list):
            return v
        if "<" not in v and "&" not in v:
            v = _xml_escape(v)
        return _P(v, "cell_b" if bold else "cell")

    # status row
    def stat(plan):
        return _P(_xml_escape(NOT_COMPLETED), "cell_b") if plan is None else \
            _P(_status_markup("Verified plan", "ok"), "cell")
    rows.append([_P("Plan status", "cell_b"), stat(chase), stat(level)])
    both_missing = chase is None and level is None
    for r in ([] if both_missing else _plan_rows(snap)):
        if r[0] == "group":
            rows.append([_P(_xml_escape(r[1]), "cell_b"), "", ""])
            i = len(rows) - 1
            extra += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), GROUP_BG),
                      ("NOSPLIT", (0, i), (-1, i + 1))]
            continue
        label, fn = r
        bold = label.startswith("bold:")
        label = label.replace("bold:", "")
        rows.append([_P(_xml_escape(label), "cell_b" if bold else "cell"),
                     cellfor(chase, fn, bold), cellfor(level, fn, bold)])
        if bold:
            extra.append(("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.9, INK))
    # feasibility
    if both_missing:
        out.append(_tbl(rows, cw, extra=extra, zebra=False))
        return out + _compare_tail(snap)
    rows.append([_P("Feasibility", "cell_b"), "", ""])
    i = len(rows) - 1
    extra += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), GROUP_BG),
              ("NOSPLIT", (0, i), (-1, i + 1))]
    rows.append([_P("Requirement checks", "cell"),
                 _P("—", "cell") if chase is None else _check_list(chase.get("checks")),
                 _P("—", "cell") if level is None else _check_list(level.get("checks"))])
    rows.append([_P("Overall", "cell_b"),
                 _P("—", "cell") if chase is None else _P(_overall_markup(chase), "cell"),
                 _P("—", "cell") if level is None else _P(_overall_markup(level), "cell")])
    out.append(_tbl(rows, cw, extra=extra, zebra=False))
    return out + _compare_tail(snap)


def _compare_tail(snap: dict) -> List:
    W = 7.0 * inch
    out: List = []
    notes = snap.get("comparison_notes") or []
    if notes:
        out.append(_P("Assumptions that differ between the plans", "h2"))
        for n in notes:
            out.append(Paragraph("• " + _esc(n), ParagraphStyle(
                "cn", parent=ST["small"], leftIndent=10, firstLineIndent=-10)))
    defs = snap.get("metric_definitions") or {}
    if defs:
        out.append(_P("Definitions of the measures", "h2"))
        drows = [[_P("Measure", "hdr"), _P("Definition", "hdr")]]
        for k, v in defs.items():
            drows.append([_PE(k, "cell_b"), _PE(v, "cell")])
        out.append(_tbl(drows, [2.1 * inch, W - 2.1 * inch]))
    return out


def _sec_strategy(snap: dict, W: float) -> List:
    st = snap.get("strategy") or {}
    out: List = [_P("4. Selected strategy and written justification", "h1")]
    sel = st.get("selected")
    flag = _Flag()
    out.append(_P("<b>Selected strategy:</b> %s" % (_esc(sel, flag) if sel else "<i>not selected</i>"),
                  "body"))
    out.append(_P("Justification (student's exact words)", "h2"))
    out += _student_blocks(st.get("justification", ""), flag)
    out.append(_P("Plan comparison answers", "h2"))
    for qa in snap.get("compare_answers") or []:
        out.append(_PE(qa.get("prompt", ""), "prompt"))
        out += _student_blocks(qa.get("response", ""), flag)
    tn = snap.get("tradeoff_note") or ""
    if tn.strip():
        out.append(_P("Additional trade-off note (student's exact words)", "prompt"))
        out += _student_blocks(tn, flag)
    out += _notice(flag)
    return out


def _sec_reflections(snap: dict, W: float) -> List:
    out: List = [_P("5. Reflection responses", "h1")]
    flag = _Flag()
    for r in snap.get("reflections") or []:
        out.append(_PE(r.get("prompt", ""), "prompt"))
        out += _student_blocks(r.get("response", ""), flag)
    out += _notice(flag)
    return out


def _hyb_metrics(d: dict) -> List[Tuple[str, str]]:
    s = d.get("summary") or {}
    return [("Total plan cost", money(s.get("Total cost"))),
            ("On-time fulfillment", pct(s.get("On-time fulfillment"))),
            ("Ending backlog (bottles)", qty(s.get("Ending backlog"))),
            ("Ending inventory (bottles)", qty(s.get("Ending inventory"))),
            ("Overtime / subcontract bottles",
             "%s / %s" % (qty(s.get("Total overtime bottles")), qty(s.get("Total subcontracted bottles")))),
            ("Planning mode", d.get("mode", "")),
            ("Worker model", d.get("worker_model", "")),
            ("Overtime enabled", "Yes" if d.get("overtime_enabled") else "No"),
            ("Subcontracting enabled", "Yes" if d.get("subcontract_enabled") else "No"),
            ("Maintains safety stock", "Yes" if d.get("safety_stock") else "No")]


def _hyb_decisions(d: dict, W: float) -> Table:
    cols = ["Month", "Workers", "Regular Production", "Overtime Production", "Subcontract Production"]
    rows = [[_P(_xml_escape(c), "hdr" if i == 0 else "hdr_r") for i, c in enumerate(cols)]]
    for m in d.get("monthly") or []:
        rows.append([_PE(m.get("Month", ""), "cell")] +
                    [_P(_col_fmt(c)(m.get(c)), "cell_r") for c in cols[1:]])
    return _tbl(rows, [W * 0.24] + [W * 0.19] * 4)


def _sec_hybrid(snap: dict, W: float) -> List:
    h = snap.get("hybrid")
    unc = snap.get("uncertainty")
    out: List = [_P("6. Hybrid challenge", "h1")]
    if h is None:
        out.append(_P("<b>Not attempted.</b>", "body"))
    else:
        out.append(_P("<b>Number of attempts:</b> %s" % _esc(h.get("attempts", 0)), "body"))
        bf, li = h.get("best_feasible"), h.get("lowest_cost_infeasible")
        # --- best feasible
        out.append(_P("Best feasible plan", "h2"))
        if bf is None:
            out.append(_P(_status_markup("No feasible plan was found", "bad") +
                          " — none of the attempts met every requirement.", "body"))
        else:
            out.append(_P(_status_markup("Meets all requirements", "ok"), "body"))
            pairs = [(k, _esc(v)) for k, v in _hyb_metrics(bf)]
            out.append(_kv_table(pairs, W, 2.4 * inch))
            out.append(_P("Requirement checks", "prompt"))
            out += _check_list(bf.get("checks"))
            if bf.get("automatic_assistance"):
                out.append(_P("Automatic-assistance notes", "prompt"))
                for n in bf["automatic_assistance"]:
                    out.append(Paragraph("• " + _esc(n), ParagraphStyle(
                        "aa", parent=ST["small"], leftIndent=10, firstLineIndent=-10)))
            out.append(_P("Monthly decisions", "prompt"))
            out.append(_hyb_decisions(bf, W))
        # --- lowest-cost infeasible
        out.append(Spacer(1, 6))
        out.append(_P("Lower-cost attempt that does NOT meet requirements", "h2"))
        if li is None:
            out.append(_P("No attempt that fails the requirements is on record.", "body"))
        else:
            out.append(_P(_status_markup("Does NOT meet requirements — not a valid plan", "bad"), "body"))
            s = li.get("summary") or {}
            pairs = [("Total plan cost", _esc(money(s.get("Total cost")))),
                     ("On-time fulfillment", _esc(pct(s.get("On-time fulfillment")))),
                     ("Requirements not met",
                      "<br/>".join("✖ " + _esc(x) for x in li.get("failed_requirements") or []) or "—")]
            out.append(_kv_table(pairs, W, 2.4 * inch))
    if unc:
        out.append(_P("Forecast-error evaluation", "h2"))
        rows = [[_P("Measure", "hdr"), _P("Value", "hdr_r")]]
        data = [("Simulated demand scenarios", qty(unc.get("n_draws"))),
                ("Mean total cost", money(unc.get("mean_cost"))),
                ("10th percentile cost", money(unc.get("p10_cost"))),
                ("90th percentile cost", money(unc.get("p90_cost"))),
                ("Mean on-time fulfillment", pct(unc.get("mean_on_time"))),
                ("10th percentile on-time fulfillment", pct(unc.get("p10_on_time"))),
                ("Probability of any shortage", pct(_num(unc.get("prob_any_shortage")) * 100))]
        if unc.get("prob_meets_service") is not None:
            data.append(("Probability of meeting the service requirement",
                         pct(_num(unc.get("prob_meets_service")) * 100)))
        for k, v in data:
            rows.append([_PE(k), _P(_xml_escape(v), "cell_r")])
        out.append(_tbl(rows, [W * 0.65, W * 0.35]))
    return out


def _sec_usage(snap: dict, W: float) -> List:
    out: List = [_P("7. Attempts and help usage", "h1"),
                 _P("Counts recorded for this attempt, shown for information.", "small")]
    rows = [[_P("Activity", "hdr"), _P("Checks run", "hdr_r"), _P("Scenario replacements", "hdr_r"),
             _P("Worked solution opened", "hdr_r")]]
    for u in snap.get("usage") or []:
        def v(x):
            return _P("—" if x is None else qty(x), "cell_r")
        rows.append([_PE(u.get("activity", "")), v(u.get("checks_run")),
                     v(u.get("scenario_replacements")), v(u.get("worked_solution_opened"))])
    out.append(_tbl(rows, [W * 0.37, W * 0.18, W * 0.23, W * 0.22], zebra=False))
    return out


def _sec_integrity(snap: dict, W: float) -> List:
    integ = snap.get("integrity") or {}
    att = snap.get("attempt") or {}
    out: List = [_P("8. Report integrity", "h1")]
    pairs = [("Attempt ID", _esc(att.get("id", ""))),
             ("Snapshot hash (SHA-256)",
              '<font name="DejaVuMono">%s</font>' % _esc(integ.get("snapshot_hash", "")))]
    if integ.get("seal"):
        pairs.append(("Seal", '<font name="DejaVuMono">%s</font>' % _esc(integ.get("seal"))))
    out.append(KeepTogether([_kv_table(pairs, W), Spacer(1, 4), _P(_xml_escape(VERIFY_SENTENCE), "small")]))
    return out


# --------------------------------------------------------------------------- #
# Appendix
# --------------------------------------------------------------------------- #
def _auto_widths(cols: List[str], body: List[List[str]], total_row: Optional[List[str]],
                 avail: float, size: float) -> Tuple[List[float], bool]:
    """Column widths from content at `size` pt; second value says whether they fit unscaled."""
    pad = 5.0
    mins = []
    for j, c in enumerate(cols):
        w = max([stringWidth(x, "DejaVu-Bold", size) for x in c.split()] or [10])
        w = max([w] + [stringWidth(r[j], "DejaVu", size) for r in body])
        if total_row:
            w = max(w, stringWidth(total_row[j], "DejaVu-Bold", size))
        mins.append(w + pad)
    tot = sum(mins)
    if tot >= avail:
        return [m * avail / tot for m in mins], tot <= avail
    return [m + (avail - tot) * m / tot for m in mins], True


_SUM_COLS = {"Forecast Demand", "Regular Production", "Overtime Production", "Subcontract Production",
             "Hires", "Layoffs", "Regular Labor Cost", "Hiring Cost", "Layoff Cost", "Overtime Cost",
             "Subcontract Cost", "Holding Cost", "Backorder Cost", "Total Monthly Cost"}


def _monthly_table(monthly: List[dict], cols: List[str], avail: float,
                   labels: Optional[Dict[str, str]] = None) -> Table:
    labels = labels or {}
    heads = [labels.get(c, c) for c in cols]
    body, totals = [], []
    for m in monthly:
        body.append([str(m.get("Month", "")) if c == "Month" else _col_fmt(c)(m.get(c)) for c in cols])
    for c in cols:
        if c == "Month":
            totals.append("Total")
        elif c in _SUM_COLS:
            totals.append(_col_fmt(c)(sum(_num(m.get(c)) for m in monthly)))
        else:
            totals.append("")
    clean_body = [[_clean(x)[0] for x in r] for r in body]
    size = 8.0
    widths, fits = _auto_widths(heads, clean_body, totals, avail, size)
    if not fits:                       # wide numbers: fall back to the 7 pt minimum
        size = 7.0
        widths, _ = _auto_widths(heads, clean_body, totals, avail, size)
    lead = size * 1.22
    sty = {k: ParagraphStyle(k + "_s", parent=ST[k], fontSize=size, leading=lead)
           for k in ("a_hdr", "a_cell", "a_cell_l", "a_tot", "a_tot_l")}
    rows = [[Paragraph(_xml_escape(c), sty["a_hdr"]) for c in heads]]
    for r in body:
        rows.append([Paragraph(_esc(r[0]), sty["a_cell_l"])] +
                    [Paragraph(_xml_escape(_clean(x)[0]), sty["a_cell"]) for x in r[1:]])
    rows.append([Paragraph(_xml_escape(totals[0]), sty["a_tot_l"])] +
                [Paragraph(_xml_escape(x), sty["a_tot"]) for x in totals[1:]])
    return _tbl(rows, widths, pad=1.5, extra=[
        ("BACKGROUND", (0, -1), (-1, -1), HEAD_BG), ("LINEABOVE", (0, -1), (-1, -1), 1.0, INK),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE")])


_HYBRID_COLS = ["Month", "Forecast Demand", "Workers", "Regular Production", "Overtime Production",
                "Subcontract Production", "Beginning Inventory", "Beginning Backlog",
                "Ending Inventory", "Backorders", "Regular Labor Cost", "Hiring Cost", "Layoff Cost",
                "Overtime Cost", "Subcontract Cost", "Holding Cost", "Backorder Cost",
                "Total Monthly Cost"]


_HYBRID_LABELS = {
    "Forecast Demand": "Demand", "Regular Production": "Regular Prod.",
    "Overtime Production": "Overtime Prod.", "Subcontract Production": "Subcontr. Prod.",
    "Beginning Inventory": "Begin. Inv.", "Beginning Backlog": "Begin. Backlog",
    "Ending Inventory": "End Inv.", "Subcontract Cost": "Subcontr. Cost",
}


def _appendix_flowables(snap: dict, avail: float) -> List:
    from .worksheet import WS_COLS
    plans = snap.get("plans") or {}
    out: List = []
    first = True
    for key, title in (("chase", "Appendix A. Chase plan — completed monthly worksheet"),
                       ("level", "Appendix B. Level plan — completed monthly worksheet")):
        if not first:
            out.append(PageBreak())
        first = False
        p = plans.get(key)
        out.append(_P(_xml_escape(title), "h1"))
        if p is None:
            out.append(_P(_xml_escape(NOT_COMPLETED) + ".", "body"))
            continue
        out.append(_PE("Worker model: %s. Inventory policy: %s." % (
            (p.get("settings") or {}).get("Worker model", ""),
            (p.get("settings") or {}).get("Inventory policy", "")), "small"))
        out.append(_monthly_table(p.get("monthly") or [], list(WS_COLS), avail))
    h = snap.get("hybrid")
    bf = (h or {}).get("best_feasible")
    if bf:
        out.append(PageBreak())
        out.append(_P("Appendix C. Hybrid challenge — best feasible plan, monthly detail", "h1"))
        out.append(_monthly_table(bf.get("monthly") or [], list(_HYBRID_COLS), avail, _HYBRID_LABELS))
        out.append(_P("Abbreviations: Prod. = production; Inv. = inventory; Begin. = beginning; "
                      "Subcontr. = subcontract.", "note"))
    else:
        out.append(CondPageBreak(2.2 * inch))
        out.append(_P("Appendix C. Hybrid challenge — best feasible plan, monthly detail", "h1"))
        out.append(_P("Not attempted." if h is None else "No feasible hybrid plan was found.", "body"))
    return out


# --------------------------------------------------------------------------- #
# Canvas (header / footer / watermark / Page X of Y)
# --------------------------------------------------------------------------- #
def _make_canvas(final: bool, attempt_id: str, version: str):
    aid, ver = _clean(attempt_id)[0], _clean(version)[0]

    class _NumberedCanvas(Canvas):
        def __init__(self, *a, **k):
            Canvas.__init__(self, *a, **k)
            self._saved: List[dict] = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                self._decorate(n)
                Canvas.showPage(self)
            Canvas.save(self)

        def _decorate(self, total: int):
            W, H = self._pagesize
            land = W > H
            lr = LAND_LR if land else PORT_LR
            right = W - lr
            self.saveState()
            # watermark on top, translucent so content stays readable
            if not final:
                self.saveState()
                self.setFillColor(colors.Color(0.35, 0.35, 0.35))
                self.setFillAlpha(0.13)
                self.setFont("DejaVu-Bold", 150 if not land else 130)
                self.translate(W / 2, H / 2)
                self.rotate(55 if not land else 28)
                self.drawCentredString(0, -45, "DRAFT")
                self.restoreState()
            # header
            self.setFillColor(INK)
            self.setFont("DejaVu", 8.5)
            self.drawString(lr, H - 30, "Juicetification: Aggregate Anxiety · Completion Report")
            if final:
                self.setFont("DejaVu-Bold", 9)
                self.drawRightString(right, H - 30, "✔ FINAL")
                self.setStrokeColor(GRID)
                self.setLineWidth(0.6)
                self.line(lr, H - 38, right, H - 38)
            else:
                self.setFillColor(colors.HexColor("#8B1A1A"))
                self.rect(lr, H - 56, right - lr, 17, stroke=0, fill=1)
                self.setFillColor(colors.white)
                self.setFont("DejaVu-Bold", 9.5)
                self.drawCentredString((lr + right) / 2, H - 51, DRAFT_BANNER)
            # footer
            self.setFillColor(MUTED)
            self.setStrokeColor(GRID)
            self.setLineWidth(0.6)
            self.line(lr, 46, right, 46)
            self.setFont("DejaVu", 8)
            self.drawString(lr, 35, "Attempt ID: %s" % aid)
            self.drawRightString(right, 35, "Page %d of %d" % (self._pageNumber, total))
            self.drawString(lr, 24, "Aggregate Anxiety — model %s" % ver)
            self.setFont("DejaVu-Bold", 8)
            self.drawRightString(right, 24, "FINAL" if final else "DRAFT — do not submit")
            self.restoreState()

    return _NumberedCanvas


_HEADING_SPACE = {"h1": 1.4 * inch, "h2": 1.2 * inch, "prompt": 0.9 * inch}


def _guard_headings(story: List) -> List:
    """Avoid headings stranded at the bottom of a page without forcing whole tables to move."""
    out: List = []
    for f in story:
        name = getattr(getattr(f, "style", None), "name", None)
        if isinstance(f, Paragraph) and name in _HEADING_SPACE:
            out.append(CondPageBreak(_HEADING_SPACE[name]))
        out.append(f)
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _ensure() -> None:
    _register_fonts()
    if not ST:
        ST.update(_styles())


def build_pdf(snapshot: dict) -> bytes:
    """Render the completion report for `snapshot` and return the PDF bytes."""
    _ensure()
    snap = snapshot or {}
    final = str(snap.get("status", "")).strip().lower() == "final"
    attempt_id = (snap.get("attempt") or {}).get("id", "")
    version = (snap.get("app") or {}).get("model_version", "")

    pw, ph = letter
    lw, lh = landscape(letter)
    pbody_w = pw - 2 * PORT_LR
    lbody_w = lw - 2 * LAND_LR

    def frame(w, h, lr, top, bottom, fid):
        return Frame(lr, bottom, w - 2 * lr, h - top - bottom, leftPadding=0, rightPadding=0,
                     topPadding=0, bottomPadding=0, id=fid)

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=letter, leftMargin=PORT_LR, rightMargin=PORT_LR,
                          topMargin=PORT_TOP, bottomMargin=PORT_BOTTOM,
                          title="Aggregate Anxiety Completion Report %s" % _clean(attempt_id)[0],
                          subject="Aggregate Anxiety simulation completion report",
                          creator="Juicetification: Aggregate Anxiety %s" % _clean(version)[0],
                          author="", pageCompression=1)
    doc.addPageTemplates([
        PageTemplate(id="portrait", frames=[frame(pw, ph, PORT_LR, PORT_TOP, PORT_BOTTOM, "p")],
                     pagesize=letter),
        PageTemplate(id="landscape", frames=[frame(lw, lh, LAND_LR, LAND_TOP, LAND_BOTTOM, "l")],
                     pagesize=landscape(letter)),
    ])
    story: List = []
    story += _sec_header(snap, final, pbody_w)
    story += _sec_scenario(snap, pbody_w)
    story += _sec_activities(snap, pbody_w)
    story += _sec_compare(snap, pbody_w)
    story += _sec_strategy(snap, pbody_w)
    story += _sec_reflections(snap, pbody_w)
    story += _sec_hybrid(snap, pbody_w)
    story += _sec_usage(snap, pbody_w)
    story += _sec_integrity(snap, pbody_w)
    story += [NextPageTemplate("landscape"), PageBreak()]
    story += _appendix_flowables(snap, lbody_w)
    story = _guard_headings(story)
    doc.build(story, canvasmaker=_make_canvas(final, attempt_id, version))
    return buf.getvalue()
