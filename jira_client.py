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
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

import requests

# One shared session for every Jira call: connection pooling means each request
# reuses an open TLS connection instead of paying a fresh TCP+TLS handshake —
# a full data pull is dozens of paged calls, so this adds up. urllib3's pool is
# thread-safe, so the parallel top-ups below can share it too.
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10,
                                                         pool_maxsize=20))


# ---------------------------------------------------------------------------
# Configuration (read from environment so no secrets live in the code)
# ---------------------------------------------------------------------------

JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "https://lifedata.atlassian.net").rstrip("/")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

# Which project(s) to report on, and how far back "recent activity" looks.
PROJECT_KEYS = os.environ.get("JIRA_PROJECTS", "LIFEDATAV2").split(",")


def configured_projects() -> list:
    """Project keys to query: admin selection from Settings if any, else the env
    default (JIRA_PROJECTS). Lets admins switch between the Support space, the V2
    space, or both without a deploy."""
    try:
        import settings as _st
        sel = _st.load().get("projects")
        if sel:
            return sel
    except Exception:
        pass
    return PROJECT_KEYS
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
_refreshing: set[Any] = set()
_cached_fns: dict[str, Any] = {}
# Bumped by clear_cache(). A fetch started before the bump (e.g. under an old
# project selection) must not be stored after it — its data is already stale.
_cache_gen = 0

# ---- disk persistence: survive restarts/deploys -------------------------
# Every successful fetch is also written to data_dir()/jira_cache.json (all
# results are plain JSON from the Jira API). load_disk_cache() reads it back at
# startup and seeds the in-memory cache — the entries are stale by then, so the
# stale-while-revalidate path serves them instantly while a refresh runs behind.
_persist_lock = threading.Lock()


def _cache_file() -> str:
    import settings as st
    return os.path.join(st.data_dir(), "jira_cache.json")


def _persist_entry(key, ts, result) -> None:
    """Write/replace one entry in the on-disk cache (atomic, best-effort)."""
    try:
        fn_name, args, kwargs = key[0], list(key[1]), [list(kv) for kv in key[2]]
        with _persist_lock:
            try:
                with open(_cache_file()) as fh:
                    disk = json.load(fh)
            except (OSError, ValueError):
                disk = {"entries": []}
            disk["entries"] = [e for e in disk.get("entries", [])
                               if not (e.get("fn") == fn_name and e.get("args") == args
                                       and e.get("kwargs") == kwargs)]
            disk["entries"].append({"fn": fn_name, "args": args, "kwargs": kwargs,
                                    "ts": ts, "result": result})
            path = _cache_file()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(disk, fh)
            os.replace(tmp, path)
    except Exception:
        pass  # persistence is best-effort; the in-memory cache is unaffected


def load_disk_cache() -> None:
    """Seed the in-memory cache from the last persisted fetches, so the first
    pages after a restart/deploy serve instantly (stale, refreshed behind)."""
    try:
        with open(_cache_file()) as fh:
            disk = json.load(fh)
    except (OSError, ValueError):
        return
    with _cache_lock:
        for e in disk.get("entries", []):
            try:
                key = (e["fn"], tuple(e["args"]),
                       tuple((k, v) for k, v in e["kwargs"]))
                if key not in _cache_store:
                    _cache_store[key] = (float(e["ts"]), e["result"])
            except (KeyError, TypeError, ValueError):
                continue


def _refresh_async(fn, args, kwargs, key):
    """Refresh one cache entry in a daemon thread (at most one per key)."""
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            with _cache_lock:
                gen = _cache_gen
            result = fn(*args, **kwargs)
            ts = time.time()
            with _cache_lock:
                if _cache_gen != gen:  # cache cleared mid-fetch: discard result
                    return
                _cache_store[key] = (ts, result)
            _persist_entry(key, ts, result)
        except Exception:
            pass  # keep serving the stale value; try again next request
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True).start()


def _cached(fn):
    """Cache a fetch helper's return value by its arguments (stale-while-revalidate).

    A fresh entry (< CACHE_TTL old) is served straight from memory. A stale entry
    is ALSO served immediately, but triggers a background refresh so the next
    request gets fresh data — so a page load never blocks on a slow Jira pull
    except the very first time a given query is seen (a cold cache).
    """
    _cached_fns[fn.__name__] = fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if CACHE_TTL <= 0:
            return fn(*args, **kwargs)
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        now = time.time()
        with _cache_lock:
            hit = _cache_store.get(key)
        if hit:
            if now - hit[0] >= CACHE_TTL:
                _refresh_async(fn, args, kwargs, key)  # serve stale, refresh behind
            return hit[1]
        with _cache_lock:
            gen = _cache_gen
        result = fn(*args, **kwargs)  # cold cache: must fetch synchronously
        ts = time.time()
        with _cache_lock:
            if _cache_gen != gen:  # cache cleared mid-fetch: serve but don't store
                return result
            _cache_store[key] = (ts, result)
        # persist off-thread so the waiting request isn't delayed by disk I/O
        threading.Thread(target=_persist_entry, args=(key, ts, result),
                         daemon=True).start()
        return result
    return wrapper


def clear_cache() -> None:
    """Drop all cached fetches, memory AND disk (e.g. to force a fresh pull —
    the disk file must go too, or a restart would resurrect data fetched under
    old settings such as a different project selection). Bumps the generation
    so fetches already in flight are discarded instead of stored."""
    global _cache_gen
    with _cache_lock:
        _cache_store.clear()
        _cache_gen += 1
    try:
        with _persist_lock:
            os.remove(_cache_file())
    except OSError:
        pass


def warm_cache() -> None:
    """Pre-fetch the common datasets so the first page after a (re)start is fast.

    Safe to call in a background thread at startup; swallows all errors (e.g. no
    Jira credentials in a test/dev environment)."""
    try:
        fetch_dev_dataset(None)
    except Exception:
        pass
    try:
        detect_custom_fields()
    except Exception:
        pass


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


def _post_with_retry(url, body, attempts=3):
    """FR-D6: respect rate limits — retry on 429/5xx with backoff (Retry-After
    honored when present)."""
    for i in range(attempts):
        resp = _session.post(url, json=body, auth=_auth(),
                             headers={"Accept": "application/json"}, timeout=60)
        if resp.status_code not in (429, 500, 502, 503, 504) or i == attempts - 1:
            return resp
        try:
            wait = float(resp.headers.get("Retry-After", 2 ** i))
        except ValueError:
            wait = 2 ** i
        time.sleep(min(wait, 30))
    return resp


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

        resp = _post_with_retry(url, body)
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
    projects = " ,".join(f'"{p.strip()}"' for p in configured_projects())
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
    projects = " ,".join(f'"{p.strip()}"' for p in configured_projects())
    jql = f'project in ({projects}) AND ({time_clause}) ORDER BY updated DESC'
    return search_issues(jql, REPORT_FIELDS, expand_changelog=True)


# ---------------------------------------------------------------------------
# Enriched dataset for the developer-discipline reports (comments, worklogs,
# estimates, due dates, labels, sprint) — see dev_reports.py.
# ---------------------------------------------------------------------------

DEV_LOOKBACK_DAYS = int(os.environ.get("DEV_REPORTS_MAX_LOOKBACK_DAYS", "365"))
# Only fully page comments/worklogs for issues updated within this many days.
TOPUP_DAYS = int(os.environ.get("DEV_REPORTS_TOPUP_DAYS", "14"))


@_cached
def detect_custom_fields() -> dict:
    """Find the instance's Story Points, Sprint, and Start date field ids by name.
    The resolved start-date id is persisted to settings (admin-overridable there)."""
    out = {"story_points": os.environ.get("STORY_POINT_FIELD") or None,
           "sprint": None, "start_date": None}
    try:
        import settings as st
        out["start_date"] = st.load().get("start_date_field") or None
    except Exception:
        st = None
    try:
        resp = _session.get(f"{JIRA_BASE_URL}/rest/api/3/field", auth=_auth(),
                            headers={"Accept": "application/json"}, timeout=60)
        if resp.ok:
            for f in resp.json():
                name = (f.get("name") or "").lower()
                if not out["story_points"] and name in ("story points", "story point estimate"):
                    out["story_points"] = f.get("id")
                if not out["sprint"] and name == "sprint":
                    out["sprint"] = f.get("id")
                if not out["start_date"] and name in ("start date", "planned start"):
                    out["start_date"] = f.get("id")
    except requests.RequestException:
        pass
    if st and out["start_date"]:
        try:
            s = st.load()
            if s.get("start_date_field") != out["start_date"]:
                s["start_date_field"] = out["start_date"]
                st.save(s)
        except Exception:
            pass
    return out


def _fetch_all_pages(url: str, list_key: str) -> list[dict]:
    out, start = [], 0
    while True:
        r = _session.get(url, params={"startAt": start, "maxResults": 100},
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
        projects = " ,".join(f'"{p.strip()}"' for p in configured_projects())
    cf = detect_custom_fields()
    fields = ["summary", "status", "assignee", "reporter", "issuetype", "priority",
              "created", "updated", "resolutiondate", "duedate", "labels",
              "timeoriginalestimate", "comment", "worklog", "fixVersions"]
    fields += [v for v in (cf["story_points"], cf["sprint"], cf.get("start_date")) if v]
    jql = (f'project in ({projects}) AND ('
           f'statusCategory != Done OR updated >= -{lookback}d) ORDER BY updated DESC')
    issues = search_issues(jql, fields, expand_changelog=True)
    # Top up truncated comment/worklog lists (search returns a capped page), but
    # only for recently-updated issues — a page load blocks on these extra calls,
    # and the "comment today"/EOD checks only look at recent activity, so there's
    # no reason to fully page the history of tickets untouched for weeks. The
    # top-ups are independent per-issue GETs, so they run in parallel.
    cutoff = (now_utc().date() - dt.timedelta(days=TOPUP_DAYS)).isoformat()

    def _top_up(raw):
        f = raw.get("fields", {})
        c = f.get("comment") or {}
        if c.get("total", 0) > len(c.get("comments", [])):
            f["comment"] = {"comments": _fetch_all_pages(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{raw['key']}/comment", "comments")}
        w = f.get("worklog") or {}
        if w.get("total", 0) > len(w.get("worklogs", [])):
            f["worklog"] = {"worklogs": _fetch_all_pages(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{raw['key']}/worklog", "worklogs")}

    recent = [raw for raw in issues
              if (raw.get("fields", {}).get("updated") or "")[:10] >= cutoff]
    if recent:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_top_up, recent))
    return issues


def fetch_single_issue(key: str) -> dict | None:
    """Uncached full fetch of one issue (fields + complete changelog + comments +
    worklogs) for the Ticket Investigator — always fresh, whole history."""
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{key}"
    r = _session.get(url, params={"expand": "changelog"}, auth=_auth(),
                     headers={"Accept": "application/json"}, timeout=60)
    if not r.ok:
        return None
    raw = r.json()
    f = raw.setdefault("fields", {})
    raw.setdefault("changelog", {})
    cl = raw["changelog"]
    if cl.get("total", 0) > len(cl.get("histories", [])):
        cl["histories"] = get_changelog(key)
    f["comment"] = {"comments": _fetch_all_pages(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{key}/comment", "comments")}
    f["worklog"] = {"worklogs": _fetch_all_pages(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{key}/worklog", "worklogs")}
    return raw


@_cached
def list_projects() -> list[dict]:
    """All Jira projects visible to the token: [{key, name}] — for the Settings
    project picker."""
    out = []
    try:
        start = 0
        while True:
            r = _session.get(f"{JIRA_BASE_URL}/rest/api/3/project/search",
                             params={"maxResults": 100, "startAt": start},
                             auth=_auth(), headers={"Accept": "application/json"}, timeout=60)
            if not r.ok:
                break
            data = r.json()
            for pr in data.get("values", []):
                out.append({"key": pr.get("key"), "name": pr.get("name")})
            if data.get("isLast", True) or not data.get("values"):
                break
            start += len(data["values"])
    except Exception:
        pass
    return sorted(out, key=lambda p: (p.get("name") or "").lower())


@_cached
def fetch_project_versions() -> list[dict]:
    out = []
    for p in configured_projects():
        url = f"{JIRA_BASE_URL}/rest/api/3/project/{p.strip()}/versions"
        resp = _session.get(url, auth=_auth(), headers={"Accept": "application/json"},
                            timeout=60)
        if resp.ok:
            out.extend(resp.json())
    return out


@_cached
def fetch_issues_for_version(version_name: str) -> list[dict]:
    projects = " ,".join(f'"{p.strip()}"' for p in configured_projects())
    jql = f'project in ({projects}) AND fixVersion = "{version_name}"'
    return search_issues(jql, REPORT_FIELDS, expand_changelog=False)


@_cached
def fetch_active_sprints() -> list[dict]:
    """Active sprints + their issues, for the Sprint Health report. Requires
    JIRA_BOARD_IDS to be configured; returns [] otherwise (report degrades gracefully)."""
    import settings as _st
    sprints = []
    board_ids = _st.load().get("board_ids") or []
    for board_id in board_ids:
        s_url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/sprint?state=active"
        r = _session.get(s_url, auth=_auth(), headers={"Accept": "application/json"},
                         timeout=60)
        if not r.ok:
            continue
        for sp in r.json().get("values", []):
            i_url = f"{JIRA_BASE_URL}/rest/agile/1.0/sprint/{sp['id']}/issue?maxResults=200"
            ir = _session.get(i_url, auth=_auth(), headers={"Accept": "application/json"},
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
        resp = _session.get(url, params={"startAt": start_at, "maxResults": 100},
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
    projects = " ,".join(f'"{p.strip()}"' for p in configured_projects())
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
