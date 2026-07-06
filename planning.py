"""
planning.py
-----------
Planning-hygiene engines (PRD v3 FR-S3, §3.6) — all consumers are gated:
due-date slip metrics, start-date reschedule counts, missing-date checks.
Built dark; the Settings gates light them up with zero deploy.
"""

from __future__ import annotations

import datetime as dt

import settings as st


def _to_date(v):
    try:
        return dt.date.fromisoformat((v or "")[:10])
    except ValueError:
        return None


def slip_metrics(issue) -> dict | None:
    """Original due date = first value ever set (the first changelog change's
    'from' when present, else its 'to', else the current value if never changed).
    Push count = changes to a LATER date; slip days = current − original."""
    changes = [(ts, _to_date(frm), _to_date(to))
               for ts, _a, kind, frm, to in issue.field_events if kind == "duedate"]
    current = issue.duedate
    if not changes and not current:
        return None
    if changes:
        first_from, first_to = changes[0][1], changes[0][2]
        original = first_from or first_to
    else:
        original = current
    pushes = sum(1 for _ts, frm, to in changes if frm and to and to > frm)
    slip = (current - original).days if (current and original) else None
    return {"original": original, "current": current, "pushes": pushes, "slip_days": slip}


def reschedule_metrics(issue) -> dict:
    """Reschedule Count = number of start-date changes; Total Days Pushed =
    cumulative forward movement (PRD §3.6 — a prioritization signal)."""
    count, pushed = 0, 0
    for _ts, _a, kind, frm, to in issue.field_events:
        if kind != "startdate":
            continue
        f, t = _to_date(frm), _to_date(to)
        if t is None:
            continue
        count += 1
        if f and t > f:
            pushed += (t - f).days
    return {"count": count, "days_pushed": pushed}


def hygiene(issues, developer=None, match=None) -> dict:
    """FR-S3 tables. Empty dict values when the relevant gate is off — the
    screen then shows the teaching empty state instead."""
    dates_on = st.gate("due_dates_required") or st.gate("start_dates_required")
    est_on = st.gate("estimates_used")
    missing, slips, resched, no_est = [], [], [], []
    for i in issues:
        bucket = st.bucket_of(i.status, i.category)
        if bucket in ("done", None):
            continue
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        if dates_on and bucket in ("active_dev", "rework"):
            lack = []
            if st.gate("start_dates_required") and not i.start_date:
                lack.append("start date")
            if st.gate("due_dates_required") and not i.duedate:
                lack.append("due date")
            if lack:
                missing.append({"issue": i, "missing": ", ".join(lack)})
        if st.gate("due_dates_required"):
            sm = slip_metrics(i)
            if sm and (sm["pushes"] or (sm["slip_days"] or 0) > 0):
                slips.append({"issue": i, **sm})
        if st.gate("start_dates_required"):
            rm = reschedule_metrics(i)
            if rm["count"]:
                resched.append({"issue": i, **rm})
        if est_on and bucket == "active_dev" and not i.original_estimate and not i.story_points:
            no_est.append({"issue": i})
    slips.sort(key=lambda r: -(r["slip_days"] or 0))
    resched.sort(key=lambda r: -r["count"])
    return {"missing": missing, "slips": slips, "reschedules": resched, "no_estimate": no_est}
