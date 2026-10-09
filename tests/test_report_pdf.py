"""Tests for the PDF completion report (aggplan/report_pdf.py)."""

from __future__ import annotations

import copy
import io
import re
import time

import pytest
from pypdf import PdfReader

from aggplan import report_pdf as rp
from aggplan.engine import reference_plan, summarize
from aggplan.report_pdf import build_pdf, DRAFT_BANNER, money, qty
from tests.fixtures import make_snapshot, make_draft_snapshot
from tests.helpers import scn, SEASONAL

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def pages_text(pdf: bytes):
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]


def full_text(pdf: bytes) -> str:
    return "\n".join(pages_text(pdf))


def body_text(pdf: bytes) -> str:
    """Text with the repeating page header/footer removed (so wrapped text across pages joins)."""
    out = []
    for t in pages_text(pdf):
        t = re.sub(r"Juicetification: Aggregate Anxiety · Completion Report\s*✔ FINAL", "", t)
        t = re.sub(r"Attempt ID: \S+\s*Page \d+ of \d+\s*Aggregate Anxiety — model \S+\s*FINAL", "", t)
        out.append(t)
    return "\n".join(out)


def nows(s: str) -> str:
    return re.sub(r"\s+", "", s)


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def landscape_texts(pdf: bytes):
    reader = PdfReader(io.BytesIO(pdf))
    return [(p.extract_text() or "") for p in reader.pages
            if float(p.mediabox.width) > float(p.mediabox.height)]


@pytest.fixture(scope="module")
def final_snap():
    return make_snapshot()


@pytest.fixture(scope="module")
def final_pdf(final_snap):
    return build_pdf(final_snap)


# 1 --------------------------------------------------------------------------
def test_complete_report_basics(final_snap, final_pdf):
    assert final_pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(final_pdf))
    sizes = {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in reader.pages}
    assert sizes == {(612, 792), (792, 612)}      # portrait body + landscape appendix, US Letter
    txt = full_text(final_pdf)
    assert "FINAL" in txt and "DRAFT" not in txt and "draft" not in txt.lower()
    assert "Download your report, review it, and upload the PDF to your LMS assignment." in squash(txt)
    flat = nows(txt)
    assert nows(final_snap["attempt"]["id"]) in flat
    assert nows(final_snap["integrity"]["snapshot_hash"]) in flat
    assert nows(final_snap["app"]["model_version"]) in flat
    assert re.search(r"Scenario seed\s*%d\b" % final_snap["scenario"]["seed"], txt)
    for t in pages_text(final_pdf):
        assert "FINAL" in t and re.search(r"Page\s*\d+\s*of\s*\d+", t)
    assert "Instructors can verify this report" in squash(txt)
    assert b"/JavaScript" not in final_pdf and b"/URI" not in final_pdf and b"/Launch" not in final_pdf


# 2 --------------------------------------------------------------------------
def test_incomplete_is_marked_on_every_page_and_hides_plans():
    snap = make_draft_snapshot()
    assert snap["plans"]["chase"] is None and snap["plans"]["level"] is None
    pdf = build_pdf(snap)
    pages = pages_text(pdf)
    assert len(pages) >= 3
    for t in pages:
        assert "DRAFT" in t
        assert DRAFT_BANNER in squash(t)
    txt = full_text(pdf)
    assert "Missing requirements" in txt
    for m in snap["missing"]:
        assert nows(m) in nows(txt)
    assert "DRAFT — NOT FOR SUBMISSION" in squash(txt)
    assert "must not be submitted" in squash(txt)
    assert "upload the PDF to your LMS" not in squash(txt)
    assert "Not completed — no verified plan yet" in squash(txt)
    s = scn(SEASONAL, seed=7)
    for kind in ("chase", "level"):
        _, df = reference_plan(s, kind)
        assert money(summarize(df, s)["Total cost"]) not in txt      # no answer-key leakage
    assert "Appendix A" in txt and "Appendix B" in txt


# 3 --------------------------------------------------------------------------
def _long_text(n=25000):
    para = ("Because the level plan avoids hiring and layoff churn, it keeps workers employed; "
            "however, it carries inventory & backlog. ")
    out, i, size = [], 0, 0
    while size < n - 700:
        piece = para + "(%d)" % i + ("\n\n" if i % 4 == 3 else "\n" if i % 2 else " ")
        out.append(piece)
        size += len(piece)
        i += 1
    return "".join(out) + "W" * 600 + " end-marker-ZED"


def test_long_justification_is_complete_and_paginates():
    just = _long_text()
    assert len(just) > 24000
    pdf = build_pdf(make_snapshot(recommendation=just))
    base_pages = len(PdfReader(io.BytesIO(build_pdf(make_snapshot()))).pages)
    assert len(PdfReader(io.BytesIO(pdf)).pages) >= base_pages + 4
    assert nows(just) in nows(body_text(pdf))


def test_long_unbroken_word_stays_inside_page():
    import fitz
    just = "A" * 20 + " " + "W" * 600 + " https://example.com/" + "x" * 400
    pdf = build_pdf(make_snapshot(recommendation=just))
    doc = fitz.open(stream=pdf, filetype="pdf")
    for page in doc:
        r = page.rect
        for b in page.get_text("blocks"):
            assert b[0] >= 0 and b[2] <= r.width + 0.5, (page.number, b[:4])
    assert nows(just) in nows(body_text(pdf))


# 4 --------------------------------------------------------------------------
def test_appendix_rows_present_for_both_plans(final_pdf):
    land = landscape_texts(final_pdf)
    assert len(land) >= 3
    for tag in ("Appendix A", "Appendix B", "Appendix C"):
        t = [x for x in land if tag in x][0]
        for m in MONTHS:
            assert m in t
        assert "TotalMonthlyCost" in nows(t) and "Total" in t


def test_appendix_header_repeats_when_table_splits(monkeypatch, final_snap):
    monkeypatch.setattr(rp, "LAND_TOP", 5.0 * 72)       # shrink landscape frame -> forced split
    land = landscape_texts(build_pdf(final_snap))
    assert len(land) >= 6                                # every table needs at least two pages
    assert all(len(t) > 200 for t in land)               # no blank landscape pages
    assert sum("TotalMonthlyCost" in nows(t) for t in land) >= 6
    for t in land:
        if any(m in t for m in MONTHS):
            assert "TotalMonthlyCost" in nows(t)         # header row repeated on every page


def test_appendix_tables_have_repeat_rows(final_snap):
    from reportlab.platypus import Table
    rp._ensure()
    tables = [f for f in rp._appendix_flowables(final_snap, 720) if isinstance(f, Table)]
    assert len(tables) == 3
    assert all(t.repeatRows == 1 for t in tables)


# 5 --------------------------------------------------------------------------
def test_special_characters_are_literal_and_do_not_crash():
    just = ('Hello <img src="file:///etc/passwd"> <a href="http://x">x</a> &amp; %s {0} back\\slash\t'
            'tab 😀 漢字 Привет مرحبا <b>Bold</b> <unknown> </para> &#65; &lt;')
    snap = make_snapshot(name="Zoë O'Brien-Núñez & Sons <b>Bold</b>", section="OPS 301/02 #5",
                         recommendation=just,
                         reflections=["<i>x</i> reflection one with enough words here ok",
                                      "Ünïcödé reflection two with words {} % \\",
                                      "reflection three <script>alert(1)</script> words words"])
    pdf = build_pdf(snap)
    txt = full_text(pdf)
    flat = nows(txt)
    assert "Zoë O'Brien-Núñez & Sons <b>Bold</b>" in squash(txt)
    assert "OPS 301/02 #5" in squash(txt)
    for lit in ('<img src="file:///etc/passwd">', '<a href="http://x">x</a>', "&amp;", "%s", "{0}",
                "back\\slash", "<b>Bold</b>", "<unknown>", "</para>", "&#65;", "&lt;",
                "<script>alert(1)</script>", "Привет"):
        assert nows(lit) in flat, lit
    assert "file:///etc/passwd" in txt
    assert "Some characters in this response could not be displayed in the PDF" in squash(txt)
    assert b"/URI" not in pdf and b"/JavaScript" not in pdf and b"/Launch" not in pdf
    assert "□" in txt            # unsupported characters become a visible placeholder
    assert "漢" not in txt


def test_control_characters_stripped():
    snap = make_snapshot(name="Ada\x00 Lovelace\x07‮", recommendation="a\x0bb\x1fc " * 30)
    assert "Ada Lovelace" in full_text(build_pdf(snap))


# 6 --------------------------------------------------------------------------
@pytest.mark.parametrize("scn_obj", [
    scn(SEASONAL, seed=7),
    scn(SEASONAL, seed=11, regular_labor_cost=4000, hiring_cost=700, overtime_pct=0.05),
])
def test_totals_match_engine_and_snapshot(scn_obj):
    snap = make_snapshot(scn_obj)
    txt = full_text(build_pdf(snap))
    flat = squash(txt)
    totals = {}
    for kind in ("chase", "level"):
        _, df = reference_plan(scn_obj, kind)
        eng = summarize(df, scn_obj)
        totals[kind] = eng["Total cost"]
        for key in ("Total cost", "Total regular labor cost", "Total hiring cost", "Total layoff cost",
                    "Total overtime cost", "Total subcontract cost", "Total holding cost",
                    "Total backorder cost", "Total disposal cost"):
            assert snap["plans"][kind]["summary"][key] == pytest.approx(eng[key], abs=1e-6)
        assert money(eng["Total cost"]) in txt.split("Appendix A")[1]       # appendix total row
    # comparison table: each cost category row shows chase then level, both from the engine
    rows = [("Regular labor cost", "Total regular labor cost"), ("Hiring cost", "Total hiring cost"),
            ("Layoff cost", "Total layoff cost"), ("Overtime cost", "Total overtime cost"),
            ("Subcontract cost", "Total subcontract cost"), ("Holding cost", "Total holding cost"),
            ("Backorder cost", "Total backorder cost"), ("Disposal cost", "Total disposal cost"),
            ("Total plan cost", "Total cost")]
    engs = {k: summarize(reference_plan(scn_obj, k)[1], scn_obj) for k in ("chase", "level")}
    for label, key in rows:
        pat = r"%s %s %s" % (re.escape(label), re.escape(money(engs["chase"][key])),
                             re.escape(money(engs["level"][key])))
        assert re.search(pat, flat), label
    assert qty(sum(scn_obj.demand)) in txt
    p = scn_obj.params
    assert money(p["regular_labor_cost"]) in flat
    assert money(p["hiring_cost"]) in flat
    assert "%d%%" % round(p["overtime_pct"] * 100) in flat
    assert qty(p["beginning_inventory"]) in flat
    for d in scn_obj.demand:
        assert qty(d) in txt


# 7 --------------------------------------------------------------------------
def test_draft_vs_final_labelling_flips():
    s = scn(SEASONAL, seed=7)
    done = full_text(build_pdf(make_snapshot(s)))
    undone = full_text(build_pdf(make_snapshot(s, complete=False)))
    assert "FINAL" in done and "DRAFT" not in done
    assert "DRAFT" in undone and "FINAL" not in undone
    assert "upload the PDF to your LMS" in squash(done)
    assert "upload the PDF to your LMS" not in squash(undone)


def test_draft_has_watermark_on_every_page_final_has_none():
    import fitz

    def big_spans(page):
        return [s for b in page.get_text("dict")["blocks"] if b["type"] == 0
                for l in b["lines"] for s in l["spans"] if s["size"] > 100]

    for page in fitz.open(stream=build_pdf(make_draft_snapshot()), filetype="pdf"):
        assert big_spans(page), "no watermark on page %d" % page.number
    for page in fitz.open(stream=build_pdf(make_snapshot()), filetype="pdf"):
        assert not big_spans(page)


# 8 --------------------------------------------------------------------------
def _section(txt: str, start: str, end: str) -> str:
    i = txt.index(start)
    return txt[i:txt.index(end, i)]


def test_hybrid_sections_are_separate(final_snap, final_pdf):
    txt = re.sub(r"[ \t]+", " ", full_text(final_pdf))
    hyb = _section(txt, "6. Hybrid challenge", "7. Attempts")
    i_best = hyb.index("Best feasible plan")
    i_low = hyb.index("Lower-cost attempt that does NOT meet requirements")
    assert i_best < i_low
    best, low = hyb[i_best:i_low], hyb[i_low:]
    h = final_snap["hybrid"]
    assert money(h["best_feasible"]["summary"]["Total cost"]) in best
    assert money(h["lowest_cost_infeasible"]["summary"]["Total cost"]) in low
    assert money(h["lowest_cost_infeasible"]["summary"]["Total cost"]) not in best
    for f in h["lowest_cost_infeasible"]["failed_requirements"]:
        assert nows(f) in nows(low)
    assert "Number of attempts: 2" in hyb
    assert "Automatic-assistance notes" in best
    assert "Appendix C" in txt


def test_hybrid_only_infeasible(final_snap):
    snap = copy.deepcopy(final_snap)
    snap["hybrid"]["best_feasible"] = None
    txt = squash(full_text(build_pdf(snap)))
    assert "No feasible plan was found" in txt
    assert "Lower-cost attempt that does NOT meet requirements" in txt
    assert "No feasible hybrid plan" in txt


def test_no_hybrid_is_not_attempted_and_uncertainty_table():
    snap = make_snapshot(hybrid=False)
    assert snap["hybrid"] is None
    txt = re.sub(r"[ \t]+", " ", full_text(build_pdf(snap)))
    assert "Not attempted" in _section(txt, "6. Hybrid challenge", "7. Attempts")
    snap2 = make_snapshot()
    snap2["uncertainty"] = {"n_draws": 200, "mean_cost": 470123.0, "p10_cost": 450000.0,
                            "p90_cost": 495500.0, "mean_on_time": 97.25, "p10_on_time": 93.0,
                            "prob_any_shortage": 0.4, "prob_meets_service": 0.85}
    txt2 = full_text(build_pdf(snap2))
    assert "Forecast-error evaluation" in txt2
    assert "$470,123" in txt2 and "$495,500" in txt2 and "40.0%" in txt2 and "85.0%" in txt2


def test_one_plan_missing_shows_nothing_for_it(final_snap):
    snap = copy.deepcopy(final_snap)
    lvl_total = money(snap["plans"]["level"]["summary"]["Total cost"])
    snap["plans"]["level"] = None
    txt = full_text(build_pdf(snap))
    assert "Not completed — no verified plan yet" in squash(txt)
    assert lvl_total not in txt
    assert money(snap["plans"]["chase"]["summary"]["Total cost"]) in txt


# 9 --------------------------------------------------------------------------
def test_no_secrets(monkeypatch, final_snap):
    monkeypatch.setenv("AGG_TEST_SECRET_VALUE", "zq9-env-value-zq9")
    txt = full_text(build_pdf(final_snap)).lower()
    for bad in ("sid=", "token", "secret", "password", "zq9-env-value"):
        assert bad not in txt
    src = open(rp.__file__, encoding="utf-8").read()
    assert "environ" not in src and "getenv" not in src
    assert "requests" not in src and "urllib" not in src and "streamlit" not in src.lower().replace(
        "no streamlit", "")


# 10 -------------------------------------------------------------------------
def test_build_time(final_snap):
    t = time.time()
    build_pdf(final_snap)
    assert time.time() - t < 10


def test_footer_and_metadata(final_snap, final_pdf):
    reader = PdfReader(io.BytesIO(final_pdf))
    pages = pages_text(final_pdf)
    n = len(pages)
    for i, t in enumerate(pages, 1):
        assert re.search(r"Page\s*%d\s*of\s*%d\b" % (i, n), t)
        assert "Aggregate Anxiety — model %s" % final_snap["app"]["model_version"] in t
        assert final_snap["attempt"]["id"] in t
    meta = reader.metadata
    assert final_snap["attempt"]["id"] in (meta.title or "")
    assert not (meta.author or "")
    assert final_snap["student"]["name"] not in str(meta)


def test_activity_status_has_text_labels():
    txt = full_text(build_pdf(make_draft_snapshot()))
    assert "Complete" in txt and "Incomplete" in txt and "Not attempted" in txt
    assert "✔" in txt and "✖" in txt


def test_usage_table_is_neutral(final_pdf):
    sect = squash(_section(full_text(final_pdf), "7. Attempts and help usage", "8. Report integrity")).lower()
    for word in ("too many", "excessive", "good", "bad", "penal", "relied", "only"):
        assert word not in sect
    assert "checks run" in sect and "scenario replacements" in sect and "worked solution opened" in sect


def test_reflections_and_compare_answers_verbatim():
    refl = ["First line\n\nSecond   paragraph with  spaces <b>x</b>.",
            "Zoë says: «ça va»? Ünï.", "Third."]
    snap = make_snapshot(reflections=refl)
    snap["tradeoff_note"] = "My trade-off note\nwith two lines."
    txt = nows(body_text(build_pdf(snap)))
    for r in refl:
        assert nows(r) in txt
    assert nows(snap["tradeoff_note"]) in txt
    for qa in snap["compare_answers"]:
        assert nows(qa["prompt"]) in txt
