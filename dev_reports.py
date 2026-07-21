"""
dev_reports.py
--------------
The 18 developer-discipline reports from the "Jira Developer Reports Definition"
spec: daily activity, silent tickets, focus discipline, EOD discipline, QA handoff,
returns from QA, cycle time, aging, worklogs, estimates, overdue, handoff quality,
uncommented transitions, blockers, sprint commitment, ticket timeline, focus, and
bug quality.

All reports operate on one enriched in-memory dataset (jira_client.fetch_dev_dataset:
issues + changelog + comments + worklogs + planning fields) and share the spec's
common input parameters: project, developer (accountId or name fragment), start
date, end date — all optional unless noted (timeline needs an issue key).

Each builder returns {"columns": [labels], "rows": [[cell, ...]], "note": str}.
A cell may be {"text": ..., "url": ...} to render as a link; everything else is
rendered as text. This keeps the web layer generic across all 18 reports.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from statistics import mean

import analytics as A
import config as cfg


# ---------------------------------------------------------------------------
# Dataset model
# ---------------------------------------------------------------------------

def _adf_text(node) -> str:
    """Flatten an Atlassian Document Format body (or plain string) to text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    out = []
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "text":
                out.append(n.get("text", ""))
            for c in n.get("content", []) or []:
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)
    walk(node)
    return " ".join(t for t in out if t).strip()


@dataclass
class DevIssue:
    key: str
    summary: str
    type: str
    status: str
    category: str
    assignee: str
    assignee_id: str
    reporter: str
    created: dt.datetime | None
    updated: dt.datetime | None
    resolved: dt.datetime | None
    duedate: dt.date | None
    original_estimate: int | None          # seconds
    story_points: float | None
    labels: list
    sprints: list                          # [{"name","state","end"}]
    status_events: list                    # [(ts, author, author_id, from, to)]
    assignee_events: list                  # [(ts, author, from, to)]
    comments: list                         # [{"ts","author","author_id","text"}]
    worklogs: list                         # [{"ts","author","author_id","seconds","note"}]
    field_events: list = field(default_factory=list)  # [(ts, author, kind, from, to)]
    start_date: dt.date | None = None
    fix_versions: list = field(default_factory=list)   # release names (fixVersion)
    timeline: A.Timeline = None

    @property
    def has_release(self) -> bool:
        return bool(self.fix_versions)

    @property
    def url(self):
        import jira_client as jc
        return f"{jc.JIRA_BASE_URL}/browse/{self.key}"

    @property
    def stage(self):
        return cfg.stage_of(self.status, self.category)

    @property
    def is_open(self):
        return self.stage not in cfg.DONE_STAGES

    @property
    def is_active(self):
        return self.category == "In Progress"

    def key_cell(self):
        return {"text": self.key, "url": self.url}


def _parse_sprints(val) -> list:
    out = []
    for s in val or []:
        if isinstance(s, dict):
            out.append({"name": s.get("name", "?"), "state": s.get("state", ""),
                        "end": A.parse_ts(s.get("endDate") or s.get("completeDate"))})
        elif isinstance(s, str):  # legacy "com.atlassian.greenhopper...[name=...,state=...]"
            name = re.search(r"name=([^,\]]+)", s)
            state = re.search(r"state=([^,\]]+)", s)
            end = re.search(r"endDate=([^,\]]+)", s)
            out.append({"name": name.group(1) if name else "?",
                        "state": state.group(1) if state else "",
                        "end": A.parse_ts(end.group(1)) if end and end.group(1) != "<null>" else None})
    return out


# Changelog field name (lowercased) -> unified field-event kind (FR-D3).
_FIELD_KINDS = {"duedate": "duedate", "due date": "duedate",
                "start date": "startdate", "planned start": "startdate",
                "flagged": "flag", "sprint": "sprint"}


def load_dev_issues(raw_list, custom_fields=None) -> list[DevIssue]:
    cf = custom_fields or {}
    start_field = cf.get("start_date") or ""
    issues = []
    for raw in raw_list:
        f = raw.get("fields", {})
        status = f.get("status", {}) or {}
        cat = (status.get("statusCategory", {}) or {}).get("name", "")
        hist = raw.get("changelog", {}).get("histories", [])
        status_events, assignee_events, field_events = [], [], []
        for h in hist:
            ts = A.parse_ts(h.get("created"))
            if not ts:
                continue
            author = (h.get("author") or {}).get("displayName", "Unknown")
            author_id = (h.get("author") or {}).get("accountId", "")
            for item in h.get("items", []):
                fname = (item.get("field") or "").lower()
                if fname == "status":
                    status_events.append((ts, author, author_id,
                                          item.get("fromString") or "", item.get("toString") or ""))
                elif fname == "assignee":
                    assignee_events.append((ts, author,
                                            item.get("fromString") or "", item.get("toString") or ""))
                elif fname in _FIELD_KINDS or (start_field and item.get("fieldId") == start_field):
                    kind = _FIELD_KINDS.get(fname, "startdate")
                    field_events.append((ts, author, kind,
                                         item.get("fromString") or "", item.get("toString") or ""))
        status_events.sort(key=lambda e: e[0])
        assignee_events.sort(key=lambda e: e[0])
        field_events.sort(key=lambda e: e[0])
        comments = [{"ts": A.parse_ts(c.get("created")),
                     "author": (c.get("author") or {}).get("displayName", "Unknown"),
                     "author_id": (c.get("author") or {}).get("accountId", ""),
                     "text": _adf_text(c.get("body"))}
                    for c in (f.get("comment") or {}).get("comments", [])]
        worklogs = [{"ts": A.parse_ts(w.get("started")),
                     "author": (w.get("author") or {}).get("displayName", "Unknown"),
                     "author_id": (w.get("author") or {}).get("accountId", ""),
                     "seconds": w.get("timeSpentSeconds") or 0,
                     "note": _adf_text(w.get("comment"))}
                    for w in (f.get("worklog") or {}).get("worklogs", [])]
        sp = f.get(cf.get("story_points") or "", None)
        due = None
        if f.get("duedate"):
            try:
                due = dt.date.fromisoformat(f["duedate"][:10])
            except ValueError:
                pass
        sdate = None
        raw_sdate = f.get(start_field) if start_field else None
        if isinstance(raw_sdate, str):
            try:
                sdate = dt.date.fromisoformat(raw_sdate[:10])
            except ValueError:
                pass
        issues.append(DevIssue(
            key=raw.get("key", ""),
            summary=f.get("summary", ""),
            type=(f.get("issuetype") or {}).get("name", ""),
            status=status.get("name", ""),
            category=cat,
            assignee=(f.get("assignee") or {}).get("displayName", "Unassigned"),
            assignee_id=(f.get("assignee") or {}).get("accountId", ""),
            reporter=(f.get("reporter") or {}).get("displayName", ""),
            created=A.parse_ts(f.get("created")),
            updated=A.parse_ts(f.get("updated")),
            resolved=A.parse_ts(f.get("resolutiondate")),
            duedate=due,
            original_estimate=f.get("timeoriginalestimate"),
            story_points=float(sp) if isinstance(sp, (int, float)) else None,
            labels=[l for l in (f.get("labels") or [])],
            sprints=_parse_sprints(f.get(cf.get("sprint") or "", None)),
            status_events=status_events,
            assignee_events=assignee_events,
            comments=[c for c in comments if c["ts"]],
            worklogs=[w for w in worklogs if w["ts"]],
            field_events=field_events,
            start_date=sdate,
            fix_versions=[v.get("name") for v in (f.get("fixVersions") or []) if v.get("name")],
            timeline=A.analyze(hist, f.get("created"), f.get("resolutiondate"),
                               status.get("name", ""), cat),
        ))
    return issues


# ---------------------------------------------------------------------------
# Shared filter helpers
# ---------------------------------------------------------------------------

def _dev_match(q: str | None, name: str, account_id: str = "") -> bool:
    """Developer filter: accountId exact or display-name substring, per the spec.

    The name half matches whole words rather than any substring, so typing a
    first name still works but "Sam" no longer also matches "Samantha". The v3
    screens pick from a dropdown and use the stricter dev_match_exact()."""
    if not q:
        return True
    q = q.strip().lower()
    if q == (account_id or "").lower():
        return True
    full = (name or "").strip().lower()
    return q == full or q in full.split()


def dev_match_exact(q: str | None, name: str, account_id: str = "") -> bool:
    """Strict developer filter for UI-selected developers: the dropdown supplies
    an accountId (or an exact display name), so match exactly and never by
    substring — otherwise one developer's view can pull in a colleague whose name
    merely contains theirs."""
    if not q:
        return True
    q = q.strip().lower()
    return q == (account_id or "").lower() or q == (name or "").strip().lower()


def _in_range(ts, start, end) -> bool:
    if ts is None:
        return False
    return (start is None or ts >= start) and (end is None or ts < end)


def _fts(ts):
    return ts.strftime("%Y-%m-%d %H:%M") if ts else "—"


def _days_since(ts, now=None):
    if not ts:
        return None
    return round(((now or A.now_utc()) - ts).total_seconds() / 86400, 1)


def _hours(a, b):
    if not (a and b):
        return None
    return round((b - a).total_seconds() / 3600, 1)


def _first_entry(i: DevIssue, stages) -> dt.datetime | None:
    for ts, _a, _id, _f, to in i.status_events:
        if cfg.stage_of(to) in stages:
            return ts
    return None


def _yes(b):
    return "Yes" if b else "No"


def _preview(text, n=120):
    text = (text or "").replace("\n", " ")
    return text[:n] + ("…" if len(text) > n else "")


# ---------------------------------------------------------------------------
# 1. Daily Developer Activity
# ---------------------------------------------------------------------------

def daily_activity(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if _in_range(ts, start, end) and _dev_match(developer, author, aid):
                rows.append((ts, ["", "Status change", author, i.key_cell(), i.summary,
                                  i.type, i.status, f"{frm or '—'} → {to}"]))
        for ts, author, frm, to in i.assignee_events:
            if _in_range(ts, start, end) and _dev_match(developer, author):
                rows.append((ts, ["", "Assignee change", author, i.key_cell(), i.summary,
                                  i.type, i.status, f"{frm or 'Unassigned'} → {to or 'Unassigned'}"]))
        for c in i.comments:
            if _in_range(c["ts"], start, end) and _dev_match(developer, c["author"], c["author_id"]):
                rows.append((c["ts"], ["", "Comment", c["author"], i.key_cell(), i.summary,
                                       i.type, i.status, _preview(c["text"])]))
        for w in i.worklogs:
            if _in_range(w["ts"], start, end) and _dev_match(developer, w["author"], w["author_id"]):
                rows.append((w["ts"], ["", "Worklog", w["author"], i.key_cell(), i.summary,
                                       i.type, i.status,
                                       f"{round(w['seconds']/3600, 1)}h {_preview(w['note'], 80)}"]))
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    for ts, r in rows:
        r[0] = _fts(ts)
        out.append(r)
    return {"columns": ["Activity At", "Activity Type", "Developer", "Issue Key", "Summary",
                        "Issue Type", "Current Status", "Details"],
            "rows": out,
            "note": "Every status/assignee change, comment, and worklog in the period."}


# ---------------------------------------------------------------------------
# 2. Silent / No Daily Update Tickets
# ---------------------------------------------------------------------------

def silent_tickets(issues, developer=None, start=None, end=None):
    now = A.now_utc()
    rows = []
    for i in issues:
        if not i.is_active or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        if start and i.updated and i.updated >= start:
            continue  # updated after the cutoff -> not silent
        rows.append([i.key_cell(), i.summary, i.assignee, i.status,
                     _fts(i.updated), _days_since(i.updated, now)])
    rows.sort(key=lambda r: -(r[5] or 0))
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Updated At",
                        "Days Without Update"],
            "rows": rows,
            "note": "Active tickets with no update since the start date (or ordered by staleness when blank)."}


# ---------------------------------------------------------------------------
# 3. Multiple Active Tickets Violation
# ---------------------------------------------------------------------------

def multiple_active(issues, developer=None, start=None, end=None):
    by_dev = {}
    for i in issues:
        if i.is_active and i.assignee != "Unassigned":
            by_dev.setdefault((i.assignee, i.assignee_id), []).append(i)
    rows = []
    for (name, aid), tickets in sorted(by_dev.items(), key=lambda kv: -len(kv[1])):
        if len(tickets) <= 1 or not _dev_match(developer, name, aid):
            continue
        rows.append([name, aid or "—", len(tickets),
                     ", ".join(f"{t.key} ({t.status})" for t in tickets)])
    return {"columns": ["Developer", "Assignee Account ID", "Active Ticket Count", "Tickets"],
            "rows": rows,
            "note": "Developers holding more than one active ticket right now."}


# ---------------------------------------------------------------------------
# 4. End-of-Day Discipline
# ---------------------------------------------------------------------------

def eod_discipline(issues, developer=None, start=None, end=None):
    day = (start or A.now_utc()).date()
    d0 = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    d1 = d0 + dt.timedelta(days=1)
    rows = []
    for i in issues:
        if not i.is_active or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        has_wl = any(_in_range(w["ts"], d0, d1) for w in i.worklogs)
        has_cm = any(_in_range(c["ts"], d0, d1) for c in i.comments)
        upd = bool(i.updated and _in_range(i.updated, d0, d1))
        rows.append([i.key_cell(), i.summary, i.assignee, i.status, _fts(i.updated),
                     _yes(has_wl), _yes(has_cm), _yes(upd)])
    rows.sort(key=lambda r: (r[5], r[6], r[7]))  # violations (No) first
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Updated At",
                        "Has Worklog On Selected Day", "Has Comment On Selected Day",
                        "Updated On Selected Day"],
            "rows": rows,
            "note": f"Selected day: {day.isoformat()} (set a start date to pick a different day)."}


# ---------------------------------------------------------------------------
# 5. Ready for QA Contribution
# ---------------------------------------------------------------------------

def rfqa_contribution(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if cfg.stage_of(to) != cfg.STAGE_READY_FOR_QA:
                continue
            if not _in_range(ts, start, end) or not _dev_match(developer, author, aid):
                continue
            rows.append([_fts(ts), author, i.key_cell(), i.summary,
                         frm or "—", to, i.status, i.assignee])
    rows.sort(key=lambda r: r[0], reverse=True)
    return {"columns": ["Ready for QA At", "Developer", "Issue Key", "Summary",
                        "Previous Status", "New Status", "Current Status", "Current Assignee"],
            "rows": rows,
            "note": "Credited to the person who performed the Ready-for-QA transition."}


# ---------------------------------------------------------------------------
# 6. Returned from QA / Reopened
# ---------------------------------------------------------------------------

def returned_from_qa(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if cfg.stage_of(frm) not in cfg.QA_STAGES | {cfg.STAGE_STAGING}:
                continue
            if cfg.stage_of(to) not in cfg.RETURN_TARGET_STAGES:
                continue
            if not _in_range(ts, start, end):
                continue
            if not (_dev_match(developer, author, aid) or _dev_match(developer, i.assignee, i.assignee_id)):
                continue
            rows.append([_fts(ts), author, i.assignee, i.key_cell(), i.summary,
                         frm, to, i.status])
    rows.sort(key=lambda r: r[0], reverse=True)
    return {"columns": ["Returned At", "Changed By", "Current Developer", "Issue Key",
                        "Summary", "From Status", "To Status", "Current Status"],
            "rows": rows,
            "note": "Transitions from QA statuses back to development statuses."}


# ---------------------------------------------------------------------------
# 7. Cycle Time by Developer
# ---------------------------------------------------------------------------

def cycle_time(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        if not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        dev_start = _first_entry(i, cfg.DEV_STAGES)
        rfqa = _first_entry(i, {cfg.STAGE_READY_FOR_QA})
        done = _first_entry(i, cfg.DONE_STAGES) or i.resolved
        if not dev_start:
            continue
        anchor = done or rfqa or dev_start
        if not _in_range(anchor, start, end):
            continue
        rows.append([i.key_cell(), i.summary, i.type, i.assignee,
                     _fts(dev_start), _fts(rfqa), _fts(done),
                     _hours(dev_start, rfqa) if rfqa else "—",
                     _hours(dev_start, done) if done else "—"])
    rows.sort(key=lambda r: r[4], reverse=True)
    return {"columns": ["Issue Key", "Summary", "Issue Type", "Developer",
                        "Development Started At", "Ready for QA At", "Done At",
                        "Development to QA Hours", "Total Cycle Hours"],
            "rows": rows,
            "note": "From first development transition to QA handoff and completion."}


# ---------------------------------------------------------------------------
# 8. Stuck Ticket Aging
# ---------------------------------------------------------------------------

def stuck_aging(issues, developer=None, start=None, end=None, threshold_days=0):
    now = A.now_utc()
    rows = []
    for i in issues:
        if not i.is_open or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        entered = i.status_events[-1][0] if i.status_events else i.created
        days = _days_since(entered, now) or 0
        if days < (threshold_days or 0):
            continue
        rows.append([i.key_cell(), i.summary, i.assignee, i.status,
                     _fts(entered), days, _fts(i.updated)])
    rows.sort(key=lambda r: -r[5])
    return {"columns": ["Issue Key", "Summary", "Developer", "Status",
                        "Current Status Started At", "Days in Status", "Updated At"],
            "rows": rows,
            "note": "Unfinished tickets ordered by time in their current status."}


# ---------------------------------------------------------------------------
# 9. Worklog Completeness
# ---------------------------------------------------------------------------

def worklog_completeness(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        wl = [w for w in i.worklogs if _in_range(w["ts"], start, end)] if (start or end) else i.worklogs
        relevant = wl or (i.updated and _in_range(i.updated, start, end)) or not (start or end)
        if not relevant and not i.is_active:
            continue
        if not (_dev_match(developer, i.assignee, i.assignee_id)
                or any(_dev_match(developer, w["author"], w["author_id"]) for w in wl)):
            continue
        total_h = round(sum(w["seconds"] for w in wl) / 3600, 1)
        rows.append([i.key_cell(), i.summary, i.assignee, i.status, _fts(i.updated),
                     total_h, len(wl), _yes(bool(wl))])
    rows.sort(key=lambda r: (r[7], -r[5]))  # missing worklogs first
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Updated At",
                        "Total Logged Hours", "Worklog Count", "Has Worklog"],
            "rows": rows,
            "note": "Tickets updated in the period vs the worklog effort actually recorded."}


# ---------------------------------------------------------------------------
# 10. Tickets Without Estimate
# ---------------------------------------------------------------------------

def no_estimate(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        if not i.is_active or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        if i.original_estimate or i.story_points:
            continue
        rows.append([i.key_cell(), i.summary, i.assignee, i.status, i.type,
                     "—", "—"])
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Issue Type",
                        "Original Estimate", "Story Points"],
            "rows": rows,
            "note": "Active tickets missing both a time estimate and story points."}


# ---------------------------------------------------------------------------
# 11. Overdue Tickets
# ---------------------------------------------------------------------------

def overdue(issues, developer=None, start=None, end=None):
    today = A.now_utc().date()
    rows = []
    for i in issues:
        if not i.is_open or not i.duedate or i.duedate >= today:
            continue
        if not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        rows.append([i.key_cell(), i.summary, i.assignee, i.status,
                     i.duedate.isoformat(), (today - i.duedate).days, _fts(i.updated)])
    rows.sort(key=lambda r: -r[5])
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Due Date",
                        "Days Overdue", "Updated At"],
            "rows": rows,
            "note": "Unfinished tickets past their Jira due date."}


# ---------------------------------------------------------------------------
# 12. Developer Handoff Quality
# ---------------------------------------------------------------------------

def handoff_quality(issues, developer=None, start=None, end=None):
    rows = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if cfg.stage_of(to) != cfg.STAGE_READY_FOR_QA:
                continue
            if not _in_range(ts, start, end) or not _dev_match(developer, author, aid):
                continue
            dev_comments = [c for c in i.comments
                            if c["author_id"] == aid or c["author"] == author]
            all_text = " ".join(c["text"].lower() for c in i.comments)
            has_comment = bool(dev_comments)
            has_test = any(k in all_text for k in cfg.HANDOFF_TEST_KEYWORDS)
            has_pr = any(k in all_text for k in cfg.HANDOFF_PR_KEYWORDS)
            result = "Pass" if (has_comment and has_test) else "Needs Improvement"
            rows.append([i.key_cell(), i.summary, author, _fts(ts), i.status,
                         _yes(has_comment), _yes(has_test), _yes(has_pr), result])
    rows.sort(key=lambda r: (r[8] == "Pass", r[3]), reverse=False)
    return {"columns": ["Issue Key", "Summary", "Developer", "Ready for QA At",
                        "Current Status", "Has Developer Comment", "Has Testing Note",
                        "Has PR/Build Reference", "Handoff Result"],
            "rows": rows,
            "note": "Keyword checks are configurable via HANDOFF_TEST_KEYWORDS / HANDOFF_PR_KEYWORDS."}


# ---------------------------------------------------------------------------
# 13. Status Change Without Comment
# ---------------------------------------------------------------------------

def status_no_comment(issues, developer=None, start=None, end=None):
    win = dt.timedelta(minutes=cfg.COMMENT_WINDOW_MIN)
    rows = []
    for i in issues:
        for ts, author, aid, frm, to in i.status_events:
            if not _in_range(ts, start, end) or not _dev_match(developer, author, aid):
                continue
            near = any((c["author_id"] == aid or c["author"] == author)
                       and abs((c["ts"] - ts).total_seconds()) <= win.total_seconds()
                       for c in i.comments)
            rows.append([_fts(ts), author, i.key_cell(), i.summary, frm or "—", to,
                         _yes(near), f"±{cfg.COMMENT_WINDOW_MIN} min"])
    rows.sort(key=lambda r: (r[6], r[0]), reverse=False)  # "No" rows first
    return {"columns": ["Changed At", "Developer", "Issue Key", "Summary", "From Status",
                        "To Status", "Nearby Comment Found", "Comment Window"],
            "rows": rows,
            "note": "Transitions without an explanatory comment by the same user sort first."}


# ---------------------------------------------------------------------------
# 14. Blocked Tickets
# ---------------------------------------------------------------------------

def blocked(issues, developer=None, start=None, end=None):
    now = A.now_utc()
    rows = []
    for i in issues:
        if not i.is_open or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        by_status = i.stage in cfg.BLOCKED_STAGES
        hit_labels = [l for l in i.labels if l.lower() in cfg.BLOCKED_LABELS]
        if not by_status and not hit_labels:
            continue
        reason = i.status if by_status else "label: " + ", ".join(hit_labels)
        rows.append([i.key_cell(), i.summary, i.assignee, i.status,
                     ", ".join(i.labels) or "—", _fts(i.updated), reason,
                     _days_since(i.updated, now)])
    rows.sort(key=lambda r: -(r[7] or 0))
    return {"columns": ["Issue Key", "Summary", "Developer", "Status", "Labels",
                        "Updated At", "Blocked Reason", "Days Since Update"],
            "rows": rows,
            "note": f"Blocked by paused status or labels ({', '.join(sorted(cfg.BLOCKED_LABELS))})."}


# ---------------------------------------------------------------------------
# 15. Sprint Commitment vs Completion
# ---------------------------------------------------------------------------

def sprint_commitment(issues, developer=None, start=None, end=None):
    cells = {}
    for i in issues:
        if not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        for s in i.sprints:
            c = cells.setdefault((s["name"], i.assignee),
                                 {"committed": 0, "done": 0, "carried": 0,
                                  "sp_committed": 0.0, "sp_done": 0.0,
                                  "closed": s["state"].lower() == "closed"})
            c["committed"] += 1
            c["sp_committed"] += i.story_points or 0
            if not i.is_open:
                c["done"] += 1
                c["sp_done"] += i.story_points or 0
            elif s["state"].lower() == "closed":
                c["carried"] += 1
    rows = []
    for (sprint, dev), c in sorted(cells.items()):
        pct = round(100 * c["done"] / c["committed"]) if c["committed"] else 0
        rows.append([sprint, dev, c["committed"], c["done"], c["carried"],
                     round(c["sp_committed"], 1), round(c["sp_done"], 1), f"{pct}%"])
    return {"columns": ["Sprint Name", "Developer", "Committed Tickets", "Completed Tickets",
                        "Carried Over Tickets", "Committed Story Points",
                        "Completed Story Points", "Completion Percent"],
            "rows": rows,
            "note": "All synced sprints for the selected scope; derived from the issue sprint field."}


# ---------------------------------------------------------------------------
# 16. Ticket Movement Timeline
# ---------------------------------------------------------------------------

def ticket_timeline(issues, issue_key=None, developer=None, start=None, end=None):
    key = (issue_key or "").strip().upper()
    match = next((i for i in issues if i.key.upper() == key), None)
    if not match:
        return {"columns": ["Event At", "Event Type", "Actor", "From Value", "To Value", "Details"],
                "rows": [],
                "note": f"Issue {key or '(none)'} not found in the synced dataset — check the key."}
    events = []
    for ts, author, aid, frm, to in match.status_events:
        events.append((ts, ["", "Status change", author, frm or "—", to, ""]))
    for ts, author, frm, to in match.assignee_events:
        events.append((ts, ["", "Assignee change", author, frm or "Unassigned",
                            to or "Unassigned", ""]))
    for c in match.comments:
        events.append((c["ts"], ["", "Comment", c["author"], "", "", _preview(c["text"], 160)]))
    for w in match.worklogs:
        events.append((w["ts"], ["", "Worklog", w["author"], "", "",
                                 f"{round(w['seconds']/3600, 1)}h {_preview(w['note'], 120)}"]))
    events = [(ts, r) for ts, r in events if _in_range(ts, start, end) or not (start or end)]
    events.sort(key=lambda e: e[0])
    rows = []
    for ts, r in events:
        r[0] = _fts(ts)
        rows.append(r)
    return {"columns": ["Event At", "Event Type", "Actor", "From Value", "To Value", "Details"],
            "rows": rows,
            "note": f"{match.key} — {match.summary} · current status: {match.status} · assignee: {match.assignee}"}


# ---------------------------------------------------------------------------
# 17. Developer Focus
# ---------------------------------------------------------------------------

def developer_focus(issues, developer=None, start=None, end=None):
    import settings as _st
    days = {}
    def bump(ts, actor, aid, key, kind):
        if not ts or not _in_range(ts, start, end) or not _dev_match(developer, actor, aid):
            return
        if _st.is_developer_hidden(actor, aid):   # past employees stay hidden
            return
        d = days.setdefault((ts.date(), actor), {"tickets": set(), "status": 0,
                                                 "comments": 0, "worklogs": 0})
        d["tickets"].add(key)
        d[kind] += 1
    for i in issues:
        for ts, author, aid, _f, _t in i.status_events:
            bump(ts, author, aid, i.key, "status")
        for c in i.comments:
            bump(c["ts"], c["author"], c["author_id"], i.key, "comments")
        for w in i.worklogs:
            bump(w["ts"], w["author"], w["author_id"], i.key, "worklogs")
    rows = []
    for (day, dev), d in sorted(days.items(), reverse=True):
        total = d["status"] + d["comments"] + d["worklogs"]
        rows.append([day.isoformat(), dev, len(d["tickets"]), total,
                     d["status"], d["comments"], d["worklogs"]])
    return {"columns": ["Activity Date", "Developer", "Distinct Tickets Touched",
                        "Total Activities", "Status Changes", "Comments", "Worklogs"],
            "rows": rows,
            "note": "High distinct-ticket counts per day indicate context switching."}


# ---------------------------------------------------------------------------
# 18. Bug Fix Quality
# ---------------------------------------------------------------------------

def bug_quality(issues, developer=None, start=None, end=None):
    per_dev = {}
    for i in issues:
        if i.type.lower() != "bug" or not _dev_match(developer, i.assignee, i.assignee_id):
            continue
        anchor = i.resolved or i.updated or i.created
        if (start or end) and not _in_range(anchor, start, end):
            continue
        d = per_dev.setdefault(i.assignee, {"count": 0, "done": 0, "returned": 0, "hours": []})
        d["count"] += 1
        if not i.is_open:
            d["done"] += 1
            begin = _first_entry(i, cfg.DEV_STAGES) or i.created
            fin = _first_entry(i, cfg.DONE_STAGES) or i.resolved
            h = _hours(begin, fin)
            if h is not None:
                d["hours"].append(h)
        d["returned"] += sum(
            1 for _ts, _a, _id, frm, to in i.status_events
            if cfg.stage_of(frm) in cfg.QA_STAGES | {cfg.STAGE_STAGING}
            and cfg.stage_of(to) in cfg.RETURN_TARGET_STAGES)
    rows = []
    for dev, d in sorted(per_dev.items(), key=lambda kv: -kv[1]["count"]):
        rate = round(100 * d["returned"] / d["done"]) if d["done"] else 0
        rows.append([dev, d["count"], d["done"], d["returned"],
                     round(mean(d["hours"]), 1) if d["hours"] else "—", f"{rate}%"])
    return {"columns": ["Developer", "Bug Count", "Completed Bugs", "Returned from QA Count",
                        "Average Resolution Hours", "Return Rate"],
            "rows": rows,
            "note": "Return rate = QA returns divided by completed bugs."}
