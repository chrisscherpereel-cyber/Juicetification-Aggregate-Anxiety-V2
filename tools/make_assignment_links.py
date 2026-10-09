#!/usr/bin/env python
"""Mint signed assignment links (one per student) for graded use.

  export ASSIGNMENT_SIGNING_KEY='a long random secret'      # same value as on the server
  python tools/make_assignment_links.py --base-url https://your-app.streamlit.app \
         --game OPS301-F26 --roster roster.csv [--expires 2026-12-20] > links.csv

roster.csv has one student ID per line (or a column named `sid`; other columns are copied).
Output CSV: the roster columns plus `link`. A link carries ?game=&sid=&tok=; the app accepts
the identity only if the HMAC-SHA256 token matches game+sid (and has not expired).
Treat links like passwords: whoever holds one can act as that student.

Also: `--verify-seal ATTEMPT_ID SNAPSHOT_HASH SEAL` checks a report seal printed in a PDF /
stored in a completion record (needs the same key)."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aggplan import identity  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url")
    ap.add_argument("--game")
    ap.add_argument("--roster")
    ap.add_argument("--expires", help="YYYY-MM-DD (end of that day, UTC); default: no expiry")
    ap.add_argument("--verify-seal", nargs=3, metavar=("ATTEMPT_ID", "SNAPSHOT_HASH", "SEAL"))
    a = ap.parse_args()
    key = os.environ.get("ASSIGNMENT_SIGNING_KEY", "").encode()
    if not key:
        print("Set ASSIGNMENT_SIGNING_KEY in the environment first.", file=sys.stderr)
        return 2
    if a.verify_seal:
        att, h, seal = a.verify_seal
        ok = identity.verify_seal(key, h, att, seal)
        print("VALID seal" if ok else "INVALID seal")
        return 0 if ok else 1
    if not (a.base_url and a.game and a.roster):
        ap.error("--base-url, --game and --roster are required")
    exp = None
    if a.expires:
        d = dt.datetime.strptime(a.expires, "%Y-%m-%d").replace(hour=23, minute=59, second=59,
                                                                tzinfo=dt.timezone.utc)
        exp = int(d.timestamp())
    with open(a.roster, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header = rows[0] if rows and rows[0] and rows[0][0].lower() in ("sid", "student", "id") else None
    body = rows[1:] if header else rows
    sid_col = 0
    if header and "sid" in [h.lower() for h in header]:
        sid_col = [h.lower() for h in header].index("sid")
    out = csv.writer(sys.stdout, lineterminator="\n")
    out.writerow((header or ["sid"]) + ["link"])
    for r in body:
        if not r or not r[sid_col].strip():
            continue
        sid = r[sid_col].strip()
        tok = identity.make_token(key, a.game, sid, exp)
        q = urllib.parse.urlencode({"game": a.game, "sid": sid, "tok": tok})
        out.writerow(r + [f"{a.base_url.rstrip('/')}/?{q}"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
