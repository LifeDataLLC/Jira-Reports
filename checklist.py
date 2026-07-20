"""
checklist.py
------------
My Day checklist engine (PRD v3 FR-M1/M2/M4). For a developer + date + config,
returns one checklist row per ticket in active_dev/rework (plus qa_stage tickets
they own). Each check is a pure function over the activity feed and issue fields
returning "pass" / "fail" / "na" — gated checks return "na" when their gate is
off, and disabled checklist items are skipped entirely.
"""

from __future__ import annotations

import datetime as dt

import activity
import analytics as A
import settings as st

CHECK_ORDER = ["status_mapped", "comment_today", "due_date", "past_due", "has_release"]

CHECK_LABELS = {
    "status_mapped": "Status classified",
    "comment_today": "Comment today",
    "due_date": "Due date set",
    "past_due": "Past due date",
    "has_release": "Belongs to a release",
}


def _day_bounds(day: dt.date):
    d0 = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    return d0, d0 + dt.timedelta(days=1)


def _ago(ts, now=None) -> str:
    if not ts:
        return ""
    secs = ((now or A.now_utc()) - ts).total_seconds()
    if secs < 3600:
        return f"{max(int(secs // 60), 1)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _dur(days) -> str:
    """A span of days as a short duration: '45m', '3h', '2d 4h'."""
    if days is None:
        return ""
    hours = days * 24
    if hours < 1:
        return f"{max(int(round(hours * 60)), 1)}m"
    if hours < 24:
        return f"{int(round(hours))}h"
    d = int(hours // 24)
    h = int(round(hours - d * 24))
    return f"{d}d {h}h" if h else f"{d}d"


def _days_in_current_status(issue, now=None):
    entered = issue.status_events[-1][0] if issue.status_events else issue.created
    if not entered:
        return None
    return ((now or A.now_utc()) - entered).total_seconds() / 86400


def _is_flagged(issue) -> bool:
    state = False
    for _ts, _a, kind, _frm, to in issue.field_events:
        if kind == "flag":
            state = bool(to.strip())
    return state or any(l.lower() in {x.lower() for x in st.load()["blocked_labels"]}
                        for l in issue.labels)


def evaluate_ticket(issue, day: dt.date, now=None) -> dict:
    """Checklist row for one ticket on one day. Returns
    {issue, bucket, checks: [(id,label,state,why)], fails, eod_signal}."""
    s = st.load()
    gates, items = s["gates"], s["checklist_items"]
    d0, d1 = _day_bounds(day)
    events = activity.events_for(issue)
    today_events = [e for e in events if d0 <= e.ts < d1]
    bucket = st.bucket_of(issue.status, issue.category)
    checks = []

    def add(cid, state, why=""):
        if items.get(cid, True):
            checks.append((cid, CHECK_LABELS[cid], state, why))

    add("status_mapped", "pass" if bucket else "fail",
        "" if bucket else "status not classified in Settings")

    add("comment_today", "pass" if any(e.kind == "comment" for e in today_events) else "fail")

    if gates.get("due_dates_required"):
        add("due_date", "pass" if issue.duedate else "fail",
            "" if issue.duedate else "missing due date")
    else:
        add("due_date", "na", "due dates not required")

    # Past due: a due date exists and is in the past. Independent of the due-date
    # gate — if someone set a due date and it slipped, that's always worth a flag.
    today = (now or A.now_utc()).date()
    if issue.duedate is None:
        add("past_due", "na", "no due date set")
    elif issue.duedate < today:
        add("past_due", "fail", f"due {issue.duedate.isoformat()} — past due")
    else:
        add("past_due", "pass", f"due {issue.duedate.isoformat()}")

    # Every ticket must belong to a release (fixVersion).
    add("has_release", "pass" if issue.has_release else "fail",
        (", ".join(issue.fix_versions)) if issue.has_release
        else "not assigned to a feature/bug/backlog release")

    # Time in the current status: drives "stale" and, for active tickets, the
    # "how long it's been active" readout on the card.
    dsc = _days_in_current_status(issue, now)
    stale = dsc is not None and dsc >= s.get("stale_days", 10)
    active = st.is_active_status(issue.status)

    # Most recent action of any kind (status change incl. a handoff by another
    # developer, comment, worklog, field change) — drives the My Day ordering
    # and the date filter.
    last_activity = events[-1].ts if events else (issue.updated or issue.created)

    eod_signal = bool(today_events)
    return {"issue": issue, "bucket": bucket, "checks": checks,
            "active": active,
            "lane": st.lane_label(issue.status),
            "active_for": _dur(dsc) if (active and dsc is not None) else "",
            "stale": stale, "stale_days": round(dsc, 1) if dsc is not None else None,
            "last_activity": last_activity, "last_activity_str": _ago(last_activity, now),
            "fails": sum(1 for _c, _l, state, _w in checks if state == "fail"),
            "fail_ids": [c for c, _l, state, _w in checks if state == "fail"],
            "eod_signal": eod_signal}


def my_day(issues, developer, day: dt.date, match, now=None, show_all=False) -> dict:
    """Checklist rows for one developer's open, assigned work: anything in an
    active status (currently working), paused, in the QA pipeline, or reopened.
    To Do and Done are excluded.

    The rows are filtered to those that were edited on `day` — had a comment or a
    status change that day — EXCEPT tickets in an active status, which are always
    shown because they're what's being worked right now, even if they went active
    on an earlier day.

    With show_all=True the view instead lists EVERY open (non-done) ticket
    assigned to the developer — their whole workload, including To Do — ignoring
    both the bucket and date filters, so they can eyeball the status of everything."""
    d0, d1 = _day_bounds(day)
    rows = []
    for i in issues:
        b = st.bucket_of(i.status, i.category)
        if show_all:
            if b == "done" or i.category == "Done":
                continue
        # Unmapped statuses in Jira's own In Progress category still appear so the
        # developer sees the "status classified" failure (never silently dropped).
        elif b not in ("active_dev", "rework", "qa_stage", "paused") and not (
                b is None and i.category == "In Progress"):
            continue
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        r = evaluate_ticket(i, day, now)
        if not show_all and not (r["active"] or activity.edited_in_range(i, d0, d1)):
            continue
        rows.append(r)
    # Tickets in an active status ("currently working") are pinned to the top;
    # within each group, most recent action first (a handoff by anyone, or the
    # developer's own work).
    _min = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows.sort(key=lambda r: (r["active"], r["last_activity"] or _min), reverse=True)
    return {"rows": rows, "day": day,
            "total_fails": sum(r["fails"] for r in rows)}


def rollup(issues, day: dt.date, now=None) -> dict:
    """Admin roll-up (FR-M4): % of tickets in an ACTIVE or PAUSED status with an
    EOD signal on `day`, per developer and overall. Active = one of the blue
    "currently working" statuses; paused counts because pausing at end of day is
    itself the signal. Queue states (To Do, Ready for QA, etc.) are excluded —
    nobody is actively working them."""
    per_dev, total, signaled = {}, 0, 0
    for i in issues:
        if not (st.is_active_status(i.status)
                or st.bucket_of(i.status, i.category) == "paused"):
            continue
        r = evaluate_ticket(i, day, now)
        d = per_dev.setdefault(i.assignee, {"tickets": 0, "signaled": 0})
        d["tickets"] += 1
        total += 1
        if r["eod_signal"]:
            d["signaled"] += 1
            signaled += 1
    rows = [{"developer": dev, "tickets": v["tickets"], "signaled": v["signaled"],
             "pct": round(100 * v["signaled"] / v["tickets"]) if v["tickets"] else 0}
            for dev, v in sorted(per_dev.items())]
    return {"rows": rows, "total": total, "signaled": signaled,
            "pct": round(100 * signaled / total) if total else 0, "day": day}
