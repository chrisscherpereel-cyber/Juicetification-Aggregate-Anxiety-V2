"""Workflow model: the five navigation groups, the stages in them, what each stage asks the
student to do, and — from the saved progress dict `pg` — whether it is complete and, if
not, exactly why. The PDF report uses the same completion rules, so the app's checklist and
the report can never disagree.

`pg` layout (all JSON-safe; see persistence.py):
    pg["ans"]       simple answers keyed by name (s1q1, s2picks, s3picks, s4_<col>, cap_*, …)
    pg["checks"]    {stage_key: {"n": checks run, "passed": bool}}
    pg["plans"]     {"chase": {...}, "level": {...}}   worksheet state + completion record
    pg["hybrid"]    {"inputs": …, "state": {attempts, history, best_feasible, …}}
    pg["uncertainty"], pg["usage"], pg["report"]  …
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MIN_RECOMMENDATION_WORDS = 20
MIN_REFLECTION_WORDS = 10

GROUPS: List[Tuple[str, str]] = [
    ("understand", "1 · Understand the scenario"),
    ("prepare", "2 · Prepare the worksheet"),
    ("build", "3 · Build chase and level plans"),
    ("compare", "4 · Compare and improve"),
    ("submit", "5 · Submit and reflect"),
]


@dataclass(frozen=True)
class Stage:
    key: str
    group: str
    title: str
    task: str                 # what the student is doing here
    action: str               # the one thing to do next
    required: bool = True


STAGES: List[Stage] = [
    Stage("brief", "understand", "Scenario brief",
          "Read the company, forecast, costs and rules for your own scenario.",
          "Read the brief, then continue.", required=False),
    Stage("understand", "understand", "Frame the problem",
          "Decide what chase and level strategies trade off before calculating anything.",
          "Answer all six questions, then press ‘Check my work’.", required=False),
    Stage("data", "prepare", "What data do you need?",
          "Select every input the cost model needs — and nothing it doesn't.",
          "Select your inputs, then press ‘Check my work’."),
    Stage("columns", "prepare", "Choose your columns",
          "Choose the columns a chase worksheet needs.",
          "Select your columns, then press ‘Check my work’."),
    Stage("formulas", "prepare", "Build the formulas",
          "Pick the correct formula for each column.",
          "Choose a formula for every column, then press ‘Check my work’."),
    Stage("practice", "prepare", "Practice drills",
          "Warm up on small level and chase calculations.",
          "Work each scenario, then press ‘Check my work’ on it.", required=False),
    Stage("capacity", "prepare", "Capacity per worker",
          "Work out how many bottles one worker makes per day and per month.",
          "Enter both capacities, then press ‘Check my work’."),
    Stage("chase", "build", "Build the chase plan",
          "Fill the 12-month chase worksheet (in the app, or in Excel and import it).",
          "Fill every cell and the annual total, then press ‘Check my work’."),
    Stage("level", "build", "Build the level plan",
          "Fill the 12-month level worksheet (in the app, or in Excel and import it).",
          "Fill every cell and the annual total, then press ‘Check my work’."),
    Stage("compare", "compare", "Compare the plans",
          "Read the metrics side by side and interpret what they show.",
          "Answer the four interpretation questions."),
    Stage("hybrid", "compare", "Hybrid design challenge",
          "Design your own workforce plan under the same rules and try to improve on the "
          "reference plans — there is no guarantee it can.",
          "Set your decisions, review the feasibility panel, and save an attempt.", required=False),
    Stage("advanced", "compare", "Advanced options",
          "Optional extras your instructor may enable: forecast error, perishability, "
          "optimization benchmark.", "Explore the advanced exercises (optional).", required=False),
    Stage("reflect", "submit", "Recommend and reflect",
          "Choose a strategy and defend it in your own words, then reflect.",
          "Choose a plan and write your justification and three reflections."),
    Stage("submit", "submit", "Download your PDF report",
          "Check that every required activity is complete, then download your report.",
          "Review the checklist and download the PDF for your LMS."),
]
STAGE_BY_KEY = {s.key: s for s in STAGES}
STAGE_KEYS = [s.key for s in STAGES]


def words(text: Optional[str]) -> int:
    return len((text or "").split())


def plan_signature(scn_hash: str, plan_state: dict) -> str:
    """Fingerprint of everything a plan check depends on, so a check is only honoured while
    the worksheet, settings and scenario still match what was checked."""
    blob = json.dumps({"s": scn_hash, "ws": plan_state.get("ws"), "pol": plan_state.get("policy"),
                       "whole": plan_state.get("whole"), "maint": plan_state.get("maintain"),
                       "tot": plan_state.get("total_in"),
                       "lp": plan_state.get("level_prod_in"), "lw": plan_state.get("level_workers_in")},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def get(pg: dict, *path, default=None):
    cur = pg
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def check_passed(pg: dict, key: str) -> bool:
    return bool(get(pg, "checks", key, "passed", default=False))


def check_count(pg: dict, key: str) -> int:
    return int(get(pg, "checks", key, "n", default=0))


def plan_complete(pg: dict, kind: str, scn_hash: str) -> bool:
    st = get(pg, "plans", kind, default=None)
    if not st or not st.get("completed"):
        return False
    return st.get("sig") == plan_signature(scn_hash, st)


def plan_stale(pg: dict, kind: str, scn_hash: str) -> bool:
    st = get(pg, "plans", kind, default=None)
    return bool(st and st.get("completed") and st.get("sig") != plan_signature(scn_hash, st))


@dataclass
class StageStatus:
    key: str
    state: str                  # done | in_progress | not_started | optional
    reasons: List[str]

    @property
    def done(self) -> bool:
        return self.state == "done"

    @property
    def icon(self) -> str:
        return {"done": "✅", "in_progress": "🟡", "not_started": "⬜", "optional": "➕"}[self.state]

    @property
    def text(self) -> str:
        return {"done": "Complete", "in_progress": "In progress", "not_started": "Not started",
                "optional": "Optional"}[self.state]


def _answered(pg, *keys) -> int:
    ans = pg.get("ans", {})
    return sum(1 for k in keys if ans.get(k) not in (None, "", [], ()))


S1_KEYS = ["s1q1", "s1q2", "s1q3_ans", "s1q4_ans", "s1q5_ans", "s1q6_ans"]
CMP_KEYS = ["q_cost", "q_wf", "q_inv", "q_serv"]
REFL_KEYS = ["refl_stability", "refl_responsive", "refl_surprise"]
PRACTICE_N = 4


def stage_status(pg: dict, scn_hash: str, key: str) -> StageStatus:
    ans = pg.get("ans", {})
    st = STAGE_BY_KEY[key]
    reasons: List[str] = []

    def result(done: bool, started: bool) -> StageStatus:
        if done:
            return StageStatus(key, "done", [])
        if not st.required:
            return StageStatus(key, "in_progress" if started else "optional", reasons)
        return StageStatus(key, "in_progress" if started else "not_started", reasons)

    if key == "brief":
        return result(bool(pg.get("visited", {}).get("brief")), bool(pg.get("visited", {}).get("brief")))
    if key == "understand":
        n = _answered(pg, *S1_KEYS)
        if not check_passed(pg, key):
            reasons.append("Answer all six questions and press ‘Check my work’ "
                           f"({n}/6 answered)." if n < 6 else
                           "Press ‘Check my work’ — the answers haven't all been confirmed yet.")
        return result(check_passed(pg, key), n > 0)
    if key in ("data", "columns", "formulas"):
        n = check_count(pg, key)
        started = n > 0 or bool(ans.get({"data": "s2picks", "columns": "s3picks",
                                          "formulas": "s4_Workers_ans"}[key]))
        if not check_passed(pg, key):
            reasons.append("Press ‘Check my work’ and fix anything flagged." if started else
                           "Not started yet.")
        return result(check_passed(pg, key), started)
    if key == "practice":
        done_n = sum(1 for i in range(PRACTICE_N) if get(pg, "practice", str(i), "passed"))
        if done_n < PRACTICE_N:
            reasons.append(f"{done_n}/{PRACTICE_N} drills checked and correct.")
        return result(done_n == PRACTICE_N, done_n > 0 or bool(get(pg, "practice")))
    if key == "capacity":
        if not check_passed(pg, key):
            reasons.append("Enter capacity per day and per month and press ‘Check my work’.")
        return result(check_passed(pg, key), bool(ans.get("cap_capday") or ans.get("cap_capmonth")))
    if key in ("chase", "level"):
        stt = get(pg, "plans", key, default={}) or {}
        done = plan_complete(pg, key, scn_hash)
        if not done:
            if plan_stale(pg, key, scn_hash):
                reasons.append("You changed the worksheet or settings after the last successful "
                               "check — press ‘Check my work’ again.")
            else:
                reasons.append("Every cell and the annual total must pass ‘Check my work’."
                               + ("" if check_passed(pg, "capacity") else
                                  " The Capacity stage must also be complete."))
        started = any(v for v in (stt.get("ws") or {}).values()) if isinstance(stt.get("ws"), dict) else False
        return result(done, started or int(stt.get("n_checks", 0)) > 0)
    if key == "compare":
        n = _answered(pg, *CMP_KEYS)
        if not (plan_complete(pg, "chase", scn_hash) and plan_complete(pg, "level", scn_hash)):
            reasons.append("Finish and check both plans first.")
        elif n < 4:
            reasons.append(f"Answer all four interpretation questions ({n}/4).")
        done = n == 4 and plan_complete(pg, "chase", scn_hash) and plan_complete(pg, "level", scn_hash)
        return result(done, n > 0)
    if key == "hybrid":
        h = pg.get("hybrid", {}).get("state", {})
        return result(bool(h.get("best_feasible")), int(h.get("attempts", 0)) > 0)
    if key == "advanced":
        adv = pg.get("uncertainty", {})
        return result(bool(adv.get("committed")) or bool(pg.get("advanced_visited")), bool(adv))
    if key == "reflect":
        rec_ok = words(ans.get("recommendation")) >= MIN_RECOMMENDATION_WORDS
        refl_ok = [words(ans.get(k)) >= MIN_REFLECTION_WORDS for k in REFL_KEYS]
        if not ans.get("chosen_plan"):
            reasons.append("Choose Chase or Level as your recommendation.")
        if not rec_ok:
            reasons.append(f"Write at least {MIN_RECOMMENDATION_WORDS} words defending your "
                           f"recommendation ({words(ans.get('recommendation'))} so far).")
        for k, ok, nm in zip(REFL_KEYS, refl_ok, ("stability", "responsiveness", "surprise")):
            if not ok:
                reasons.append(f"Reflection on {nm}: write at least {MIN_REFLECTION_WORDS} words "
                               f"({words(ans.get(k))} so far).")
        done = bool(ans.get("chosen_plan")) and rec_ok and all(refl_ok)
        return result(done, bool(ans.get("recommendation")) or any(ans.get(k) for k in REFL_KEYS))
    if key == "submit":
        rep = pg.get("report", {})
        done = bool(rep.get("final_generated")) and rep.get("fp") == work_fingerprint(pg)
        if rep.get("final_generated") and not done:
            reasons.append("Your work changed after the last report was generated — generate it again.")
        return result(done, bool(rep))
    raise KeyError(key)


def all_status(pg: dict, scn_hash: str) -> Dict[str, StageStatus]:
    return {s.key: stage_status(pg, scn_hash, s.key) for s in STAGES}


# --------------------------------------------------------------------------- #
# Required activities (the PDF checklist) and progress
# --------------------------------------------------------------------------- #
REQUIRED_ACTIVITIES: List[Tuple[str, str, str]] = [
    # (stage key, label, short requirement)
    ("data", "Select the required data inputs", "all required inputs, no extras"),
    ("columns", "Choose the worksheet columns", "the correct column set"),
    ("formulas", "Choose the correct formulas", "all formulas correct"),
    ("capacity", "Calculate capacity per worker", "per-day and per-month capacity"),
    ("chase", "Build and verify the chase plan", "every cell and the annual total correct"),
    ("level", "Build and verify the level plan", "every cell and the annual total correct"),
    ("compare", "Interpret the plan comparison", "all four questions answered"),
    ("reflect", "Recommendation and reflections", "strategy chosen, justification and 3 reflections"),
]
OPTIONAL_ACTIVITIES: List[Tuple[str, str]] = [
    ("understand", "Frame the problem (Stage 1 check)"),
    ("practice", "Practice drills"),
    ("hybrid", "Hybrid design challenge"),
]


def requirement_list(pg: dict, scn_hash: str) -> List[dict]:
    """[{key,label,required,complete,detail}] for the report checklist."""
    out = []
    for key, label, req in REQUIRED_ACTIVITIES:
        s = stage_status(pg, scn_hash, key)
        out.append({"key": key, "label": label, "required": True, "complete": s.done,
                    "detail": req if s.done else " ".join(s.reasons) or req})
    for key, label in OPTIONAL_ACTIVITIES:
        s = stage_status(pg, scn_hash, key)
        out.append({"key": key, "label": label, "required": False, "complete": s.done,
                    "detail": "Completed" if s.done else
                    ("Attempted, not completed" if s.state == "in_progress" else "Not attempted")})
    return out


def missing_requirements(pg: dict, scn_hash: str) -> List[str]:
    out = [f"{r['label']}: {r['detail']}" for r in requirement_list(pg, scn_hash)
           if r["required"] and not r["complete"]]
    if not (pg.get("student_name") or "").strip():
        out.insert(0, "Student name: enter your name (sidebar) so the report identifies you.")
    if not (pg.get("section") or "").strip():
        out.insert(1 if out and out[0].startswith("Student name") else 0,
                   "Course section: enter your section (sidebar; type N/A if you have none).")
    return out


def work_fingerprint(pg: dict) -> str:
    """Hash of the student's graded work (not timestamps/UI state): a generated report is
    'stale' once this changes."""
    keep = {"ans": pg.get("ans"), "student_name": pg.get("student_name"), "section": pg.get("section"),
            "checks": {k: bool(v.get("passed")) for k, v in (pg.get("checks") or {}).items()},
            "plans": {k: {"sig": v.get("sig"), "done": v.get("completed"), "tries": v.get("tries"),
                          "stuck": v.get("stuck")} for k, v in (pg.get("plans") or {}).items()},
            "hybrid": (pg.get("hybrid") or {}).get("state"), "unc": (pg.get("uncertainty") or {}).get("eval"),
            "practice": pg.get("practice")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


def progress_fraction(pg: dict, scn_hash: str) -> float:
    reqs = [r for r in requirement_list(pg, scn_hash) if r["required"]]
    done = sum(1 for r in reqs if r["complete"])
    return done / len(reqs) if reqs else 0.0


def next_stage(pg: dict, scn_hash: str, current: str) -> Optional[str]:
    """The next stage worth visiting: the first unfinished REQUIRED stage after the current
    one (wrapping to earlier ones), otherwise the submit stage."""
    statuses = all_status(pg, scn_hash)
    i = STAGE_KEYS.index(current)
    order = STAGE_KEYS[i + 1:] + STAGE_KEYS[:i]
    for k in order:
        if STAGE_BY_KEY[k].required and not statuses[k].done and k != "submit":
            return k
    if current != "submit":
        return "submit"
    return None


def group_of(key: str) -> str:
    return STAGE_BY_KEY[key].group


def stages_in(group: str) -> List[Stage]:
    return [s for s in STAGES if s.group == group]
