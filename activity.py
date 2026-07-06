"""
activity.py
-----------
Unified activity feed (PRD v3 §3.3): one chronological event stream per ticket
and per developer — status transitions, assignee changes, comments, worklogs,
due-date/start-date/flag/sprint changes. Powers My Day, the checklist engine,
Focus, the Investigator, and the raw activity view.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt


@dataclass
class Event:
    ts: dt.datetime
    kind: str          # status | assignee | comment | worklog | duedate | startdate | flag | sprint
    actor: str
    actor_id: str
    issue: object      # DevIssue
    frm: str = ""
    to: str = ""
    detail: str = ""
    seconds: int = 0   # worklog only

    @property
    def is_update(self):
        return True


def events_for(issue) -> list[Event]:
    """All events for one DevIssue, oldest first."""
    out = []
    for ts, author, aid, frm, to in issue.status_events:
        out.append(Event(ts, "status", author, aid, issue, frm, to))
    for ts, author, frm, to in issue.assignee_events:
        out.append(Event(ts, "assignee", author, "", issue,
                         frm or "Unassigned", to or "Unassigned"))
    for c in issue.comments:
        out.append(Event(c["ts"], "comment", c["author"], c["author_id"], issue,
                         detail=c["text"]))
    for w in issue.worklogs:
        out.append(Event(w["ts"], "worklog", w["author"], w["author_id"], issue,
                         detail=w["note"], seconds=w["seconds"]))
    for ts, author, kind, frm, to in issue.field_events:
        out.append(Event(ts, kind, author, "", issue, frm, to))
    out.sort(key=lambda e: e.ts)
    return out


def build_feed(issues, developer=None, start=None, end=None, match=None) -> list[Event]:
    """Flat feed across issues, newest first. `match(q, name, id)` is the
    developer matcher (dev_reports._dev_match) injected to avoid a cycle."""
    feed = []
    for i in issues:
        for e in events_for(i):
            if start and e.ts < start:
                continue
            if end and e.ts >= end:
                continue
            if developer and match and not match(developer, e.actor, e.actor_id):
                continue
            feed.append(e)
    feed.sort(key=lambda e: e.ts, reverse=True)
    return feed


def last_event_ts(issue) -> dt.datetime | None:
    ev = events_for(issue)
    return ev[-1].ts if ev else (issue.updated or issue.created)
