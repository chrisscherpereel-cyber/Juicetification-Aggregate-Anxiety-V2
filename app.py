"""
Juicetification: Aggregate Anxiety  (model 3.x)
An individual experiential Streamlit simulation of aggregate production planning.

Students identify the data, choose worksheet columns, build formulas, calculate chase and
level plans by hand (in the app or in Excel), compare them, and defend a recommendation.
They finish by downloading a PDF completion report for their LMS.

Run:  streamlit run app.py        (see README.md for configuration)

This file only wires things together: calculation, validation, persistence, Excel and PDF
code live in `aggplan/`; the stage screens live in `aggplan_ui/`.
"""

from __future__ import annotations

import random
import time

import streamlit as st
import streamlit.components.v1 as components

import student_store as store
from juice_director import resolve_config, serve_manifest_if_requested
from manifest import MANIFEST, MODEL_VERSION

from aggplan import identity as ident
from aggplan import persistence as persist
from aggplan import workflow as W
from aggplan.scenario import resolve_scenario
from aggplan_ui import common as C
from aggplan_ui import stages_build, stages_compare, stages_early, stages_submit

st.set_page_config(page_title="Juicetification: Aggregate Anxiety", page_icon="🧃", layout="wide")
SS = st.session_state

serve_manifest_if_requested(MANIFEST)
CFG, CTX = resolve_config(MANIFEST)
st.markdown(C.CSS, unsafe_allow_html=True)


def scroll_to_top(token) -> None:
    """Scroll to the top once per navigation (the token makes the payload unique, so the
    iframe re-runs only when the stage changes)."""
    components.html(
        f"""<div id="st-{token}" style="display:none">{token}</div><script>(function(){{
        try{{const d=window.parent.document;
        for(const s of ['section.main','div[data-testid="stAppViewContainer"]',
                        'div[data-testid="stMain"]','.main']){{const e=d.querySelector(s);
                        if(e){{e.scrollTop=0;}}}}
        window.parent.scrollTo(0,0);}}catch(e){{}}}})();</script>""", height=0)


def new_pg() -> dict:
    return {"ans": {}, "checks": {}, "plans": {}, "practice": {}, "visited": {}, "variant": 0,
            "lineage": [], "student_name": "", "section": "", "stage": "brief"}


# --------------------------------------------------------------------------- #
# 1. Identity
# --------------------------------------------------------------------------- #
GAME = store.game_code()
SID = store.get_student_id()
TOK = st.query_params.get("tok")
PRACTICE = bool(SS.get("practice")) or bool(st.query_params.get("practice"))
identity = ident.resolve_identity(GAME, SID, TOK, store.enabled(), practice=PRACTICE)

if identity.mode == "blocked":
    st.title("Juicetification: Aggregate Anxiety")
    st.error("**This assignment needs your personal sign-in link.** " + identity.message)
    st.write("Open the link your instructor gave you (it includes a signed token). If you only "
             "want to practise, you can continue without saving or submitting anything.")
    if st.button("Continue in practice mode (nothing is recorded)"):
        SS["practice"] = True
        st.rerun()
    st.stop()

if identity.mode == "unverified" and not identity.sid and store.enabled():
    st.title("Juicetification: Aggregate Anxiety")
    st.subheader("Enter your student ID to begin")
    entered = st.text_input("Student ID", key="_gate_sid",
                            help="Your progress is saved under this ID so you can resume later.")
    c1, c2 = st.columns(2)
    if c1.button("Start", type="primary") and entered.strip():
        store.set_student_id(entered)
        st.rerun()
    if c2.button("Practice without saving"):
        SS["practice"] = True
        st.rerun()
    st.stop()

# --------------------------------------------------------------------------- #
# 2. Progress: restore (once) with version / configuration checks
# --------------------------------------------------------------------------- #
if "pg" not in SS:
    SS["pg"] = new_pg()
pg = SS["pg"]


def seed_for_new_student() -> int:
    if identity.sid:
        return store.derive_seed(GAME, identity.sid, lo=1, hi=10 ** 6)
    if CTX["seed"] is not None:
        return int(CTX["seed"])
    return random.randint(1, 10 ** 6)


def apply_saved(saved: dict) -> None:
    for k in [k for k in SS.keys() if k.startswith(("w_", "in_", "chase_", "level_", "hy_", "unc_", "xl_", "cmp_", "sens_"))]:
        SS.pop(k, None)
    SS["pg"] = persist.restore_dataframes(saved["pg"])
    SS.pop("_pdf", None)


def check_saved(saved: dict) -> persist.Compat:
    seed_info = persist.saved_base_seed(saved)
    if not seed_info:
        return persist.check_compatibility({"x": 1}, resolve_scenario(CFG, 1, 0))
    return persist.check_compatibility(saved, resolve_scenario(CFG, *seed_info))


if not SS.get("_restore_done"):
    SS["_restore_done"] = True
    if identity.recorded and store.enabled():
        saved, err = persist.load_remote(store, GAME, identity.sid)
        if err:
            SS["_load_error"] = err
        elif saved:
            compat = check_saved(saved)
            if compat.ok:
                apply_saved(saved)
                pg = SS["pg"]
            else:
                SS["_incompat"] = {"reason": compat.reason, "saved": saved}

if SS.get("_load_error"):
    st.title("Juicetification: Aggregate Anxiety")
    st.warning("**Your saved progress could not be loaded.** " + SS["_load_error"])
    st.write("Saving is paused so that a fresh start can't overwrite work that is still safe on "
             "the server.")
    c1, c2 = st.columns(2)
    if c1.button("Try again", type="primary"):
        SS["_restore_done"] = False
        SS.pop("_load_error", None)
        st.rerun()
    if c2.button("Continue without my saved progress (saving will replace it)"):
        SS.pop("_load_error", None)
        st.rerun()
    st.stop()

if SS.get("_incompat"):
    inc = SS["_incompat"]
    st.title("Juicetification: Aggregate Anxiety")
    st.warning("**Saved progress found, but it can't be reused safely.** " + inc["reason"])
    st.write("Your old record has **not** been deleted or overwritten. Download a copy if you want to "
             "keep it, then start fresh under the current settings.")
    st.download_button("Download my old progress (JSON)", persist.recovery_bytes(inc["saved"]),
                       file_name="aggregate_anxiety_old_progress.json", mime="application/json")
    if st.button("Start fresh under the current settings", type="primary"):
        SS.pop("_incompat", None)
        st.rerun()
    st.stop()

if "base_seed" not in pg:
    pg["base_seed"] = seed_for_new_student()
    pg.setdefault("lineage", []).append({"variant": 0, "reason": "assigned"})

scn = resolve_scenario(CFG, int(pg["base_seed"]), int(pg.get("variant", 0)))

# --------------------------------------------------------------------------- #
# 3. Navigation state
# --------------------------------------------------------------------------- #
SS.setdefault("nav_stage", pg.get("stage", "brief") if pg.get("stage") in W.STAGE_KEYS else "brief")
stage_key = SS["nav_stage"]
SS.setdefault("_nav_token", 0)
if SS.get("_prev_stage") != stage_key:
    SS["_nav_token"] += 1
    SS["_prev_stage"] = stage_key
    for k in ("chase_reveal", "level_reveal"):
        SS.pop(k, None)
pg["stage"] = stage_key
ctx = C.Ctx(scn=scn, pg=pg, identity=identity, cfg=CFG, store=store, game=GAME, stage=stage_key)

# --------------------------------------------------------------------------- #
# 4. Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.title("🧃 Juicetification")
    st.caption(f"Aggregate Anxiety · model {MODEL_VERSION}")
    st.caption(f"🔐 {identity.label}" + (f" — {identity.message}" if identity.message and identity.mode != "signed" else ""))
    pg["student_name"] = st.text_input("Student name", pg.get("student_name", ""), key="in_name")
    pg["section"] = st.text_input("Course section", pg.get("section", ""), key="in_section")
    save_slot = st.empty()
    statuses = W.all_status(pg, scn.config_hash)
    st.markdown("**Your path**")
    for gkey, glabel in W.GROUPS:
        stg = W.stages_in(gkey)
        n_done = sum(1 for s in stg if statuses[s.key].done)
        with st.expander(f"{glabel}  ({n_done}/{len(stg)})", expanded=(W.group_of(stage_key) == gkey)):
            for s in stg:
                stt = statuses[s.key]
                st.button(f"{stt.icon} {s.title}" + ("" if s.required else " (optional)"),
                          key=f"nav_{s.key}", on_click=C.go_to, args=(s.key,),
                          type="primary" if s.key == stage_key else "secondary",
                          **C.STRETCH, help=f"{stt.text}")
    st.caption("✅ complete · 🟡 in progress · ⬜ not started · ➕ optional")
    with st.expander("📋 Scenario data (open any time)"):
        stages_early.brief_tables(ctx, height=200)
    with st.expander("❓ Help and how it works"):
        st.markdown(
            "**Stages** — 1) understand the scenario, 2) prepare the worksheet (data, columns, "
            "formulas, capacity), 3) build the chase and level plans, 4) compare and improve, "
            "5) recommend, reflect and download your PDF.\n\n"
            "**Entering values** — type a number or a formula starting with `=`; formulas may use "
            "cells (`=E2`), `+ - * / ^`, functions `MAX MIN SUM ROUNDUP ROUNDDOWN ROUND ABS` and the "
            "brief-data names (`LABOR`, `RATE`, …). January is row 2, as in Excel.\n\n"
            "**Check my work** — every stage has one. Feedback appears when you press it (unless "
            "your instructor set live feedback).\n\n"
            "**Keyboard** — every button and field is reachable with Tab; in the grid use Enter, "
            "Tab and the arrow keys.")
    for n in scn.notices:
        st.warning("Configuration note: " + n)
    rec_slot = st.empty()

# --------------------------------------------------------------------------- #
# 5. Main page
# --------------------------------------------------------------------------- #
scroll_to_top(SS["_nav_token"])
stage_def = W.STAGE_BY_KEY[stage_key]
if stage_key != "brief":
    st.title(stage_def.title)
if stage_key != "brief":
    C.task_card(ctx)

RENDER = {
    "brief": stages_early.render_brief,
    "understand": stages_early.render_understand,
    "data": stages_early.render_data,
    "columns": stages_early.render_columns,
    "formulas": stages_early.render_formulas,
    "practice": stages_early.render_practice,
    "capacity": stages_early.render_capacity,
    "chase": lambda c: stages_build.render_plan(c, "chase"),
    "level": lambda c: stages_build.render_plan(c, "level"),
    "compare": stages_compare.render_compare,
    "hybrid": stages_compare.render_hybrid,
    "advanced": stages_compare.render_advanced,
    "reflect": stages_submit.render_reflect,
    "submit": stages_submit.render_submit,
}
RENDER[stage_key](ctx)
if stage_key in ("brief", "understand", "data", "columns", "formulas", "practice", "capacity"):
    C.footer_nav(ctx)

# --------------------------------------------------------------------------- #
# 6. Autosave with visible status, recovery file
# --------------------------------------------------------------------------- #
DEBOUNCE = float(CFG.get("autosave_seconds", persist.DEFAULT_DEBOUNCE_SECONDS))
SAVING_ON = bool(identity.recorded and store.enabled())
status = persist.SaveStatus.from_dict(SS.get("_save"))
snapshot = persist.make_snapshot(pg, scn, identity.mode)


def run_autosave(force: bool = False) -> persist.SaveStatus:
    """Debounced autosave. Rapid edits coalesce into one upload every DEBOUNCE seconds;
    milestones (navigation, a passed check, a finished plan, a generated report) save at
    once. Returns the updated status."""
    st_ = persist.SaveStatus.from_dict(SS.get("_save"))
    if not SAVING_ON:
        st_.state, st_.pending = "off", False
        SS["_save"] = st_.to_dict()
        return st_
    now = time.time()
    blob = persist.content_blob(pg)
    changed = blob != SS.get("_last_blob")
    milestone = persist.milestone_key(pg, stage_key)
    important = force or (changed and milestone != SS.get("_last_milestone"))
    st_.pending = changed
    if st_.state == "off":
        st_.state = "pending"
    if persist.should_save(st_, changed, important=important, now=now, debounce=DEBOUNCE):
        persist.save_remote(store, GAME, identity.sid,
                            persist.make_snapshot(pg, scn, identity.mode), st_,
                            progress=W.progress_fraction(pg, scn.config_hash), step=stage_key,
                            now=now)
        if st_.state == "saved":
            SS["_last_blob"], SS["_last_milestone"] = blob, milestone
            st_.pending = False
    SS["_save"] = st_.to_dict()
    return st_


status = run_autosave()
with save_slot.container():
    if status.state == "failed":
        st.error(status.label())
    elif status.state == "saved":
        st.success(status.label())
    else:
        st.info(status.label())
with rec_slot.container():
    with st.expander("💾 Recovery file"):
        st.caption("Download a file with your work (no passwords or tokens). If the page is "
                   "refreshed or saving fails, upload it here to continue.")
        st.download_button("⬇ Download recovery file", persist.recovery_bytes(snapshot),
                           file_name=f"aggregate_anxiety_recovery_{scn.base_seed}.json",
                           mime="application/json", key="dl_recovery")
        up = st.file_uploader("Restore from a recovery file", type=["json"], key="up_recovery")
        if up is not None:
            snap, err = persist.parse_recovery(up.getvalue())
            if err:
                st.error(err)
            else:
                compat = check_saved(snap)
                if not compat.ok:
                    st.error("This recovery file can't be used: " + compat.reason)
                elif st.button("Restore this file (replaces my current work)", key="do_restore"):
                    apply_saved(snap)
                    SS["_last_blob"] = None
                    st.rerun()


# A student who makes an edit and then stops interacting would otherwise leave those changes
# un-uploaded, because Streamlit only runs the script on interaction. This tiny fragment
# reruns on a timer and flushes anything still pending. It renders nothing, runs only when
# server saving is on, and costs one small rerun per interval per student (set
# autosave_flush_seconds to 0 to turn it off).
_FLUSH_EVERY = float(CFG.get("autosave_flush_seconds", 20))
if SAVING_ON and _FLUSH_EVERY > 0 and hasattr(st, "fragment"):

    @st.fragment(run_every=_FLUSH_EVERY)
    def _autosave_flush():
        # No force: the fragment's job is only to make a rerun HAPPEN while the student is
        # idle. run_autosave still applies the debounce, so this never turns into an upload
        # per rerun (the fragment body also executes on the initial render).
        if persist.SaveStatus.from_dict(SS.get("_save")).pending:
            run_autosave()

    _autosave_flush()
