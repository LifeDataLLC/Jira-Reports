"""
attention.py
------------
Attention Board engine (PRD v3 FR-A1/A2): every non-done ticket evaluated
against reason rules; reasons stack per ticket; severity = max(days over
threshold). Phase 1 ships Silent + Aging; Overdue / Blocked / Missing dates
are added by Phase 4 behind their gates.
"""

from __future__ import annotations

import datetime as dt

import activity
import analytics as A
import settings as st


def _reasons_for(issue, now) -> list[dict]:
    """[{tag, days, severity}] — severity is days-over-threshold for sorting."""
    s = st.load()
    reasons = []
    bucket = st.bucket_of(issue.status, issue.category)

    # Silent: no activity-feed event in N days while actively working (any active
    # lane — dev or a testing lane) or in rework (FR-A1).
    if bucket in ("active_dev", "rework") or st.is_active_status(issue.status):
        last = activity.last_event_ts(issue)
        if last:
            silent_days = (now - last).total_seconds() / 86400
            n = s["silent_days"]
            if silent_days >= n:
                reasons.append({"tag": f"Silent {silent_days:.0f}d",
                                "kind": "silent", "severity": silent_days - n})

    # Rule 3: active ticket not paused (left in an active status overnight).
    if st.is_active_status(issue.status):
        entered = issue.status_events[-1][0] if issue.status_events else issue.created
        if entered and entered.date() < now.date():
            days = (now - entered).total_seconds() / 86400
            reasons.append({"tag": f"Not paused {days:.0f}d",
                            "kind": "not_paused", "severity": days})

    # Rule 5: work in flight with no release assigned.
    if bucket in ("active_dev", "rework", "qa_stage") and not issue.has_release:
        reasons.append({"tag": "No release", "kind": "no_release", "severity": 0.5})

    # Aging: over the per-status threshold
    thr = st.threshold_for(issue.status)
    if thr is not None:
        entered = issue.status_events[-1][0] if issue.status_events else issue.created
        if entered:
            days_in = (now - entered).total_seconds() / 86400
            if days_in > thr:
                reasons.append({"tag": f"Aging {days_in:.0f}d",
                                "kind": "aging", "severity": days_in - thr})

    # Overdue (gated on due dates — Phase 4 lights this up via the gate)
    if st.gate("due_dates_required") and issue.duedate and issue.duedate < now.date():
        over = (now.date() - issue.duedate).days
        reasons.append({"tag": f"Overdue {over}d", "kind": "overdue", "severity": float(over)})

    # Blocked: Jira Flagged field primary; labels are low-confidence hints
    flag_state, flag_ts = False, None
    for ts, _a, kind, _f, to in issue.field_events:
        if kind == "flag":
            flag_state = bool(to.strip())
            flag_ts = ts if flag_state else None
    if flag_state:
        days_b = (now - flag_ts).total_seconds() / 86400 if flag_ts else 0
        reasons.append({"tag": f"Blocked {days_b:.0f}d", "kind": "blocked", "severity": days_b})
    elif any(l.lower() in {x.lower() for x in s["blocked_labels"]} for l in issue.labels):
        reasons.append({"tag": "Blocked (label)", "kind": "blocked_hint", "severity": 0.5})

    # Missing dates (gated)
    missing = []
    if st.gate("start_dates_required") and bucket in ("active_dev", "rework") and not issue.start_date:
        missing.append("start")
    if st.gate("due_dates_required") and bucket in ("active_dev", "rework") and not issue.duedate:
        missing.append("due")
    if missing:
        reasons.append({"tag": f"Missing dates ({'/'.join(missing)})",
                        "kind": "dates", "severity": 1.0})

    return reasons


def disposition_state(issue, now) -> dict | None:
    """Needs-disposition rule (FR-A3, PRD §3.6): a ticket over its aging threshold
    must move to Backlog (todo bucket) or get a future start date within 48h of
    crossing the threshold. Computed statelessly from the changelog."""
    thr = st.threshold_for(issue.status)
    if thr is None:
        return None
    entered = issue.status_events[-1][0] if issue.status_events else issue.created
    if not entered:
        return None
    crossed = entered + dt.timedelta(days=thr)
    if now < crossed:
        return None
    # Dispositioned when, after crossing: moved to todo bucket, or start date set future.
    for ts, _a, _id, _f, to in issue.status_events:
        if ts >= crossed and st.bucket_of(to) == "todo":
            return {"state": "dispositioned", "within_48h": (ts - crossed).total_seconds() <= 48 * 3600}
    for ts, _a, kind, _f, to in issue.field_events:
        if kind == "startdate" and ts >= crossed:
            try:
                if dt.date.fromisoformat(to[:10]) > now.date():
                    return {"state": "dispositioned", "within_48h": (ts - crossed).total_seconds() <= 48 * 3600}
            except ValueError:
                continue
    hours_open = (now - crossed).total_seconds() / 3600
    return {"state": "needs_disposition", "hours_open": hours_open,
            "overdue_48h": hours_open > 48}


def disposition_compliance(issues, now=None) -> dict:
    """FR-A3: of tickets that crossed their aging threshold, % dispositioned
    (moved to Backlog or given a future start date) within 48h."""
    now = now or A.now_utc()
    flagged = dispositioned = within = 0
    for i in issues:
        if st.bucket_of(i.status, i.category) == "done":
            continue
        d = disposition_state(i, now)
        if not d:
            continue
        flagged += 1
        if d["state"] == "dispositioned":
            dispositioned += 1
            if d["within_48h"]:
                within += 1
    return {"flagged": flagged, "dispositioned": dispositioned, "within_48h": within,
            "pct": round(100 * within / flagged) if flagged else None}


def board(issues, developer=None, reason_filter=None, match=None, now=None) -> dict:
    now = now or A.now_utc()
    rows = []
    for i in issues:
        bucket = st.bucket_of(i.status, i.category)
        if bucket == "done":
            continue
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        reasons = _reasons_for(i, now)
        if not reasons:
            continue
        if reason_filter and not any(r["kind"] == reason_filter or
                                     r["kind"].startswith(reason_filter) for r in reasons):
            continue
        rows.append({"issue": i, "reasons": reasons,
                     "severity": max(r["severity"] for r in reasons)})
    rows.sort(key=lambda r: -r["severity"])
    kinds = sorted({r["kind"].replace("blocked_hint", "blocked")
                    for row in rows for r in row["reasons"]})
    return {"rows": rows, "kinds": kinds}
