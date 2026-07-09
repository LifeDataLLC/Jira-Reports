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

CHECK_ORDER = ["status_mapped", "comment_today", "worklog_today", "due_date",
               "has_release", "eod_pause", "not_over_threshold", "start_date",
               "handoff_comment", "blocked_reason"]

CHECK_LABELS = {
    "status_mapped": "Status classified",
    "comment_today": "Comment today",
    "worklog_today": "Worklog today",
    "start_date": "Start date OK",
    "due_date": "Due date set",
    "has_release": "Belongs to a release",
    "eod_pause": "Paused for end of day",
    "not_over_threshold": "Within aging threshold",
    "handoff_comment": "Handoff comment",
    "blocked_reason": "Blocked reason",
}


def _day_bounds(day: dt.date):
    d0 = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    return d0, d0 + dt.timedelta(days=1)


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

    if gates.get("worklogs_required"):
        add("worklog_today", "pass" if any(e.kind == "worklog" for e in today_events) else "fail")
    else:
        add("worklog_today", "na", "worklogs not required")

    if gates.get("start_dates_required"):
        sd = issue.start_date
        if not sd:
            add("start_date", "fail", "missing start date")
        elif bucket in ("todo",) and sd < day:
            add("start_date", "fail", "planned start in the past")
        else:
            add("start_date", "pass")
    else:
        add("start_date", "na", "start dates not required")

    if gates.get("due_dates_required"):
        add("due_date", "pass" if issue.duedate else "fail",
            "" if issue.duedate else "missing due date")
    else:
        add("due_date", "na", "due dates not required")

    # Rule 5: every ticket must belong to a release (fixVersion).
    add("has_release", "pass" if issue.has_release else "fail",
        (", ".join(issue.fix_versions)) if issue.has_release
        else "not assigned to a feature/bug/backlog release")

    # Rule 3: pause the active ticket at end of day.
    if st.is_active_status(issue.status):
        entered = issue.status_events[-1][0] if issue.status_events else issue.created
        carried = bool(entered and entered.date() < (now or A.now_utc()).date())
        pause = st.pause_for(issue.status) or "its paused status"
        if carried:
            add("eod_pause", "fail", f"left active overnight — move to {pause} at end of day")
        else:
            add("eod_pause", "na", f"move to {pause} before you sign off")
    else:
        add("eod_pause", "na", "not an active status")

    thr = st.threshold_for(issue.status)
    days_in = _days_in_current_status(issue, now)
    if thr is None or days_in is None:
        add("not_over_threshold", "na", "no threshold for this status")
    else:
        over = days_in - thr
        add("not_over_threshold", "pass" if over <= 0 else "fail",
            f"{days_in:.1f}d in status (limit {thr:g}d)" if over > 0 else "")

    moved_to_qa = [e for e in today_events if e.kind == "status"
                   and st.bucket_of(e.to) == "qa_stage"]
    if moved_to_qa:
        win = dt.timedelta(hours=s["handoff_window_hours"])
        ok = False
        for m in moved_to_qa:
            ok = ok or any(e.kind == "comment"
                           and (e.actor_id == m.actor_id or e.actor == m.actor)
                           and m.ts - win <= e.ts <= m.ts + dt.timedelta(minutes=10)
                           for e in events)
        add("handoff_comment", "pass" if ok else "fail",
            "" if ok else "moved to QA without a handoff comment")
    else:
        add("handoff_comment", "na", "not moved to QA today")

    if _is_flagged(issue):
        flag_ts = max((ts for ts, _a, k, _f, to in issue.field_events
                       if k == "flag" and to.strip()), default=None)
        has_reason = any(e.kind == "comment" and (flag_ts is None or e.ts >= flag_ts - dt.timedelta(hours=1))
                         for e in events)
        add("blocked_reason", "pass" if has_reason else "fail",
            "" if has_reason else "blocked without a reason comment")
    else:
        add("blocked_reason", "na", "not blocked")

    eod_signal = bool(today_events)
    return {"issue": issue, "bucket": bucket, "checks": checks,
            "active": st.is_active_status(issue.status),
            "fails": sum(1 for _c, _l, state, _w in checks if state == "fail"),
            "eod_signal": eod_signal}


def my_day(issues, developer, day: dt.date, match, now=None) -> dict:
    """Checklist rows for one developer's IN-FLIGHT tickets — their open, assigned
    work: anything in an active status (currently working), paused, in the QA
    pipeline, or reopened. To Do and Done are excluded."""
    rows = []
    for i in issues:
        b = st.bucket_of(i.status, i.category)
        # Unmapped statuses in Jira's own In Progress category still appear so the
        # developer sees the "status classified" failure (never silently dropped).
        if b not in ("active_dev", "rework", "qa_stage", "paused") and not (
                b is None and i.category == "In Progress"):
            continue
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        rows.append(evaluate_ticket(i, day, now))
    rows.sort(key=lambda r: (-r["fails"], r["issue"].key))
    return {"rows": rows, "day": day,
            "total_fails": sum(r["fails"] for r in rows)}


def rollup(issues, day: dt.date, now=None) -> dict:
    """Admin roll-up (FR-M4): % of tickets in an ACTIVE or PAUSED status with an
    EOD signal on `day`, per developer and overall. Active = one of the 5 blue
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
