"""Student identity for graded use.

A `?sid=` value in a URL is only a label — anyone can type someone else's. For graded
assignments an instructor can turn on SIGNED ASSIGNMENT LINKS:

  * set the secret ASSIGNMENT_SIGNING_KEY (environment variable or Streamlit secret);
  * mint one link per student with tools/make_assignment_links.py (or `make_token`);
  * the link carries `?game=<code>&sid=<id>&tok=<signed token>`; the app accepts the identity
    only when the HMAC-SHA256 signature matches game+sid (and has not expired).

With no key configured nothing changes for standalone practice, and a legacy `?sid=` link
still works but is labelled "not verified" everywhere (including the PDF report).

The same key can also SEAL a submitted report (`seal`): an instructor holding the key can
check that a report's integrity hash was produced by this deployment. The seal is not a
defence against a student editing their own PDF text; the verifiable record is the stored
completion record (attempt id + snapshot hash)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

TOKEN_VERSION = 1


def _cfg(name: str) -> Optional[str]:
    val = os.environ.get(name)
    if val:
        return val
    try:                                                    # Streamlit secrets, if available
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return None


def signing_key() -> Optional[bytes]:
    k = _cfg("ASSIGNMENT_SIGNING_KEY")
    return k.encode("utf-8") if k else None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(key: bytes, game: str, sid: str, expires_at: Optional[int] = None) -> str:
    payload = {"v": TOKEN_VERSION, "g": game or "", "s": sid or "", "x": expires_at}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


@dataclass
class TokenCheck:
    valid: bool
    reason: str = ""


def verify_token(key: Optional[bytes], token: Optional[str], game: Optional[str],
                 sid: Optional[str], now: Optional[float] = None) -> TokenCheck:
    if not key:
        return TokenCheck(False, "Signed links are not enabled on this server.")
    if not token:
        return TokenCheck(False, "Your link has no sign-in token.")
    try:
        body, sig = token.split(".", 1)
        want = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return TokenCheck(False, "The sign-in token is not valid for this assignment.")
        payload = json.loads(_unb64(body))
    except Exception:
        return TokenCheck(False, "The sign-in token is malformed.")
    if payload.get("v") != TOKEN_VERSION:
        return TokenCheck(False, "The sign-in token is from an unsupported version.")
    if payload.get("g") != (game or "") or payload.get("s") != (sid or ""):
        return TokenCheck(False, "The sign-in token belongs to a different student or assignment.")
    exp = payload.get("x")
    if exp is not None and (now if now is not None else time.time()) > float(exp):
        return TokenCheck(False, "The sign-in link has expired — ask your instructor for a new one.")
    return TokenCheck(True, "")


def seal(key: Optional[bytes], snapshot_hash: str, attempt_id: str) -> str:
    """Short HMAC over the report's integrity hash. '' when no key is configured."""
    if not key:
        return ""
    msg = f"{attempt_id}|{snapshot_hash}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:20]


def verify_seal(key: Optional[bytes], snapshot_hash: str, attempt_id: str, value: str) -> bool:
    return bool(key) and bool(value) and hmac.compare_digest(seal(key, snapshot_hash, attempt_id), value)


@dataclass
class Identity:
    """Who the app believes the student is, and how sure it is."""
    game: Optional[str]
    sid: Optional[str]
    verified: bool            # signed link checked out
    mode: str                 # 'signed' | 'unverified' | 'practice' | 'blocked'
    message: str = ""

    @property
    def recorded(self) -> bool:
        """May progress/completion be written to the store for this identity?"""
        return self.mode in ("signed", "unverified") and bool(self.sid)

    @property
    def label(self) -> str:
        return {"signed": "Verified assignment link", "unverified": "Student ID (not verified)",
                "practice": "Practice mode (nothing is recorded)",
                "blocked": "Sign-in required"}[self.mode]


def resolve_identity(game: Optional[str], sid: Optional[str], token: Optional[str],
                     store_enabled: bool, practice: bool = False) -> Identity:
    key = signing_key()
    if practice:
        return Identity(game, None, False, "practice", "Practice mode — nothing is saved to the server.")
    if key and game:
        chk = verify_token(key, token, game, sid)
        if chk.valid:
            return Identity(game, sid, True, "signed")
        return Identity(game, sid, False, "blocked", chk.reason)
    if sid:
        return Identity(game, sid, False, "unverified",
                        "This is a typed student ID. It is not verified."
                        if store_enabled else "")
    return Identity(game, None, False, "practice" if not store_enabled else "unverified")
