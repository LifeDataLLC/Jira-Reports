"""
reports.py
----------
Builds the eight Executive Reporting Framework reports from a single in-memory dataset
of issues (each carrying its changelog). Transport lives in jira_client; changelog math
lives in analytics; this module is the business logic that shapes report payloads.

Attribution note: developer/QA output is attributed to the *person who performed the
transition* (changelog author), which is more accurate than current assignee for
"who did the work". Workload/assignment views still use current assignee.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from statistics import mean, median

import analytics as A
import config as cfg

JIRA_BASE = None  # set by jira_client import below to build URLs
try:
    import jira_client as jc
    JIRA_BASE = jc.JIRA_BASE_URL
except Exception:
    JIRA_BASE = ""


# ---------------------------------------------------------------------------
# Issue wrapper
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    key: str
    summary: str
    type: str
    priority: str
    assignee: str
    status: str
    category: str
    created: dt.datetime | None
    resolved: dt.datetime | None
    fix_versions: list
    timeline: A.Timeline
    events: list  # (ts, author, from_status, to_status)

    @property
    def url(self):
        return f"{JIRA_BASE}/browse/{self.key}"

    @property
    def stage(self):
        return cfg.stage_of(self.status, self.category)

    @property
    def is_open(self):
        return self.stage not in cfg.DONE_STAGES

    @property
    def is_bug(self):
        return self.type.lower() == "bug"

    @property
    def age_days(self):
        """Calendar days since the ticket was created (its open age)."""
        if not self.created:
            return None
        return round((A.now_utc() - self.created).total_seconds() / 86400, 1)


def load_issues(raw_list) -> list[Issue]:
    issues = []
    for raw in raw_list:
        f = raw.get("fields", {})
        status = f.get("status", {}) or {}
        cat = (status.get("statusCategory", {}) or {}).get("name", "")
        cl = raw.get("changelog", {}).get("histories", [])
        tl = A.analyze(cl, f.get("created"), f.get("resolutiondate"),
                       status.get("name", ""), cat)
        issues.append(Issue(
            key=raw.get("key", ""),
            summary=f.get("summary", ""),
            type=(f.get("issuetype", {}) or {}).get("name", ""),
            priority=(f.get("priority") or {}).get("name", "None"),
            assignee=(f.get("assignee") or {}).get("displayName", "Unassigned"),
            status=status.get("name", ""),
            category=cat,
            created=A.parse_ts(f.get("created")),
            resolved=A.parse_ts(f.get("resolutiondate")),
            fix_versions=[v.get("name") for v in (f.get("fixVersions") or [])],
            timeline=tl,
            events=A.status_events(cl),
        ))
    return issues


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 2) if vals else None


def _med(vals):
    vals = [v for v in vals if v is not None]
    return round(median(vals), 2) if vals else None


def window_bounds(days_back: int, now=None):
    now = now or A.now_utc()
    return now - dt.timedelta(days=days_back), now


# ---------------------------------------------------------------------------
# Report 1 — Daily Work Movement
# ---------------------------------------------------------------------------

def daily_movement(issues, day_start=None, day_end=None):
    now = A.now_utc()
    if not day_start:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not day_end:
        day_end = now
    created = sum(1 for i in issues if i.created and day_start <= i.created < day_end)
    started = sum(A.transitions_into(i.timeline, cfg.STAGE_IN_PROGRESS, day_start, day_end) for i in issues)
    dev = sum(A.transitions_into(i.timeline, cfg.STAGE_DEVELOPMENT, day_start, day_end) for i in issues)
    ready_qa = sum(A.transitions_into(i.timeline, cfg.STAGE_READY_FOR_QA, day_start, day_end) for i in issues)
    completed = sum(A.transitions_into(i.timeline, cfg.STAGE_DONE, day_start, day_end) for i in issues)
    blocked = [i for i in issues if i.stage in cfg.BLOCKED_STAGES]
    moved = [i for i in issues if any(day_start <= ts < day_end for ts, *_ in i.timeline.transitions)]
    return {
        "day_start": day_start, "day_end": day_end,
        "counts": {"New tickets": created, "Started": started, "Development started": dev,
                   "Ready for QA": ready_qa, "Completed": completed, "Blocked (now)": len(blocked)},
        "moved": sorted(moved, key=lambda i: i.key),
        "blocked": blocked,
    }


# ---------------------------------------------------------------------------
# Report 3 — Developer Productivity  (attributed by transition author)
# ---------------------------------------------------------------------------

def developer_productivity(issues, days_back=14, now=None):
    start, end = window_bounds(days_back, now)
    out_stage_names = {n for n, s in cfg.STATUS_STAGE.items() if s == cfg.DEV_OUTPUT_STAGE}
    devs = {}

    def row(name):
        return devs.setdefault(name, {"name": name, "output": 0, "reopened": 0,
                                      "dev_durations": [], "tickets": []})

    for i in issues:
        # output: who moved it into the output stage within the window
        for ts, author, frm, to in i.events:
            if to in out_stage_names and start <= ts < end:
                r = row(author)
                r["output"] += 1
                r["tickets"].append(i)
                if i.timeline.dev_duration_days is not None:
                    r["dev_durations"].append(i.timeline.dev_duration_days)
        # reopened: attribute to last developer (assignee) of reopened work in window
        for ts, author, frm, to in i.events:
            if (cfg.stage_of(to) == cfg.STAGE_REOPENED or
                (cfg.stage_of(frm) in {cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING}
                 and cfg.stage_of(to) in {cfg.STAGE_DEVELOPMENT, cfg.STAGE_IN_PROGRESS})):
                if start <= ts < end:
                    row(i.assignee)["reopened"] += 1

    rows = []
    for r in devs.values():
        o, re = r["output"], r["reopened"]
        quality = round(o / (o + re), 2) if (o + re) else None
        rows.append({**r, "avg_dev_days": _avg(r["dev_durations"]),
                     "quality_score": quality})
    rows.sort(key=lambda r: -r["output"])
    return {"window_days": days_back, "rows": rows}


# ---------------------------------------------------------------------------
# Report 4 — QA Productivity  (attributed by transition author)
# ---------------------------------------------------------------------------

def qa_productivity(issues, days_back=14, now=None):
    start, end = window_bounds(days_back, now)
    qa = {}

    def row(name):
        return qa.setdefault(name, {"name": name, "verified": 0, "rejected": 0,
                                    "test_durations": []})

    for i in issues:
        for ts, author, frm, to in i.events:
            if not (start <= ts < end):
                continue
            fs, tstage = cfg.stage_of(frm), cfg.stage_of(to)
            # verified: moved from a QA stage to Done
            if fs in {cfg.STAGE_QA_TESTING, cfg.STAGE_READY_FOR_QA} and tstage == cfg.STAGE_DONE:
                r = row(author); r["verified"] += 1
                if i.timeline.qa_duration_days is not None:
                    r["test_durations"].append(i.timeline.qa_duration_days)
            # rejected: bounced back from QA to dev/reopen
            if fs in {cfg.STAGE_QA_TESTING, cfg.STAGE_READY_FOR_QA} and \
               tstage in {cfg.STAGE_DEVELOPMENT, cfg.STAGE_IN_PROGRESS, cfg.STAGE_REOPENED}:
                row(author)["rejected"] += 1

    rows = []
    for r in qa.values():
        v, rej = r["verified"], r["rejected"]
        rate = round(rej / (v + rej), 2) if (v + rej) else None
        rows.append({**r, "avg_test_days": _avg(r["test_durations"]), "rejection_rate": rate})
    rows = [r for r in rows if r["verified"] or r["rejected"]]
    rows.sort(key=lambda r: -r["verified"])
    return {"window_days": days_back, "rows": rows}


# ---------------------------------------------------------------------------
# Report 5 — Individual Activity
# ---------------------------------------------------------------------------

def individual_activity(issues, person, days_back=30, now=None,
                        types=None, statuses=None, min_open_age=0):
    """One person's activity, optionally filtered.

    types        -> keep only these issue types (e.g. {"Bug","Story"}); empty = all.
    statuses     -> keep only these raw statuses; empty = all.
    min_open_age -> drop open tickets younger than this many days (their open age).

    The available type/status options returned for the filter UI come from the
    person's full (unfiltered) set so toggling a filter never hides the control.
    """
    now = now or A.now_utc()
    start, end = window_bounds(days_back, now)
    mine_all = [i for i in issues if i.assignee == person]
    avail_types = sorted({i.type for i in mine_all if i.type})
    avail_statuses = sorted({i.status for i in mine_all if i.status})

    tset, sset = set(types or []), set(statuses or [])
    mine = [i for i in mine_all
            if (not tset or i.type in tset) and (not sset or i.status in sset)]

    completed = [i for i in mine if i.resolved and start <= i.resolved < end]
    open_issues = [i for i in mine if i.is_open]
    if min_open_age > 0:
        open_issues = [i for i in open_issues if (i.age_days or 0) >= min_open_age]

    active_secs = sum(sum(i.timeline.seconds_in_stage.get(s, 0) for s in cfg.ACTIVE_STAGES)
                      for i in completed)
    return {
        "person": person, "window_days": days_back,
        "assigned": len(mine), "completed": len(completed), "open": len(open_issues),
        "active_days_total": round(active_secs / 86400, 1),
        "completed_list": sorted(completed, key=lambda i: i.resolved or A.now_utc(), reverse=True),
        "open_list": sorted(open_issues, key=lambda i: i.age_days or 0, reverse=True),
        "avail_types": avail_types, "avail_statuses": avail_statuses,
        "sel_types": sorted(tset), "sel_statuses": sorted(sset), "min_open_age": min_open_age,
    }


# ---------------------------------------------------------------------------
# Report 6 — Status Duration Analysis
# ---------------------------------------------------------------------------

def status_duration(issues, window=None):
    """Average/median time per stage, plus the current worst offenders.

    window=None       -> lifetime time per stage (every ticket's full history).
    window=(start,end) -> only the time each ticket accrued in each stage INSIDE the
                          window, so the page can show "past 24h / 7d / month / range".
    The offenders list is always a *current* snapshot (how long open tickets have sat
    in their present stage), independent of the window.
    """
    per_stage = {}
    for i in issues:
        secs_map = (i.timeline.seconds_in_stage_window(window[0], window[1])
                    if window else i.timeline.seconds_in_stage)
        for stage, secs in secs_map.items():
            if secs > 0:
                per_stage.setdefault(stage, []).append(secs / 86400)
    rows = []
    for stage in cfg.STAGE_ORDER:
        vals = per_stage.get(stage)
        if vals:
            rows.append({"stage": stage, "avg_days": round(mean(vals), 2),
                         "median_days": round(median(vals), 2), "tickets": len(vals)})
    # worst current offenders
    offenders = sorted(
        [i for i in issues if i.is_open],
        key=lambda i: (i.timeline.days_in_stage(i.stage) or 0), reverse=True)[:15]
    return {"rows": rows, "offenders": offenders}


# ---------------------------------------------------------------------------
# Report 7 — Release Readiness
# ---------------------------------------------------------------------------

def release_readiness(version_issues, version_name):
    issues = load_issues(version_issues) if version_issues and isinstance(version_issues[0], dict) else version_issues
    total = len(issues)
    done = sum(1 for i in issues if not i.is_open)
    open_bugs = [i for i in issues if i.is_bug and i.is_open]
    crit = sum(1 for i in open_bugs if i.priority.lower() in ("highest", "critical"))
    high = sum(1 for i in open_bugs if i.priority.lower() == "high")
    pending_qa = [i for i in issues if i.stage in (cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING)]
    pending_stories = [i for i in issues if i.is_open and not i.is_bug]
    blocked = [i for i in issues if i.stage in cfg.BLOCKED_STAGES]
    w = cfg.RISK_WEIGHTS
    risk = (crit * w["critical_bug"] + high * w["high_bug"]
            + len(pending_stories) * w["pending_story"] + len(blocked) * w["blocked"])
    return {
        "version": version_name, "total": total, "done": done,
        "completion_pct": round(100 * done / total) if total else 0,
        "open_critical": crit, "open_high": high,
        "pending_qa": len(pending_qa), "pending_stories": len(pending_stories),
        "blocked": len(blocked), "risk_score": risk,
        "open_bugs_list": open_bugs, "pending_qa_list": pending_qa,
    }


# ---------------------------------------------------------------------------
# Time-in-status — per-ticket breakdown of time spent in each status
# ---------------------------------------------------------------------------

def _status_sort_key(status):
    """Order statuses by their logical stage, then alphabetically."""
    stage = cfg.stage_of(status)
    idx = cfg.STAGE_ORDER.index(stage) if stage in cfg.STAGE_ORDER else 99
    return (idx, status)


def time_in_status(issues, window=None, change_window=None):
    """For each issue, time spent in each status.

    window=None        -> lifetime totals (capped at resolved/now).
    window=(start,end)  -> only the time that accrued INSIDE the window (segment overlap).

    change_window=(start,end) -> only include tickets that had at least one status change
                                 inside that window (i.e. the ticket actually moved during
                                 the timeframe). Tickets that sat still are dropped.

    Returns rows + the ordered union of statuses seen, for a pivot table.
    """
    rows = []
    seen = set()
    for i in issues:
        if change_window:
            cs, ce = change_window
            if not any(cs <= ts < ce for ts, *_ in i.events):
                continue  # ticket didn't move during the timeframe
        if window:
            secs = i.timeline.seconds_in_status_window(window[0], window[1])
        else:
            secs = i.timeline.seconds_in_status
        per = {st: round(s / 86400, 2) for st, s in secs.items() if s > 0}
        if window and not per:
            continue  # ticket didn't overlap the window
        seen |= set(per)
        rows.append({
            "issue": i,
            "per_status": per,
            "current": i.status,
            "total_days": round(sum(secs.values()) / 86400, 2),
        })
    rows.sort(key=lambda r: -r["total_days"])
    statuses = sorted(seen, key=_status_sort_key)
    return {"rows": rows, "statuses": statuses, "count": len(rows)}


# ---------------------------------------------------------------------------
# Report 2 — Sprint Health (uses Agile API payloads; degrades if no board configured)
# ---------------------------------------------------------------------------

def sprint_health(sprints_raw):
    out = []
    for sp in sprints_raw:
        issues = load_issues(sp.get("issues", []))
        total = len(issues)
        done = sum(1 for i in issues if not i.is_open)
        out.append({
            "name": sp.get("name", "Sprint"),
            "total": total, "done": done,
            "completion_pct": round(100 * done / total) if total else 0,
            "remaining": total - done,
            "by_stage": _stage_distribution(issues),
            "spillover": [i for i in issues if i.is_open],
        })
    return out


def _stage_distribution(issues):
    dist = {}
    for i in issues:
        dist[i.stage] = dist.get(i.stage, 0) + 1
    return dist


# ---------------------------------------------------------------------------
# Report 8 — Executive Dashboard (aggregates the rest)
# ---------------------------------------------------------------------------

def executive_dashboard(issues, days_back=7, now=None):
    start, end = window_bounds(days_back, now)
    completed = [i for i in issues if i.resolved and start <= i.resolved < end]
    open_issues = [i for i in issues if i.is_open]
    cycle = [i.timeline.cycle_days for i in completed if i.timeline.cycle_days is not None]
    stuck = [i for i in open_issues
             if (i.timeline.days_in_stage(i.stage) or 0) > cfg.STUCK_THRESHOLD_DAYS]
    open_bugs = [i for i in open_issues if i.is_bug]
    crit = [i for i in open_bugs if i.priority.lower() in ("highest", "critical")]
    blocked = [i for i in open_issues if i.stage in cfg.BLOCKED_STAGES]
    dev = developer_productivity(issues, days_back, now)
    qa = qa_productivity(issues, days_back, now)
    return {
        "window_days": days_back,
        "delivery": {"Completed this week": len(completed),
                     "Median cycle (d)": _med(cycle),
                     "Open work items": len(open_issues),
                     "Blocked": len(blocked)},
        "productivity": {"Active developers": len(dev["rows"]),
                         "Dev output": sum(r["output"] for r in dev["rows"]),
                         "QA verified": sum(r["verified"] for r in qa["rows"])},
        "quality": {"Reopened": sum(r["reopened"] for r in dev["rows"]),
                    "QA rejections": sum(r["rejected"] for r in qa["rows"])},
        "risk": {"Critical bugs": len(crit), "Open bugs": len(open_bugs),
                 f"Stuck > {cfg.STUCK_THRESHOLD_DAYS}d": len(stuck), "Blocked": len(blocked)},
        "stuck_list": sorted(stuck, key=lambda i: (i.timeline.days_in_stage(i.stage) or 0),
                             reverse=True)[:10],
    }
