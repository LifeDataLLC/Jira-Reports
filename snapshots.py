"""
snapshots.py
------------
SQLite snapshot store (PRD v3 FR-X3): one row of team aggregates per day,
enabling week-over-week trends. Written by the /tasks/snapshot endpoint
(container-friendly — hit it from cron / an Azure WebJob / Logic App).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

import analytics as A
import attention
import checklist
import flow_quality as fq
import settings as st

DB_PATH = os.environ.get("SNAPSHOT_DB_PATH") or os.path.join(st.data_dir(), "snapshots.db")


def _conn(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS snapshots (day TEXT PRIMARY KEY, data TEXT)")
    return conn


def compute_aggregates(issues, now=None) -> dict:
    """FR-X1 team aggregates — no individual names anywhere in this dict."""
    now = now or A.now_utc()
    roll = checklist.rollup(issues, now.date(), now=now)
    rows = fq.cycle_rows(issues, start=now - dt.timedelta(days=30))
    stats = fq.cycle_stats(rows)
    trend = fq.return_trend(issues, weeks=2)
    handoffs = sum(w["handoffs"] for w in trend)
    returns = sum(w["returns"] for w in trend)
    blocked_days = []
    blocked_count = 0
    for i in issues:
        if st.bucket_of(i.status, i.category) == "done":
            continue
        state, ts = False, None
        for ets, _a, kind, _f, to in i.field_events:
            if kind == "flag":
                state = bool(to.strip())
                ts = ets if state else None
        if state:
            blocked_count += 1
            if ts:
                blocked_days.append((now - ts).total_seconds() / 86400)
    board = attention.board(issues, now=now)
    med_blocked = A.percentile(blocked_days, 50)
    return {
        "eod_signal_pct": roll["pct"], "eod_total": roll["total"],
        "cycle_median_h": stats["cycle"]["median"], "cycle_n": stats["cycle"]["n"],
        "return_rate_pct": round(100 * returns / handoffs) if handoffs else None,
        "handoffs": handoffs, "returns": returns,
        "blocked_count": blocked_count,
        "blocked_median_days": round(med_blocked, 1) if med_blocked is not None else None,
        "attention_size": len(board["rows"]),
    }


def take(issues, day=None, path=None, now=None) -> dict:
    day = (day or A.now_utc().date()).isoformat()
    data = compute_aggregates(issues, now)
    with _conn(path) as c:
        c.execute("INSERT OR REPLACE INTO snapshots (day, data) VALUES (?, ?)",
                  (day, json.dumps(data)))
    return data


def series(days=60, path=None) -> list[dict]:
    with _conn(path) as c:
        rows = c.execute("SELECT day, data FROM snapshots ORDER BY day DESC LIMIT ?",
                         (days,)).fetchall()
    return [{"day": d, **json.loads(j)} for d, j in rows]


def week_over_week(path=None) -> dict:
    """Latest snapshot vs the closest one ~7 days earlier: {metric: (now, delta)}."""
    s = series(30, path)
    if not s:
        return {}
    latest = s[0]
    latest_day = dt.date.fromisoformat(latest["day"])
    prior = None
    for row in s[1:]:
        if (latest_day - dt.date.fromisoformat(row["day"])).days >= 7:
            prior = row
            break
    if prior is None and len(s) > 1:
        prior = s[-1]
    out = {}
    for k, v in latest.items():
        if k == "day":
            continue
        p = prior.get(k) if prior else None
        delta = round(v - p, 1) if (isinstance(v, (int, float)) and isinstance(p, (int, float))) else None
        out[k] = {"now": v, "delta": delta}
    return out
