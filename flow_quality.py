"""
flow_quality.py
---------------
Engines for the Flow Analytics and Quality screens (PRD v3 FR-F1–F6, FR-QL1–QL3).
All durations are median/p85 with raw counts (PRD §3.5); buckets come from
settings so workflow changes never touch this code.
"""

from __future__ import annotations

import datetime as dt

import analytics as A
import qa_handoff as qh
import settings as st
import workflow


def _first_entry(issue, buckets, after=None) -> dt.datetime | None:
    """First transition into any of `buckets`; with `after`, the first one at or
    after that time — so a ticket that visited QA before development ever began
    (e.g. created straight into QA, then pulled back) can't produce a negative
    dev→QA or cycle duration."""
    for ts, _a, _id, _f, to in issue.status_events:
        if st.bucket_of(to) in buckets and (after is None or ts >= after):
            return ts
    return None


def cycle_rows(issues, developer=None, start=None, end=None, match=None) -> list[dict]:
    """Per-ticket cycle data + per-bucket time shares for the stacked bars (FR-F2)."""
    rows = []
    for i in issues:
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        dev_start = _first_entry(i, {"active_dev"})
        if not dev_start:
            continue
        rfqa = _first_entry(i, {"qa_stage"}, after=dev_start)
        done = _first_entry(i, {"done"}, after=dev_start) \
            or (i.resolved if i.resolved and i.resolved >= dev_start else None)
        anchor = done or rfqa or dev_start
        if (start and anchor < start) or (end and anchor >= end):
            continue
        per_bucket = {}
        for status, enter, exit_ in i.timeline.segments:
            b = st.bucket_of(status)
            if b and b != "todo":
                per_bucket[b] = per_bucket.get(b, 0) + (exit_ - enter).total_seconds()
        total = sum(per_bucket.values()) or 1
        segs = [{"bucket": b, "days": round(s / 86400, 1), "pct": round(100 * s / total, 1)}
                for b, s in sorted(per_bucket.items(), key=lambda kv: -kv[1]) if s > 0]
        rows.append({
            "issue": i, "dev_start": dev_start, "rfqa": rfqa, "done": done,
            "dev_to_qa_h": round((rfqa - dev_start).total_seconds() / 3600, 1) if rfqa else None,
            "cycle_h": round((done - dev_start).total_seconds() / 3600, 1) if done else None,
            "rework_loops": i.timeline.reopened_count,
            "segments": segs,
        })
    rows.sort(key=lambda r: r["dev_start"], reverse=True)
    return rows


def cycle_stats(rows) -> dict:
    """Median/p85 + counts for the two cycle durations (FR-F1)."""
    d2q = [r["dev_to_qa_h"] for r in rows if r["dev_to_qa_h"] is not None]
    cyc = [r["cycle_h"] for r in rows if r["cycle_h"] is not None]
    def f(v):
        return round(v, 1) if v is not None else None
    return {"dev_to_qa": {"median": f(A.percentile(d2q, 50)), "p85": f(A.percentile(d2q, 85)),
                          "n": len(d2q)},
            "cycle": {"median": f(A.percentile(cyc, 50)), "p85": f(A.percentile(cyc, 85)),
                      "n": len(cyc)}}


def ownership_spans(issue) -> list[tuple]:
    """(owner, enter, exit) spans reconstructed from the assignee changelog —
    who held the ticket when, same segment-rebuilding shape analytics.analyze
    uses for status.

    The changelog only records display NAMES for historical assignees (no
    accountIds), so spans are keyed by name; only the current assignee can be
    matched by id. Unassigned stretches are kept as "Unassigned" rather than
    dropped, so a ticket's spans always tile its whole life."""
    end_cap = issue.resolved or A.now_utc()
    events = issue.assignee_events
    if not events:
        return [(issue.assignee, issue.created, end_cap)] if issue.created else []
    spans = []
    cur = events[0][2] or "Unassigned"          # fromString of the first change
    seg_start = issue.created or events[0][0]
    for ts, _author, _frm, to in events:
        if ts >= seg_start:
            spans.append((cur, seg_start, ts))
        cur, seg_start = (to or "Unassigned"), ts
    if end_cap >= seg_start:
        spans.append((cur, seg_start, end_cap))
    return spans


def ticket_active_blocks(issue, start=None, end=None) -> list[tuple]:
    """(owner, status, enter, exit) — active status segments intersected with
    the ownership spans, in time order.

    The shared primitive behind both the per-person totals and the ownership
    timeline, so the summed hours and the drawn bands can never disagree."""
    out = []
    spans = ownership_spans(issue)
    for status, s_in, s_out in issue.timeline.segments:
        if not st.is_active_status(status):
            continue
        a = max(s_in, start) if start else s_in
        b = min(s_out, end) if end else s_out
        if b <= a:
            continue
        for owner, o_in, o_out in spans:
            if not (o_in and o_out):
                continue
            lo, hi = max(a, o_in), min(b, o_out)
            if hi > lo:
                out.append((owner, status, lo, hi))
    out.sort(key=lambda b: b[2])
    return out


def ticket_active_time(issue, start=None, end=None, ids_by_name=None) -> list[dict]:
    """One ticket's active-status seconds, split by who OWNED it at the time.

    A ticket handed from one developer to another credits each with the
    stretch they actually held it — the current assignee doesn't inherit
    everyone else's work. Keeps the per-status split per person too, so the
    view can show what kind of work each stretch was.

    `ids_by_name` maps display name -> accountId (auth.all_developers). The
    changelog names historical assignees but never gives their accountId, so
    without it a past owner's rows carry no id — and a developer filter built
    from the dropdown (which supplies accountIds) would silently drop a
    person's own work on any ticket they have since handed off."""
    per = {}
    for owner, status, lo, hi in ticket_active_blocks(issue, start, end):
        secs = (hi - lo).total_seconds()
        p = per.setdefault(owner, {"person": owner, "seconds": 0, "statuses": {}})
        p["seconds"] += secs
        p["statuses"][status] = p["statuses"].get(status, 0) + secs
    rows = [p for p in per.values() if p["seconds"] > 0]
    total = sum(p["seconds"] for p in rows) or 1
    for p in rows:
        p["hours"] = round(p["seconds"] / 3600, 1)
        p["pct"] = round(100 * p["seconds"] / total, 1)
        p["person_id"] = ((ids_by_name or {}).get(p["person"])
                          or (issue.assignee_id if p["person"] == issue.assignee else ""))
    rows.sort(key=lambda p: -p["seconds"])
    return rows


def logged_seconds(issue, person=None, person_id=None, start=None, end=None) -> float:
    """Worklog seconds booked on this ticket inside the window, optionally by
    one person.

    This is the ONLY real effort number available — everything else on the
    Active Time screens is calendar duration in a status, which is elapsed
    time (nights and weekends included), not hours worked. Keeping the two
    apart is the difference between "this sat in Development for 7 days" and
    "someone worked 6 hours on it"."""
    total = 0
    for w in issue.worklogs:
        if not w.get("seconds") or not w.get("ts"):
            continue
        if start and w["ts"] < start:
            continue
        if end and w["ts"] >= end:
            continue
        if person is not None:
            if not (w.get("author") == person
                    or (person_id and w.get("author_id") == person_id)):
                continue
        total += w["seconds"]
    return total


def active_time(issues, developer=None, start=None, end=None, match=None,
                ids_by_name=None) -> list[dict]:
    """Per-(developer, ticket) active-status time inside [start, end).

    'Active' = st.is_active_status() — the literal "someone is on this right
    now" statuses (one-per-lane), NOT the broader cycle-time bucket set used
    by cycle_rows/bottleneck above. A ticket sitting in a hand-off/queue
    status like "Ready for QA (QA Env)" the whole window contributes zero
    here even though it's still "in progress" for cycle-time purposes.

    Time is attributed by ownership-at-the-time (ticket_active_time), not to
    the current assignee, so a ticket that changed hands mid-window appears
    once per developer who actually held it. `active_seconds` is ELAPSED time
    in an active status; `logged_seconds` is booked worklog effort — the two
    answer different questions and the screens must never conflate them.
    Zero-time rows and hidden developers are dropped."""
    rows = []
    for i in issues:
        for p in ticket_active_time(i, start, end, ids_by_name):
            if developer and match and not match(developer, p["person"], p["person_id"]):
                continue
            if st.is_developer_hidden(p["person"], p["person_id"]):
                continue
            rows.append({"developer": p["person"], "developer_id": p["person_id"],
                         "issue": i, "active_seconds": p["seconds"],
                         "active_days": p["seconds"] / 86400,
                         "logged_seconds": logged_seconds(
                             i, p["person"], p["person_id"], start, end),
                         "statuses": p["statuses"]})
    rows.sort(key=lambda r: -r["active_seconds"])
    return rows


def bottleneck(issues) -> list[dict]:
    """FR-F3: median days per status across tickets (only statuses with data)."""
    per_status = {}
    for i in issues:
        for status, secs in i.timeline.seconds_in_status.items():
            if secs > 0:
                per_status.setdefault(status, []).append(secs / 86400)
    rows = [{"status": s, "bucket": st.bucket_of(s) or "unmapped",
             "median_days": round(A.percentile(v, 50), 2),
             "p85_days": round(A.percentile(v, 85), 2), "n": len(v)}
            for s, v in per_status.items()]
    rows.sort(key=lambda r: -r["median_days"])
    return rows


def multiple_active(issues, developer=None, match=None) -> list[dict]:
    """Rule 1 / FR-F5: more than one ticket in the SAME active lane at once —
    dev (In Progress/Development), or a testing lane (QA / Staging / Production).
    Each lane is enforced independently, per the team's status reference."""
    by = {}
    for i in issues:
        if st.is_active_status(i.status) and i.assignee != "Unassigned":
            # Developers hidden in Settings (past employees) stay out of here too,
            # not just out of the My Day dropdown.
            if st.is_developer_hidden(i.assignee, i.assignee_id):
                continue
            by.setdefault((i.assignee, i.assignee_id, st.lane_of(i.status)), []).append(i)
    rows = []
    for (name, aid, lane), tickets in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(tickets) <= 1:
            continue
        if developer and match and not match(developer, name, aid):
            continue
        rows.append({"developer": name, "account_id": aid, "lane": lane,
                     "lane_label": workflow.LANE_LABELS.get(lane, lane),
                     "count": len(tickets), "tickets": tickets})
    return rows


def bug_lens(issues, developer=None, start=None, end=None, match=None) -> list[dict]:
    """FR-QL1: bug metrics per developer — median resolution hours, raw counts."""
    per_dev = {}
    for i in issues:
        if i.type.lower() != "bug":
            continue
        if developer and match and not match(developer, i.assignee, i.assignee_id):
            continue
        if st.is_developer_hidden(i.assignee, i.assignee_id):   # past employees
            continue
        anchor = i.resolved or i.updated or i.created
        if (start or end) and anchor and not ((not start or anchor >= start)
                                              and (not end or anchor < end)):
            continue
        d = per_dev.setdefault(i.assignee, {"count": 0, "done": 0, "returned": 0, "hours": []})
        d["count"] += 1
        if not i.is_open:
            d["done"] += 1
            begin = _first_entry(i, {"active_dev"}) or i.created
            fin = _first_entry(i, {"done"}, after=begin) or i.resolved
            if begin and fin and fin >= begin:
                d["hours"].append((fin - begin).total_seconds() / 3600)
        d["returned"] += sum(1 for _t, _a, _id, frm, to in i.status_events
                             if qh._is_return(frm, to))
    rows = []
    for dev, d in sorted(per_dev.items(), key=lambda kv: -kv[1]["count"]):
        med = A.percentile(d["hours"], 50)
        rows.append({"developer": dev, "count": d["count"], "done": d["done"],
                     "returned": d["returned"],
                     "median_hours": round(med, 1) if med is not None else None,
                     "rate_label": (f"{round(100 * d['returned'] / d['done'])}% "
                                    f"({d['returned']} of {d['done']})" if d["done"] else "—")})
    return rows


def reopen_loops(issues, developer=None, match=None) -> list[dict]:
    """FR-QL2: tickets with >=2 rework cycles, highlighted. `developer`/`match`
    restrict it to one person's tickets — the table shows assignees, so an
    employee locked to their own developer must not see the whole team's."""
    rows = [{"issue": i, "loops": i.timeline.reopened_count}
            for i in issues if i.timeline.reopened_count >= 2
            and not (developer and match and not match(developer, i.assignee, i.assignee_id))]
    rows.sort(key=lambda r: -r["loops"])
    return rows


def return_trend(issues, weeks=8) -> list[dict]:
    """FR-QL3: team-level handoffs vs returns per ISO week."""
    now = A.now_utc()
    buckets = {}
    for i in issues:
        for ts, _a, _id, frm, to in i.status_events:
            if (now - ts).days > weeks * 7:
                continue
            wk = ts.strftime("%G-W%V")
            b = buckets.setdefault(wk, {"handoffs": 0, "returns": 0})
            if qh._is_handoff(frm, to):
                b["handoffs"] += 1
            elif qh._is_return(frm, to):
                b["returns"] += 1
    rows = []
    for wk in sorted(buckets):
        b = buckets[wk]
        rate = round(100 * b["returns"] / b["handoffs"]) if b["handoffs"] else None
        rows.append({"week": wk, **b, "rate_pct": rate,
                     "rate_label": f"{rate}% ({b['returns']} of {b['handoffs']})"
                                   if rate is not None else "—"})
    return rows
