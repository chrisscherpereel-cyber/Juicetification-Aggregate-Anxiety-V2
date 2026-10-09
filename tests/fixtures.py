"""Builders for realistic student-progress (`pg`) dicts used by report/workflow tests."""

from __future__ import annotations

import copy

from aggplan import workflow as W
from aggplan.hybrid import HybridInputs, register_attempt, resolve, default_workers
from aggplan.identity import Identity
from aggplan.report import build_snapshot, new_attempt_id, now_in_zone
from tests.helpers import scn as make_scn, SEASONAL

LONG_TEXT = ("I recommend the level plan because it keeps the workforce stable all year, which "
             "reduces hiring and layoff churn, even though it holds more inventory and ends "
             "some months with backlog. ") * 12


def make_pg(scn, *, complete=True, name="Ada Lovelace-Núñez", section="OPS 301-02",
            recommendation=None, reflections=None, hybrid=True, chosen="Level",
            chase_whole=True, level_whole=True, level_maintain=False, chase_policy="use_first"):
    pg = {"student_name": name, "section": section, "ans": {}, "checks": {}, "plans": {},
          "practice": {}, "visited": {"brief": True}}
    h = scn.config_hash
    if complete:
        for k in ("data", "columns", "formulas", "capacity", "understand"):
            pg["checks"][k] = {"n": 2, "passed": True}
        for i in range(W.PRACTICE_N):
            pg["practice"][str(i)] = {"passed": True}
        for kind, kw in (("chase", dict(policy=chase_policy, whole=chase_whole, maintain=False)),
                         ("level", dict(policy="use_first", whole=level_whole,
                                        maintain=level_maintain))):
            st = {"ws": {"Regular Production": ["1"] * 12}, "total_in": 123456.0,
                  "completed": True, "n_checks": 3, "tries": 1, "stuck": 0, **kw}
            st["sig"] = W.plan_signature(h, st)
            pg["plans"][kind] = st
        pg["ans"].update({
            "q_cost": "Chase", "q_wf": "Chase", "q_inv": "Level", "q_serv": "Level",
            "chosen_plan": chosen,
            "recommendation": recommendation if recommendation is not None else LONG_TEXT,
        })
        for k, txt in zip(W.REFL_KEYS, reflections or [
                "Level gives stable work because nobody is hired or laid off during the year.",
                "Chase is more responsive because capacity follows demand month by month.",
                "I was surprised that idle paid capacity made whole workers cost more than expected."]):
            pg["ans"][k] = txt
        if hybrid:
            inp = HybridInputs(mode="quarterly", quarter_workers=[default_workers(scn)] * 4,
                               use_overtime=True, auto_overtime=True)
            st = {}
            register_attempt(st, resolve(scn, inp))
            inp2 = HybridInputs(mode="quarterly", quarter_workers=[1, 1, 1, 1])   # cheap, infeasible
            register_attempt(st, resolve(scn, inp2))
            pg["hybrid"] = {"state": st}
    return pg


def make_snapshot(scn=None, **kw):
    scn = scn or make_scn(SEASONAL)
    pg = make_pg(scn, **kw)
    ident = Identity(None, None, False, "practice")
    return build_snapshot(scn, pg, identity=ident, assignment_name="Aggregate Anxiety",
                          attempt_id=new_attempt_id(True), now=now_in_zone("America/Phoenix"),
                          tz_label="America/Phoenix")


def make_draft_snapshot(scn=None):
    scn = scn or make_scn(SEASONAL)
    pg = make_pg(scn, complete=False)
    pg["ans"].update({"recommendation": "Half-finished thought about level plans."})
    pg["checks"]["data"] = {"n": 1, "passed": True}
    ident = Identity(None, None, False, "practice")
    return build_snapshot(scn, pg, identity=ident, assignment_name="Aggregate Anxiety",
                          attempt_id=new_attempt_id(False), now=now_in_zone("America/Phoenix"),
                          tz_label="America/Phoenix")
