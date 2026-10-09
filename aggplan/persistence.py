"""Versioned progress persistence with visible save status and a local recovery file.

A saved record is:  {"_schema": 3, "_meta": {...}, "pg": {<all student work>}}
`_meta` carries the model version, scenario seed lineage and the hash of the
instructor-controlled configuration. Progress is only restored automatically when all of
these are compatible with the running app; otherwise the student is told why and chooses
between starting fresh and downloading the old record — nothing is silently reused, and
nothing is silently overwritten.

Remote saving goes through `student_store` (the shared Dropbox/Fernet module, which stays
byte-for-byte identical to the Director's copy). Failures are classified into actionable
messages (never including secrets) and never interrupt the student's work."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from . import MODEL_VERSION
from .scenario import Scenario

SCHEMA = 3
RETRY_SECONDS = 30


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, pd.DataFrame):
        return {"__df__": json_safe(v.to_dict("list")), "__index__": [int(i) for i in v.index]}
    if isinstance(v, (set, frozenset)):
        return sorted(json_safe(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if hasattr(v, "tolist"):
        try:
            return json_safe(v.tolist())
        except Exception:
            pass
    if isinstance(v, float):
        return None if (v != v or v in (float("inf"), float("-inf"))) else v
    if isinstance(v, int):
        return v
    return str(v)


def df_from_json(d: dict) -> pd.DataFrame:
    df = pd.DataFrame(d["__df__"])
    if "__index__" in d and len(d["__index__"]) == len(df):
        df.index = d["__index__"]
    return df


def restore_dataframes(obj: Any) -> Any:
    """Recursively turn {'__df__': …} markers back into DataFrames."""
    if isinstance(obj, dict):
        if "__df__" in obj:
            return df_from_json(obj)
        return {k: restore_dataframes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore_dataframes(v) for v in obj]
    return obj


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Meta / compatibility
# --------------------------------------------------------------------------- #
def _major_minor(v: str) -> str:
    return ".".join(str(v).split(".")[:2])


def build_meta(scn: Scenario, identity_mode: str = "") -> dict:
    return {"model_version": MODEL_VERSION, "schema": SCHEMA, "seed": scn.seed,
            "base_seed": scn.base_seed, "variant": scn.variant, "preset": scn.preset,
            "instructor_config_hash": scn.instructor_config_hash,
            "scenario_config_hash": scn.config_hash, "identity_mode": identity_mode,
            "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "resolved_params": json_safe(scn.params), "fixed_demand": scn.fixed_demand}


@dataclass
class Compat:
    ok: bool
    reason: str = ""


def check_compatibility(saved: Optional[dict], current: Scenario) -> Compat:
    """May this saved record be restored under the running app/configuration?
    NOTE: `current` is built with the saved base seed, so the seed itself is not compared;
    parameter/fixed-demand changes and model changes are."""
    if not saved:
        return Compat(True)
    if "_schema" not in saved or "_meta" not in saved:
        return Compat(False, "This progress was saved by an earlier version of the simulation "
                      "(before saved progress was versioned), so its numbers cannot be trusted "
                      "under the current model.")
    meta = saved["_meta"]
    if saved.get("_schema") != SCHEMA:
        return Compat(False, f"Saved progress format {saved.get('_schema')} differs from the "
                      f"current format {SCHEMA}.")
    if _major_minor(meta.get("model_version", "")) != _major_minor(MODEL_VERSION):
        return Compat(False, f"Progress was saved with model version {meta.get('model_version')}; "
                      f"this app runs {MODEL_VERSION}, and the calculation rules changed.")
    if meta.get("instructor_config_hash") != current.instructor_config_hash:
        return Compat(False, "The assignment settings (costs, capacity, policies or demand) "
                      "changed since you saved, so your saved worksheets no longer match the "
                      "scenario.")
    return Compat(True)


def saved_base_seed(saved: Optional[dict]) -> Optional[Tuple[int, int]]:
    try:
        m = saved["_meta"]
        return int(m["base_seed"]), int(m.get("variant", 0))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Snapshots and recovery files
# --------------------------------------------------------------------------- #
def make_snapshot(pg: dict, scn: Scenario, identity_mode: str = "") -> dict:
    return {"_schema": SCHEMA, "_meta": build_meta(scn, identity_mode),
            "pg": json_safe(pg)}


def content_blob(pg: dict) -> str:
    """Change-detection key: only the student's work, not the save timestamp."""
    return _canon(json_safe(pg))


def recovery_bytes(snapshot: dict) -> bytes:
    """A local recovery file: the snapshot + a checksum so a damaged file is detected.
    Contains the student's own work only — no tokens, keys or credentials."""
    body = {"kind": "aggregate-anxiety-recovery", "snapshot": snapshot}
    body["checksum"] = hashlib.sha256(_canon(snapshot).encode()).hexdigest()
    return json.dumps(body, indent=1, ensure_ascii=False).encode("utf-8")


def parse_recovery(data: bytes) -> Tuple[Optional[dict], str]:
    try:
        body = json.loads(data.decode("utf-8"))
        if body.get("kind") != "aggregate-anxiety-recovery":
            return None, "This isn't an Aggregate Anxiety recovery file."
        snap = body["snapshot"]
        if hashlib.sha256(_canon(snap).encode()).hexdigest() != body.get("checksum"):
            return None, "The recovery file looks damaged or edited (checksum mismatch)."
        return snap, ""
    except Exception:
        return None, "That file could not be read as a recovery file."


# --------------------------------------------------------------------------- #
# Save status
# --------------------------------------------------------------------------- #
def classify_error(exc: BaseException) -> str:
    """Actionable, secret-free explanation of a remote-save failure."""
    name = type(exc).__name__
    text = f"{name} {exc}".lower()
    if any(k in text for k in ("autherror", "unauthorized", "invalid_access_token",
                               "expired_access_token", "401", "invalidtoken")):
        return ("The save service rejected the app's credentials. This isn't something you can "
                "fix — tell your instructor. Your work is still in this tab.")
    if any(k in text for k in ("connection", "timeout", "timed out", "temporarily", "resolve",
                               "ssl", "network", "unreachable")):
        return ("The save service can't be reached right now (network problem). Keep this tab "
                "open — the app will retry. Download a recovery file below if you need to leave.")
    if any(k in text for k in ("ratelimit", "429", "too_many")):
        return "The save service is busy. The app will retry automatically in a moment."
    if "modulenotfound" in text or "importerror" in text:
        return ("The save service isn't installed on this server (missing dependency). Tell your "
                "instructor; use the recovery download to keep your work.")
    if "insufficient_space" in text or "quota" in text:
        return "The save service is out of space. Tell your instructor; download a recovery file."
    return (f"Saving failed ({name}). Your work is still in this tab; download a recovery file "
            "and tell your instructor if it keeps happening.")


@dataclass
class SaveStatus:
    state: str = "off"            # off | pending | saved | failed
    last_ok: Optional[float] = None
    last_error: str = ""
    last_attempt: float = 0.0
    failures: int = 0
    saves: int = 0

    def label(self) -> str:
        if self.state == "off":
            return "Progress is kept in this browser tab only (no server saving)."
        if self.state == "failed":
            when = (f" Last successful save {_fmt_time(self.last_ok)}." if self.last_ok
                    else " Nothing has been saved yet.")
            return f"⚠ Not saved. {self.last_error}{when}"
        if self.state == "saved":
            return f"✔ Saved at {_fmt_time(self.last_ok)}"
        return "… Saving"

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "SaveStatus":
        s = SaveStatus()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


def _fmt_time(ts: Optional[float]) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"


def save_remote(store, game, sid, snapshot: dict, status: SaveStatus, *, progress: float = 0.0,
                step: str = "", force: bool = False, now: Optional[float] = None) -> SaveStatus:
    """Try one save. Never raises. Throttles retries after a failure unless `force`."""
    now = now if now is not None else time.time()
    if not (store.enabled() and sid):
        status.state = "off"
        return status
    if (status.state == "failed" and not force and now - status.last_attempt < RETRY_SECONDS):
        return status
    status.last_attempt = now
    try:
        ok = store.save(game, sid, snapshot, progress=progress, step=step)
        if ok is False:
            raise RuntimeError("storage disabled")
        status.state, status.last_ok, status.last_error = "saved", now, ""
        status.saves += 1
    except BaseException as e:                                  # noqa: BLE001 — never interrupt the student
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        status.state = "failed"
        status.failures += 1
        status.last_error = classify_error(e)
    return status


def load_remote(store, game, sid) -> Tuple[Optional[dict], str]:
    """(record, error_message). An empty record is (None, '')."""
    if not (store.enabled() and sid):
        return None, ""
    try:
        d = store.load(game, sid)
    except BaseException as e:                                  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return None, classify_error(e)
    return (d or None), ""


def record_completion_safe(store, game, sid, attempt_id: str, extra: dict) -> Tuple[bool, str]:
    """Write the completion record AFTER the report exists. Never raises; returns
    (ok, message). `score` stays None: plan cost goes in `extra` and is labelled as plan cost,
    never as a grade. The attempt id is the completion code the Director's roster shows."""
    if not (store.enabled() and sid):
        return False, "Not recorded on the server (practice mode or server saving is off)."
    try:
        ok = store.record_completion(game, sid, completion_code=attempt_id, score=None,
                                     extra=json_safe(extra))
        if ok is False:
            return False, "The server declined to record the completion."
        return True, "Completion recorded on the server."
    except BaseException as e:                                  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False, classify_error(e)
