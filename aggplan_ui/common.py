"""Shared Streamlit helpers: the run context, persisted widgets, the task card and styling.

Every value a student enters lives in `pg` (a plain JSON-safe dict kept in st.session_state
and saved by aggplan.persistence). Widgets are bound to pg through a throw-away widget key,
because Streamlit forgets the state of widgets that aren't rendered on the current page —
so text and choices survive navigation, refresh (with saving) and recovery-file restore."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

import streamlit as st

from aggplan import workflow as W
from aggplan.identity import Identity
from aggplan.scenario import Scenario, stable_int

SS = st.session_state


def _stretch() -> dict:
    """Full-width kwargs that work on old and new Streamlit (use_container_width was
    deprecated in favour of width='stretch')."""
    import inspect
    try:
        if "width" in inspect.signature(st.dataframe).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


STRETCH = _stretch()


@dataclass
class Ctx:
    scn: Scenario
    pg: dict
    identity: Identity
    cfg: dict
    store: Any
    game: Optional[str]
    stage: str

    @property
    def h(self) -> str:
        return self.scn.config_hash

    @property
    def ans(self) -> dict:
        return self.pg.setdefault("ans", {})

    @property
    def live(self) -> bool:
        return self.cfg.get("feedback_mode") == "live"


def shuffled(ctx: Ctx, key: str, items) -> list:
    """Stable shuffle: same order on every rerun, restart and resume (no hash())."""
    lst = sorted(items)
    lst.sort(key=lambda x: stable_int("shuffle", ctx.scn.base_seed, key, x))
    return lst


def money(x) -> str:
    """Plain dollars for tables and st.metric."""
    return f"${x:,.0f}"


def moneym(x) -> str:
    """Markdown-safe dollars (a pair of bare $ would be rendered as LaTeX math)."""
    return f"\\${x:,.0f}"


# --------------------------------------------------------------------------- #
# Persisted widgets (value lives in pg['ans'][key])
# --------------------------------------------------------------------------- #
def _wk(ctx: Ctx, key: str) -> str:
    return "w_" + key


def p_text_area(ctx: Ctx, label: str, key: str, **kw) -> str:
    wk = _wk(ctx, key)
    if wk not in SS:
        SS[wk] = ctx.ans.get(key, "")
    st.text_area(label, key=wk, **kw)
    ctx.ans[key] = SS[wk]
    return SS[wk]


def p_text_input(ctx: Ctx, label: str, key: str, **kw) -> str:
    wk = _wk(ctx, key)
    if wk not in SS:
        SS[wk] = ctx.ans.get(key, "")
    st.text_input(label, key=wk, **kw)
    ctx.ans[key] = SS[wk]
    return SS[wk]


def p_radio(ctx: Ctx, label: str, options: Sequence, key: str, optional: bool = False, **kw):
    """optional=True starts with nothing selected."""
    wk = _wk(ctx, key)
    options = list(options)
    if wk not in SS or (SS[wk] is not None and SS[wk] not in options):
        prev = ctx.ans.get(key)
        SS[wk] = prev if prev in options else (None if optional else options[0])
    if optional:
        st.radio(label, options, index=None, key=wk, **kw)
    else:
        st.radio(label, options, key=wk, **kw)
    ctx.ans[key] = SS[wk]
    return SS[wk]


def p_multiselect(ctx: Ctx, label: str, options: Sequence, key: str, **kw) -> list:
    wk = _wk(ctx, key)
    options = list(options)
    if wk not in SS:
        SS[wk] = [o for o in ctx.ans.get(key, []) if o in options]
    st.multiselect(label, options, key=wk, **kw)
    ctx.ans[key] = list(SS[wk])
    return ctx.ans[key]


def p_number(ctx: Ctx, label: str, key: str, default=0, **kw):
    wk = _wk(ctx, key)
    if wk not in SS:
        SS[wk] = ctx.ans.get(key, default)
    v = st.number_input(label, key=wk, **kw)
    ctx.ans[key] = v
    return v


def p_checkbox(ctx: Ctx, label: str, key: str, default=False, **kw) -> bool:
    wk = _wk(ctx, key)
    if wk not in SS:
        SS[wk] = bool(ctx.ans.get(key, default))
    v = st.checkbox(label, key=wk, **kw)
    ctx.ans[key] = v
    return v


# --------------------------------------------------------------------------- #
# Checks (explicit "Check my work")
# --------------------------------------------------------------------------- #
def record_check(ctx: Ctx, key: str, passed: bool) -> None:
    c = ctx.pg.setdefault("checks", {}).setdefault(key, {"n": 0, "passed": False})
    c["n"] = int(c.get("n", 0)) + 1
    c["passed"] = bool(passed)


def feedback(kind: str, text: str) -> None:
    """Status message with an icon and a text label (never color alone)."""
    icon, label, fn = {"ok": ("✅", "Correct", st.success), "bad": ("❌", "Not yet", st.error),
                       "warn": ("⚠️", "Check", st.warning), "info": ("ℹ️", "Note", st.info)}[kind]
    fn(f"**{icon} {label}.** {text}")


# --------------------------------------------------------------------------- #
# Task card and footer navigation
# --------------------------------------------------------------------------- #
GROUP_SHORT = {"understand": "Understand", "prepare": "Prepare", "build": "Build",
               "compare": "Compare", "submit": "Submit"}


def group_entry(ctx: Ctx, gkey: str) -> str:
    """Where a click on a group should land: its first unfinished stage, else its first."""
    stages = W.stages_in(gkey)
    statuses = W.all_status(ctx.pg, ctx.h)
    for s in stages:
        if s.required and not statuses[s.key].done:
            return s.key
    return stages[0].key


def stepper(ctx: Ctx) -> None:
    """A five-step strip across the top of every stage, so a student can see where they are
    and jump, without scrolling the sidebar at all. Each step names its own progress in
    words; the current step is also marked with a caret, never by colour alone."""
    here = W.group_of(ctx.stage)
    cols = st.columns(5)
    for col, (gkey, _label, done, total) in zip(cols, W.group_progress(ctx.pg, ctx.h)):
        current = gkey == here
        tick = "✓" if done >= total else f"{done}/{total}"
        label = ("▸ " if current else "") + f"{GROUP_SHORT[gkey]}  {tick}"
        col.button(label, key=f"step_{gkey}", on_click=go_to, args=(group_entry(ctx, gkey),),
                   type="primary" if current else "secondary", **STRETCH,
                   help=("You are here. " if current else "") + f"{done} of {total} done")


def task_card(ctx: Ctx) -> None:
    stage = W.STAGE_BY_KEY[ctx.stage]
    status = W.stage_status(ctx.pg, ctx.h, ctx.stage)
    done = sum(1 for r in W.requirement_list(ctx.pg, ctx.h) if r["required"] and r["complete"])
    total = sum(1 for r in W.requirement_list(ctx.pg, ctx.h) if r["required"])
    st.progress(done / total if total else 0.0,
                text=f"Required activities complete: {done} of {total}")
    with st.container(border=True):
        st.markdown(f"#### {status.icon} {stage.title} — *{status.text}*"
                    + ("" if stage.required else "  ·  optional"))
        c1, c2 = st.columns(2)
        c1.markdown(f"**Your task.** {stage.task}")
        c2.markdown(f"**What to do now.** {stage.action}")
        if status.reasons and not status.done:
            st.markdown("**Why this isn't complete yet:**\n" + "\n".join(f"- {r}" for r in status.reasons))


def go_to(stage_key: str) -> None:
    SS["nav_stage"] = stage_key


def footer_nav(ctx: Ctx) -> None:
    """Back and Next both NAME where they go, and Back mirrors Next: neither drops a student
    into an optional stage they chose to skip."""
    nxt = W.next_stage(ctx.pg, ctx.h, ctx.stage)
    prv = W.prev_stage(ctx.pg, ctx.h, ctx.stage)
    st.divider()
    cols = st.columns([2, 2, 1])
    if prv:
        cols[0].button(f"← Back to {W.STAGE_BY_KEY[prv].title}", key="nav_back",
                       on_click=go_to, args=(prv,), **STRETCH)
    if nxt:
        cols[1].button(f"Next: {W.STAGE_BY_KEY[nxt].title} →", key="nav_next", type="primary",
                       on_click=go_to, args=(nxt,), **STRETCH)


CSS = """
<style>
.aa-table{border-collapse:collapse;font-size:13px;width:100%}
.aa-table th,.aa-table td{border:1px solid #888;padding:4px 8px;text-align:right}
.aa-table th{background:#eef0f4;color:#111;text-align:center}
.aa-table td.l,.aa-table th.l{text-align:left}
.aa-table td.ok{background:#e3f4e8}
.aa-table td.bad{background:#fde3e3;font-weight:700}
.aa-table td.blank{background:#fff4d1}
.aa-table td.err{background:#fde3e3;font-weight:700}
.aa-table caption{caption-side:top;text-align:left;font-weight:600;padding:2px 0}
.aa-sum td{background:#eef2ff;font-weight:700}
.aa-wrap{overflow-x:auto}
</style>
"""
