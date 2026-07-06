"""
qa_handoff.py
-------------
QA Handoff engine (PRD v3 FR-Q1–Q4). Edge definitions are bucket-driven from
settings (config-driven, per the instructions §7): a handoff is any transition
entering `qa_stage` from outside it; a return is any `qa_stage` →
(`active_dev` | `rework`) transition. Attribution is always the changelog
author of the transition itself; current assignee is shown alongside.

Handoff checks (FR-Q2) output Pass / Needs info only — never decimal scores.
"""

from __future__ import annotations

import datetime as dt
import re

import settings as st

_URL_RE = re.compile(r"https?://\S*(github\.com|bitbucket\.org|dev\.azure\.com|/pull/|/pull-requests/|/commit/)\S*", re.I)


def _is_handoff(frm: str, to: str) -> bool:
    return st.bucket_of(to) == "qa_stage" and st.bucket_of(frm) != "qa_stage"


def _is_return(frm: str, to: str) -> bool:
    return st.bucket_of(frm) == "qa_stage" and st.bucket_of(to) in ("active_dev", "rework")


def _has_pr_reference(issue) -> bool:
    """PR/build reference: URL patterns first (FR-D4), keyword list as backup."""
    kw = [k.lower() for k in st.load()["pr_keywords"]]
    for c in issue.comments:
        text = c["text"] or ""
        if _URL_RE.search(text):
            return True
        low = text.lower()
        if any(k in low for k in kw):
            return True
    return False


def handoff_feed(issues, developer=None, start=None, end=None, match=None) -> list[dict]:
    """FR-Q1/Q2: every transition into qa_stage in the window, with checks."""
    s = st.load()
    win = dt.timedelta(hours=s["handoff_window_hours"])
    out = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if not _is_handoff(frm, to):
                continue
            if start and ts < start:
                continue
            if end and ts >= end:
                continue
            if developer and match and not match(developer, author, aid):
                continue
            has_comment = any(
                (c["author_id"] == aid or c["author"] == author)
                and ts - win <= c["ts"] <= ts + dt.timedelta(minutes=10)
                for c in i.comments)
            has_pr = _has_pr_reference(i)
            out.append({
                "ts": ts, "developer": author, "developer_id": aid, "issue": i,
                "prev_status": frm or "—", "new_status": to,
                "has_comment": has_comment, "has_pr": has_pr,
                "result": "Pass" if (has_comment and has_pr) else "Needs info",
            })
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def returned_feed(issues, developer=None, start=None, end=None, match=None) -> list[dict]:
    """FR-Q3: every qa_stage → active_dev/rework back-transition, with the
    return-reason comment when one was left near the transition."""
    out = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if not _is_return(frm, to):
                continue
            if start and ts < start:
                continue
            if end and ts >= end:
                continue
            if developer and match and not (match(developer, author, aid)
                                            or match(developer, i.assignee, i.assignee_id)):
                continue
            reason = next(
                (c["text"] for c in i.comments
                 if (c["author_id"] == aid or c["author"] == author)
                 and abs((c["ts"] - ts).total_seconds()) <= 1800), "")
            out.append({"ts": ts, "returned_by": author, "issue": i,
                        "from_status": frm, "to_status": to, "reason": reason})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def return_rates(issues, start=None, end=None) -> list[dict]:
    """FR-Q4: return-rate by developer — attribution to whoever authored the most
    recent handoff before each return. Raw counts always shown with the rate."""
    handoffs, returns = {}, {}
    for i in issues:
        last_handoff_author = None
        for ts, author, aid, frm, to in i.status_events:
            in_window = (not start or ts >= start) and (not end or ts < end)
            if _is_handoff(frm, to):
                last_handoff_author = author
                if in_window:
                    handoffs[author] = handoffs.get(author, 0) + 1
            elif _is_return(frm, to) and last_handoff_author and in_window:
                returns[last_handoff_author] = returns.get(last_handoff_author, 0) + 1
    rows = []
    for dev in sorted(set(handoffs) | set(returns)):
        h, r = handoffs.get(dev, 0), returns.get(dev, 0)
        rows.append({"developer": dev, "handoffs": h, "returns": r,
                     "rate_pct": round(100 * r / h) if h else None,
                     "rate_label": (f"{round(100 * r / h)}% ({r} of {h})" if h
                                    else f"{r} return(s), 0 handoffs in window")})
    rows.sort(key=lambda x: -(x["rate_pct"] or 0))
    return rows
