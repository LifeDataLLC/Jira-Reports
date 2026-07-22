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
    duedate: dt.date | None = None

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
    def has_release(self):
        return bool(self.fix_versions)


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
            duedate=_parse_date(f.get("duedate")),
        ))
    return issues


def _parse_date(s):
    """Jira due dates are plain 'YYYY-MM-DD' strings."""
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


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
# Employee Activity History — full per-ticket drill-down for one person
# ---------------------------------------------------------------------------

def employee_history(issues, person, since_days=None):
    """Complete activity history for one employee.

    Returns every ticket the person is currently assigned to OR performed a status
    transition on, and for each ticket: total time, the time spent in each status,
    and the full status-transition log (timestamp, from -> to, who moved it).

    Attribution: a ticket counts as "worked on" if the person is the current assignee
    or appears as the author of any status change in its history.

    since_days: if set, only include tickets whose most recent status change (or, if
    a ticket never moved, its creation) is within this many days — so a lookback of
    30d shows tickets actually touched in the last 30 days, not every open ticket.
    """
    def authored(i):
        return any(author == person for (_ts, author, _f, _t) in i.events)

    def last_change(i):
        return i.events[-1][0] if i.events else (i.resolved or i.created)

    worked = [i for i in issues if i.assignee == person or authored(i)]
    if since_days:
        cutoff = A.now_utc() - dt.timedelta(days=since_days)
        worked = [i for i in worked if last_change(i) and last_change(i) >= cutoff]

    tickets = []
    total_active = 0.0
    for i in worked:
        per_status = sorted(
            ({"status": s, "days": round(secs / 86400, 2)}
             for s, secs in i.timeline.seconds_in_status.items() if secs > 0),
            key=lambda r: -r["days"])
        # Per-stage breakdown in workflow order, for the stage-journey bar.
        stage_secs = i.timeline.seconds_in_stage
        stage_total = sum(stage_secs.values()) or 1
        stages = [{"stage": st, "days": round(stage_secs[st] / 86400, 2),
                   "pct": round(100 * stage_secs[st] / stage_total, 1)}
                  for st in cfg.STAGE_ORDER if stage_secs.get(st, 0) > 0]
        active = sum(i.timeline.seconds_in_stage.get(s, 0) for s in cfg.ACTIVE_STAGES)
        total_active += active
        transitions = [{"ts": ts, "author": author, "from": frm or "—", "to": to}
                       for (ts, author, frm, to) in i.events]
        last_activity = i.events[-1][0] if i.events else (i.resolved or i.created)
        tickets.append({
            "issue": i,
            "active_days": round(active / 86400, 2) if active else None,
            "total_days": round(sum(i.timeline.seconds_in_status.values()) / 86400, 2),
            "per_status": per_status,
            "stages": stages,
            "transitions": transitions,
            "moves": len(transitions),
            "reopened": i.timeline.reopened_count,
            "qa_rejections": i.timeline.qa_rejections,
            "days_in_current_stage": i.timeline.days_in_stage(i.stage) if i.is_open else None,
            "last_activity": last_activity,
        })
    tickets.sort(key=lambda t: t["last_activity"] or A.now_utc(), reverse=True)

    # Aggregate for a plain-language insight line.
    agg_stage = {}
    reopened_tickets = stuck_tickets = 0
    for t in tickets:
        for s in t["stages"]:
            agg_stage[s["stage"]] = agg_stage.get(s["stage"], 0) + s["days"]
        if t["reopened"]:
            reopened_tickets += 1
        if t["days_in_current_stage"] and t["days_in_current_stage"] >= 10:
            stuck_tickets += 1
    top_stage = max(agg_stage, key=agg_stage.get) if agg_stage else None
    insight = {
        "top_stage": top_stage,
        "top_stage_days": round(agg_stage[top_stage], 1) if top_stage else None,
        "reopened_tickets": reopened_tickets,
        "stuck_tickets": stuck_tickets,
    }
    return {
        "person": person,
        "ticket_count": len(tickets),
        "active_days_total": round(total_active / 86400, 1),
        "insight": insight,
        "tickets": tickets,
    }


# ---------------------------------------------------------------------------
# Report 6 — Status Duration Analysis
# ---------------------------------------------------------------------------

def status_duration(issues, window=None, exclude_stuck_days=None):
    """Average/median time per stage, plus the current worst offenders.

    window=None       -> lifetime time per stage (every ticket's full history).
    window=(start,end) -> only the time each ticket accrued in each stage INSIDE the
                          window, so the page can show "past 24h / 7d / month / range".
    exclude_stuck_days -> if set, open tickets currently sitting in their present stage
                          for >= this many days are dropped from the per-stage averages
                          (so a few languishing tickets don't skew the typical-flow
                          numbers). The offenders list below is left untouched.
    The offenders list is always a *current* snapshot (how long open tickets have sat
    in their present stage), independent of the window.
    """
    def is_stuck(i):
        return (exclude_stuck_days is not None and i.is_open
                and (i.timeline.days_in_stage(i.stage) or 0) >= exclude_stuck_days)

    per_stage = {}
    excluded = 0
    for i in issues:
        if is_stuck(i):
            excluded += 1
            continue
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
    return {"rows": rows, "offenders": offenders,
            "excluded_stuck": excluded, "exclude_stuck_days": exclude_stuck_days}


# ---------------------------------------------------------------------------
# Report 7 — Release Readiness
# ---------------------------------------------------------------------------

# Linear pipeline stages (excludes the Paused and Reopened overlays), earliest->latest.
_LINEAR_STAGES = [
    cfg.STAGE_TODO, cfg.STAGE_IN_PROGRESS, cfg.STAGE_DEVELOPMENT,
    cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING, cfg.STAGE_STAGING,
    cfg.STAGE_PROD_READY, cfg.STAGE_PRODUCTION, cfg.STAGE_DONE,
]
_LIN_IDX = {s: i for i, s in enumerate(_LINEAR_STAGES)}

# Each pipeline milestone and the linear stage a ticket must reach to count for it.
_MILESTONES = [
    ("dev_completed",  "Development completed", cfg.STAGE_READY_FOR_QA),
    ("passed_qa",      "Passed QA",             cfg.STAGE_STAGING),
    ("passed_staging", "Passed staging",        cfg.STAGE_PROD_READY),
    ("in_production",  "Live in production",    cfg.STAGE_PRODUCTION),
    ("done",           "Done",                  cfg.STAGE_DONE),
]

# Gate thresholds for the release verdict (tune here).
RR_GATES = {
    "high_bugs_max": 2,
    "bounce_rate_max": 0.15,
    "not_started_max": 8,
    "throughput_window_days": 21,
}


def _furthest_index(issue):
    """Highest linear-stage index the ticket has ever reached (from the changelog),
    so a paused/blocked ticket still counts at the furthest milestone it passed."""
    idxs = [_LIN_IDX[s] for s in issue.timeline.stage_first_entry if s in _LIN_IDX]
    if issue.stage in _LIN_IDX:
        idxs.append(_LIN_IDX[issue.stage])
    return max(idxs) if idxs else 0


def _age_in_status(issue):
    d = issue.timeline.days_in_status(issue.status)
    return round(d) if d is not None else None


def release_readiness(version_issues, version_name, release_date=None, now=None):
    """Release readiness for one fix version: a pipeline funnel (how far the batch
    has progressed), throughput-based projection, coverage gaps, and an auditable
    set of ship gates that roll up to a GO / AT RISK / NO-GO verdict.

    release_date: the version's Jira releaseDate (a date), or None.
    """
    issues = (load_issues(version_issues)
              if version_issues and isinstance(version_issues[0], dict) else version_issues)
    now = now or A.now_utc()
    today = now.date()
    total = len(issues)
    pct = lambda n: round(100 * n / total) if total else 0

    # --- Pipeline funnel: cumulative "reached this milestone or beyond" ---
    counts = {mid: 0 for mid, _l, _s in _MILESTONES}
    for i in issues:
        fi = _furthest_index(i)
        for mid, _l, stg in _MILESTONES:
            if fi >= _LIN_IDX[stg]:
                counts[mid] += 1
    funnel = [{"id": mid, "label": lbl, "count": counts[mid], "pct": pct(counts[mid])}
              for mid, lbl, _s in _MILESTONES]
    dev_done = counts["dev_completed"]

    # --- Bugs / blockers ---
    open_bugs = [i for i in issues if i.is_bug and i.is_open]
    crit = [i for i in open_bugs if i.priority.lower() in ("highest", "critical")]
    high = [i for i in open_bugs if i.priority.lower() == "high"]
    blocked = [i for i in issues if i.is_open and i.stage in cfg.BLOCKED_STAGES]

    # --- Coverage gaps (open tickets only) ---
    open_issues = [i for i in issues if i.is_open]
    missing_due = [i for i in open_issues if i.duedate is None]
    no_release = [i for i in open_issues if not i.has_release]
    not_started = [i for i in open_issues if i.stage == cfg.STAGE_TODO]
    unassigned = [i for i in open_issues if i.assignee == "Unassigned"]

    # --- Ownership: open tickets per assignee ---
    own = {}
    for i in open_issues:
        own[i.assignee] = own.get(i.assignee, 0) + 1
    ownership = sorted(({"name": k, "count": v} for k, v in own.items()),
                       key=lambda r: -r["count"])[:8]

    # --- Throughput (dev-completions/week over the window) & projection ---
    win = now - dt.timedelta(days=RR_GATES["throughput_window_days"])
    dev_completions = sum(
        1 for i in issues
        if (ts := i.timeline.stage_first_entry.get(cfg.STAGE_READY_FOR_QA)) and ts >= win)
    weeks = RR_GATES["throughput_window_days"] / 7.0
    throughput = dev_completions / weeks
    remaining_dev = total - dev_done
    proj_date = None
    if remaining_dev <= 0:
        proj_date = today
    elif throughput > 0:
        proj_date = today + dt.timedelta(days=round(remaining_dev / throughput * 7))
    proj_delta = (proj_date - release_date).days if (proj_date and release_date) else None

    # --- QA bounce rate ---
    reached_qa = sum(
        1 for i in issues
        if any(s in i.timeline.stage_first_entry
               for s in (cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING)))
    qa_stages = {cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING, cfg.STAGE_STAGING}
    back = {cfg.STAGE_IN_PROGRESS, cfg.STAGE_DEVELOPMENT, cfg.STAGE_REOPENED}
    bounced = sum(
        1 for i in issues
        if any(frm in qa_stages and to in back for _ts, frm, to in i.timeline.transitions))
    bounce_rate = (bounced / reached_qa) if reached_qa else 0.0

    days_to_target = (release_date - today).days if release_date else None

    # --- Ship gates -> verdict ---
    def g(name, sub, measure, value, status, level="warn"):
        return {"name": name, "sub": sub, "measure": measure,
                "value": value, "status": status, "level": level}

    gates = [
        g("Open critical bugs", "Priority Highest/Critical, still open",
          "must be 0 to ship", len(crit), "bad" if crit else "ok", level="block"),
        g("Open high bugs", "Priority High, still open",
          f"≤ {RR_GATES['high_bugs_max']}", len(high),
          "warn" if len(high) > RR_GATES["high_bugs_max"] else "ok"),
        g("Blocked tickets", "Blocked / Customer Feedback / Cannot Reproduce / paused",
          "0", len(blocked), "warn" if blocked else "ok"),
        g("QA bounce rate", "Returned from QA ÷ reached QA",
          f"< {round(RR_GATES['bounce_rate_max']*100)}%", f"{round(bounce_rate*100)}%",
          "warn" if bounce_rate >= RR_GATES["bounce_rate_max"] else "ok"),
    ]
    if proj_delta is not None:
        gates.append(g("Schedule confidence", "Projected dev-complete vs. target date",
                       "on/before target",
                       f"{'+' if proj_delta > 0 else ''}{proj_delta}d",
                       "warn" if proj_delta > 0 else "ok"))
    gates.append(g("Not started", "Tickets still in To Do",
                   f"≤ {RR_GATES['not_started_max']}", len(not_started),
                   "warn" if len(not_started) > RR_GATES["not_started_max"] else "ok"))
    gates.append(g("Due date & release set",
                   "Team policy: every open ticket needs both until resolved/done",
                   "0 missing", len(missing_due) + len(no_release),
                   "bad" if (missing_due or no_release) else "ok"))

    if any(x["status"] == "bad" and x["level"] == "block" for x in gates):
        verdict = "NO-GO"
    elif any(x["status"] in ("bad", "warn") for x in gates):
        verdict = "AT RISK"
    else:
        verdict = "GO"

    # --- Verdict reasons (banner) ---
    reasons = []
    if crit:
        reasons.append(("bad", f"{len(crit)} open critical bug"
                        f"{'s' if len(crit) != 1 else ''} — must be 0 before release"))
    if proj_delta is not None and proj_delta > 0:
        reasons.append(("warn", f"Projected dev-complete {proj_date:%b %-d}, "
                        f"{proj_delta} day{'s' if proj_delta != 1 else ''} past the target"))
    if missing_due or no_release:
        n = len(missing_due) + len(no_release)
        reasons.append(("bad", f"{n} open ticket{'s' if n != 1 else ''} "
                        "missing a due date or release"))
    if blocked:
        reasons.append(("warn", f"{len(blocked)} blocked ticket"
                        f"{'s' if len(blocked) != 1 else ''}"))
    if len(high) > RR_GATES["high_bugs_max"]:
        reasons.append(("warn", f"{len(high)} open high-priority bugs"))
    if not reasons:
        reasons.append(("ok", "All ship gates pass — clear to release"))

    # --- Development burn-up: weekly cumulative tickets reaching dev-complete ---
    burnup = []
    for w in range(8, -1, -1):
        wk_end = now - dt.timedelta(days=7 * w)
        c = sum(1 for i in issues
                if (ts := i.timeline.stage_first_entry.get(cfg.STAGE_READY_FOR_QA)) and ts <= wk_end)
        burnup.append({"days_ago": 7 * w, "count": c})

    # --- Must-clear list: open criticals/highs + blocked, oldest first ---
    def _row(i, tag, cls):
        return {"key": i.key, "url": i.url, "summary": i.summary,
                "tag": tag, "cls": cls, "age": _age_in_status(i)}
    must_clear = ([_row(i, "Critical", "bad") for i in crit]
                  + [_row(i, "High", "warn") for i in high]
                  + [_row(i, "Blocked", "") for i in blocked])
    must_clear.sort(key=lambda r: -(r["age"] or 0))

    return {
        "version": version_name, "release_date": release_date,
        "days_to_target": days_to_target, "total": total, "verdict": verdict,
        "reasons": reasons[:3], "funnel": funnel,
        "dev_completed": dev_done, "dev_completed_pct": pct(dev_done),
        "passed_staging": counts["passed_staging"], "passed_staging_pct": pct(counts["passed_staging"]),
        "throughput": round(throughput, 1), "remaining_dev": remaining_dev,
        "proj_date": proj_date, "proj_delta": proj_delta,
        "proj_days": (proj_date - today).days if proj_date else None,
        "open_critical": len(crit), "open_high": len(high), "blocked": len(blocked),
        "bounce_rate": round(bounce_rate * 100),
        "not_started": len(not_started), "missing_due": len(missing_due),
        "no_release": len(no_release), "unassigned": len(unassigned),
        "gates": gates, "ownership": ownership, "must_clear": must_clear[:12],
        "burnup": burnup,
        # legacy keys kept so older callers/tests keep working
        "done": counts["done"], "completion_pct": pct(counts["done"]),
        "risk_score": len(crit) * 10 + len(high) * 5 + len(blocked) * 3,
        "open_bugs_list": open_bugs,
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
