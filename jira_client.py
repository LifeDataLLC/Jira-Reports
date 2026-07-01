"""
jira_client.py
--------------
Talks to the Jira Cloud REST API and turns raw issues + changelogs into the
per-developer metrics our report needs.

Why this exists: paid Jira add-ons (Time in Status, EazyBI, etc.) are essentially
readers of the issue *changelog* — the timestamped history of every status change.
Everything here is computed from that same changelog, so there is no add-on to buy.

Auth: a Jira API token (https://id.atlassian.com/manage-profile/security/api-tokens).
Read-only scopes are sufficient: reading issues + searching. No write access needed.
"""

from __future__ import annotations

import datetime as dt
import functools
import os
import threading
import time
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Configuration (read from environment so no secrets live in the code)
# ---------------------------------------------------------------------------

JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "https://lifedata.atlassian.net").rstrip("/")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

# Which project(s) to report on, and how far back "recent activity" looks.
PROJECT_KEYS = os.environ.get("JIRA_PROJECTS", "LIFEDATAV2").split(",")
WINDOW_DAYS = int(os.environ.get("JIRA_WINDOW_DAYS", "14"))


# ---------------------------------------------------------------------------
# Lightweight TTL cache
# ---------------------------------------------------------------------------
# Jira fetches are the slow part of every page (each issue is pulled WITH its
# changelog, paged at 100/issue). Several report routes call the fetch_* helpers
# below on every request. This argument-keyed cache holds each result for
# JIRA_CACHE_TTL seconds so repeat/concurrent loads are served from memory
# instead of re-hitting Jira. Set JIRA_CACHE_TTL=0 to disable.

CACHE_TTL = int(os.environ.get("JIRA_CACHE_TTL", "300"))
_cache_lock = threading.Lock()
_cache_store: dict[Any, tuple[float, Any]] = {}


def _cached(fn):
    """Cache a fetch helper's return value by its arguments for CACHE_TTL seconds.

    The wrapped function runs outside the lock, so a slow Jira call never blocks
    other requests; at worst two concurrent misses both fetch, which is harmless.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if CACHE_TTL <= 0:
            return fn(*args, **kwargs)
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        now = time.time()
        with _cache_lock:
            hit = _cache_store.get(key)
            if hit and now - hit[0] < CACHE_TTL:
                return hit[1]
        result = fn(*args, **kwargs)
        with _cache_lock:
            _cache_store[key] = (time.time(), result)
        return result
    return wrapper


def clear_cache() -> None:
    """Drop all cached fetches (e.g. to force a fresh pull)."""
    with _cache_lock:
        _cache_store.clear()


# ---------------------------------------------------------------------------
# Low-level REST helpers
# ---------------------------------------------------------------------------

def _auth() -> tuple[str, str]:
    if not (JIRA_EMAIL and JIRA_API_TOKEN):
        raise RuntimeError(
            "Set JIRA_EMAIL and JIRA_API_TOKEN environment variables. "
            "Create a token at id.atlassian.com > Security > API tokens."
        )
    return (JIRA_EMAIL, JIRA_API_TOKEN)


def search_issues(jql: str, fields: list[str], expand_changelog: bool = False) -> list[dict]:
    """
    Run a JQL search and return ALL matching issues, paging through results.

    Uses the enhanced search endpoint (/rest/api/3/search/jql) which is the
    supported one on Jira Cloud and pages with a nextPageToken.
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    issues: list[dict] = []
    next_token: str | None = None

    while True:
        body: dict[str, Any] = {
            "jql": jql,
            "fields": fields,
            "maxResults": 100,
        }
        if expand_changelog:
            body["expand"] = "changelog"
        if next_token:
            body["nextPageToken"] = next_token

        resp = requests.post(url, json=body, auth=_auth(),
                             headers={"Accept": "application/json"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        issues.extend(data.get("issues", []))

        next_token = data.get("nextPageToken")
        if not next_token or data.get("isLast", True):
            break

    return issues


REPORT_FIELDS = ["summary", "status", "assignee", "issuetype", "priority",
                 "created", "resolutiondate", "fixVersions", "updated"]


@_cached
def fetch_working_set(window_days: int | None = None) -> list[dict]:
    """
    One broad pull for the executive reports: every issue in the configured projects
    that is either still open OR was resolved/updated within the window, each WITH its
    changelog. Reports are then computed in-memory from this single dataset.
    """
    window_days = window_days or WINDOW_DAYS
    projects = " ,".join(f'"{p.strip()}"' for p in PROJECT_KEYS)
    jql = (f'project in ({projects}) AND ('
           f'statusCategory != Done OR resolved >= -{window_days}d '
           f'OR updated >= -{window_days}d) ORDER BY updated DESC')
    return search_issues(jql, REPORT_FIELDS, expand_changelog=True)


@_cached
def fetch_issues_by_time(time_clause: str) -> list[dict]:
    """
    Fetch issues (with changelog) matching a JQL time clause, e.g.
    'updated >= -7d' or 'updated >= "2026-06-01" AND updated <= "2026-06-10 23:59"'.
    Used by the per-ticket time-in-status report so any timeframe can be requested.
    """
    projects = " ,".join(f'"{p.strip()}"' for p in PROJECT_KEYS)
    jql = f'project in ({projects}) AND ({time_clause}) ORDER BY updated DESC'
    return search_issues(jql, REPORT_FIELDS, expand_changelog=True)


# ---------------------------------------------------------------------------
# Enriched dataset for the developer-discipline reports (comments, worklogs,
# estimates, due dates, labels, sprint) — see dev_reports.py.
# ---------------------------------------------------------------------------

DEV_LOOKBACK_DAYS = int(os.environ.get("DEV_REPORTS_MAX_LOOKBACK_DAYS", "365"))


@_cached
def detect_custom_fields() -> dict:
    """Find the instance's Story Points and Sprint custom field ids by name."""
    out = {"story_points": os.environ.get("STORY_POINT_FIELD") or None, "sprint": None}
    try:
        resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/field", auth=_auth(),
                            headers={"Accept": "application/json"}, timeout=60)
        if resp.ok:
            for f in resp.json():
                name = (f.get("name") or "").lower()
                if not out["story_points"] and name in ("story points", "story point estimate"):
                    out["story_points"] = f.get("id")
                if not out["sprint"] and name == "sprint":
                    out["sprint"] = f.get("id")
    except requests.RequestException:
        pass
    return out


def _fetch_all_pages(url: str, list_key: str) -> list[dict]:
    out, start = [], 0
    while True:
        r = requests.get(url, params={"startAt": start, "maxResults": 100},
                         auth=_auth(), headers={"Accept": "application/json"}, timeout=60)
        if not r.ok:
            break
        data = r.json()
        vals = data.get(list_key, [])
        out.extend(vals)
        if start + len(vals) >= data.get("total", 0) or not vals:
            break
        start += len(vals)
    return out


@_cached
def fetch_dev_dataset(project: str | None = None, lookback_days: int | None = None) -> list[dict]:
    """
    One broad pull for the 18 developer reports: every open issue plus anything
    updated within the lookback, WITH changelog, comments, worklogs, and planning
    fields. Comments/worklogs truncated by the search API are topped up per-issue.
    """
    lookback = lookback_days or DEV_LOOKBACK_DAYS
    if project:
        projects = f'"{project.strip()}"'
    else:
        projects = " ,".join(f'"{p.strip()}"' for p in PROJECT_KEYS)
    cf = detect_custom_fields()
    fields = ["summary", "status", "assignee", "reporter", "issuetype", "priority",
              "created", "updated", "resolutiondate", "duedate", "labels",
              "timeoriginalestimate", "comment", "worklog"]
    fields += [v for v in (cf["story_points"], cf["sprint"]) if v]
    jql = (f'project in ({projects}) AND ('
           f'statusCategory != Done OR updated >= -{lookback}d) ORDER BY updated DESC')
    issues = search_issues(jql, fields, expand_changelog=True)
    # Top up truncated comment/worklog lists (search returns a capped page).
    for raw in issues:
        f = raw.get("fields", {})
        c = f.get("comment") or {}
        if c.get("total", 0) > len(c.get("comments", [])):
            f["comment"] = {"comments": _fetch_all_pages(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{raw['key']}/comment", "comments")}
        w = f.get("worklog") or {}
        if w.get("total", 0) > len(w.get("worklogs", [])):
            f["worklog"] = {"worklogs": _fetch_all_pages(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{raw['key']}/worklog", "worklogs")}
    return issues


@_cached
def fetch_project_versions() -> list[dict]:
    out = []
    for p in PROJECT_KEYS:
        url = f"{JIRA_BASE_URL}/rest/api/3/project/{p.strip()}/versions"
        resp = requests.get(url, auth=_auth(), headers={"Accept": "application/json"},
                            timeout=60)
        if resp.ok:
            out.extend(resp.json())
    return out


@_cached
def fetch_issues_for_version(version_name: str) -> list[dict]:
    projects = " ,".join(f'"{p.strip()}"' for p in PROJECT_KEYS)
    jql = f'project in ({projects}) AND fixVersion = "{version_name}"'
    return search_issues(jql, REPORT_FIELDS, expand_changelog=False)


@_cached
def fetch_active_sprints() -> list[dict]:
    """Active sprints + their issues, for the Sprint Health report. Requires
    JIRA_BOARD_IDS to be configured; returns [] otherwise (report degrades gracefully)."""
    import config as cfg
    sprints = []
    for board_id in cfg.BOARD_IDS:
        s_url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/sprint?state=active"
        r = requests.get(s_url, auth=_auth(), headers={"Accept": "application/json"},
                         timeout=60)
        if not r.ok:
            continue
        for sp in r.json().get("values", []):
            i_url = f"{JIRA_BASE_URL}/rest/agile/1.0/sprint/{sp['id']}/issue?maxResults=200"
            ir = requests.get(i_url, auth=_auth(), headers={"Accept": "application/json"},
                              timeout=60)
            sp["issues"] = ir.json().get("issues", []) if ir.ok else []
            sprints.append(sp)
    return sprints


def get_changelog(issue_key: str) -> list[dict]:
    """Fetch the full status/field history for one issue, paging if needed."""
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/changelog"
    histories: list[dict] = []
    start_at = 0
    while True:
        resp = requests.get(url, params={"startAt": start_at, "maxResults": 100},
                            auth=_auth(), headers={"Accept": "application/json"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        histories.extend(data.get("values", []))
        if data.get("isLast", True) or start_at + data.get("maxResults", 0) >= data.get("total", 0):
            break
        start_at += data.get("maxResults", 100)
    return histories


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_ts(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def days_between(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    if not (a and b):
        return None
    return round((b - a).total_seconds() / 86400, 1)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Changelog analysis — the heart of the metrics
# ---------------------------------------------------------------------------

def status_transitions(changelog: list[dict]) -> list[tuple[dt.datetime, str]]:
    """
    Return [(timestamp, to_status_name), ...] for every status change,
    sorted oldest-first. Works with both the search 'changelog.histories'
    shape and the /changelog 'values' shape.
    """
    out: list[tuple[dt.datetime, str]] = []
    for h in changelog:
        ts = parse_ts(h.get("created"))
        if not ts:
            continue
        for item in h.get("items", []):
            if item.get("field") == "status":
                out.append((ts, item.get("toString") or ""))
    out.sort(key=lambda x: x[0])
    return out


# These are the status names in the LIFEDATAV2 workflow whose category is
# "In Progress" (Jira statusCategory key = 'indeterminate'). We key off the
# statusCategory wherever possible, but keep this set as a readable reference.
IN_PROGRESS_STATUSES = {
    "In Progress / Start Investigation",
    "Development / In Design",
    "In QA Testing (QA Env)",
    "In Staging Testing",
    "Ready for QA (QA Env)",
    "Passed QA (Staging Ready)",
    "Ready for Staging Verification",
}


def first_active_entry(transitions, in_progress_names) -> dt.datetime | None:
    """Timestamp the ticket first entered any 'In Progress' category status."""
    for ts, name in transitions:
        if name in in_progress_names:
            return ts
    return None


def last_status_change(transitions) -> dt.datetime | None:
    """Timestamp of the most recent status change (= when current status began)."""
    return transitions[-1][0] if transitions else None


# ---------------------------------------------------------------------------
# Building the report
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    key: str
    summary: str
    assignee: str
    issue_type: str
    status: str
    status_category: str
    created: dt.datetime | None
    resolved: dt.datetime | None
    lead_days: float | None = None        # created -> resolved (calendar)
    cycle_days: float | None = None       # first In Progress -> resolved (active)
    days_in_status: float | None = None   # now - last status change (aging)
    age_days: float | None = None         # now - created (how long the ticket has been open)
    active_days: float | None = None      # total time in active/in-progress stages = time worked
    reopened: int = 0                     # times sent back / reopened after QA or Done (rework)

    @property
    def url(self) -> str:
        return f"{JIRA_BASE_URL}/browse/{self.key}"


@dataclass
class DeveloperReport:
    name: str
    completed: list[Ticket] = field(default_factory=list)
    in_progress: list[Ticket] = field(default_factory=list)
    assigned: list[Ticket] = field(default_factory=list)  # all open tickets assigned now

    @property
    def throughput(self) -> int:
        return len(self.completed)

    @property
    def open_count(self) -> int:
        return len(self.assigned)

    def _avg(self, vals):
        vals = [v for v in vals if v is not None]
        return round(mean(vals), 1) if vals else None

    def _med(self, vals):
        vals = [v for v in vals if v is not None]
        return round(median(vals), 1) if vals else None

    @property
    def avg_cycle(self):
        return self._avg([t.cycle_days for t in self.completed])

    @property
    def median_cycle(self):
        return self._med([t.cycle_days for t in self.completed])

    @property
    def avg_lead(self):
        return self._avg([t.lead_days for t in self.completed])

    @property
    def oldest_in_progress(self):
        ages = [t.days_in_status for t in self.in_progress if t.days_in_status is not None]
        return round(max(ages), 1) if ages else None


def _to_ticket(raw: dict) -> Ticket:
    f = raw.get("fields", {})
    status = f.get("status", {}) or {}
    cat = (status.get("statusCategory", {}) or {}).get("name", "")
    assignee = (f.get("assignee") or {}).get("displayName", "Unassigned")
    return Ticket(
        key=raw.get("key", ""),
        summary=f.get("summary", ""),
        assignee=assignee,
        issue_type=(f.get("issuetype", {}) or {}).get("name", ""),
        status=status.get("name", ""),
        status_category=cat,
        created=parse_ts(f.get("created")),
        resolved=parse_ts(f.get("resolutiondate")),
    )


def build_report(fetch_changelogs: bool = True) -> dict[str, DeveloperReport]:
    """
    Pull completed + in-progress tickets for the configured projects and
    compute per-developer metrics. Returns {developer_name: DeveloperReport}.
    """
    projects = " ,".join(f'"{p.strip()}"' for p in PROJECT_KEYS)
    fields = ["summary", "status", "assignee", "issuetype", "created", "resolutiondate"]

    completed_raw = search_issues(
        f'project in ({projects}) AND statusCategory = Done '
        f'AND resolved >= -{WINDOW_DAYS}d ORDER BY resolved DESC',
        fields, expand_changelog=fetch_changelogs)

    inprogress_raw = search_issues(
        f'project in ({projects}) AND statusCategory = "In Progress" '
        f'ORDER BY updated DESC',
        fields, expand_changelog=fetch_changelogs)

    # Every OPEN (unresolved) ticket currently assigned to someone — the full
    # workload per developer, including To Do items not yet started. No changelog
    # needed here, so this stays fast even with large backlogs.
    assigned_raw = search_issues(
        f'project in ({projects}) AND statusCategory != Done '
        f'AND assignee is not EMPTY ORDER BY assignee, status',
        fields, expand_changelog=False)

    reports: dict[str, DeveloperReport] = {}

    def report_for(name: str) -> DeveloperReport:
        return reports.setdefault(name, DeveloperReport(name=name))

    import analytics as A
    import config as cfg
    for raw in completed_raw:
        t = _to_ticket(raw)
        t.lead_days = days_between(t.created, t.resolved)
        if fetch_changelogs:
            hist = raw.get("changelog", {}).get("histories", [])
            tr = status_transitions(hist)
            start = first_active_entry(tr, IN_PROGRESS_STATUSES)
            t.cycle_days = days_between(start, t.resolved)
            f = raw.get("fields", {})
            tl = A.analyze(hist, f.get("created"), f.get("resolutiondate"),
                           t.status, t.status_category)
            t.reopened = tl.reopened_count
        report_for(t.assignee).completed.append(t)

    for raw in inprogress_raw:
        t = _to_ticket(raw)
        if fetch_changelogs:
            hist = raw.get("changelog", {}).get("histories", [])
            tr = status_transitions(hist)
            t.days_in_status = days_between(last_status_change(tr), now_utc())
            # Total time the ticket has spent in active (in-progress) stages, i.e. how
            # long it's actually been worked — paused/blocked time is excluded.
            f = raw.get("fields", {})
            tl = A.analyze(hist, f.get("created"), f.get("resolutiondate"),
                           t.status, t.status_category)
            active = sum(tl.seconds_in_stage.get(s, 0) for s in cfg.ACTIVE_STAGES)
            t.active_days = round(active / 86400, 1) if active else None
            t.reopened = tl.reopened_count
        report_for(t.assignee).in_progress.append(t)

    for raw in assigned_raw:
        t = _to_ticket(raw)
        t.age_days = days_between(t.created, now_utc())
        report_for(t.assignee).assigned.append(t)

    return reports
