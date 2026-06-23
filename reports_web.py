"""
reports_web.py
--------------
Flask Blueprint exposing the eight Executive Reporting Framework reports as web pages,
plus a JSON feed. Registered by app.py. Shares one cached dataset (issues + changelogs)
across all report pages so a page load is fast and easy on the Jira API.
"""

from __future__ import annotations

import time

import csv
import datetime as dt
import io

from flask import Blueprint, Response, jsonify, render_template_string, request

import config as cfg
import jira_client as jc
import reports as R
import analytics as A

bp = Blueprint("reports", __name__)

# ---- shared cached dataset ----
_DS = {"issues": None, "ts": 0.0}
_TTL = 300


def dataset():
    if not _DS["issues"] or time.time() - _DS["ts"] > _TTL:
        _DS["issues"] = R.load_issues(jc.fetch_working_set())
        _DS["ts"] = time.time()
    return _DS["issues"]


def fmt(v, s="d"):
    return f"{v}{s}" if v is not None else "—"


# ---------------------------------------------------------------------------
# Shared chrome (CSS + nav)
# ---------------------------------------------------------------------------

TOP = """
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#172b4d;background:#f4f5f7}
 nav{background:#0747a6;padding:10px 20px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
 nav a{color:#dbe7ff;text-decoration:none;font-size:13px;padding:5px 10px;border-radius:5px}
 nav a:hover,nav a.active{background:#fff;color:#0747a6}
 nav .brand{color:#fff;font-weight:700;margin-right:10px}
 .wrap{max-width:1150px;margin:22px auto;padding:0 20px}
 h1{font-size:20px;margin:0 0 4px}.sub{color:#6b778c;font-size:13px;margin-bottom:18px}
 h2{font-size:15px;margin:26px 0 10px}
 .cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
 .card{background:#fff;border-radius:8px;padding:14px 18px;box-shadow:0 1px 3px rgba(9,30,66,.12);flex:1;min-width:150px}
 .card .n{font-size:26px;font-weight:700}.card .l{color:#6b778c;font-size:12px;margin-top:2px}
 .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:8px}
 th,td{text-align:left;padding:9px 13px;border-bottom:1px solid #ebecf0;font-size:13px}
 th{background:#fafbfc;color:#6b778c}tr:hover td{background:#f7f8fa}
 a{color:#0052cc;text-decoration:none}a:hover{text-decoration:underline}
 .pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#dfe1e6}
 .warn{background:#ffebe6;color:#bf2600}.ok{background:#e3fcef;color:#006644}
 .muted{color:#6b778c;font-size:12px}
 .sectionbox{background:#fff;border-radius:8px;padding:16px 18px;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:16px}
</style>
<nav>
 <span class="brand">LifeData Eng Reports</span>
 <a href="/exec">Executive</a>
 <a href="/reports/daily">Daily Movement</a>
 <a href="/reports/developers">Developers</a>
 <a href="/reports/qa">QA</a>
 <a href="/reports/status-duration">Status Duration</a>
 <a href="/reports/time-in-status">Time in Status</a>
 <a href="/reports/release">Release</a>
 <a href="/reports/sprints">Sprints</a>
 <a href="/">Workload (v0)</a>
</nav>
<div class="wrap">
"""
BOT = "</div>"


def page(body, **ctx):
    return render_template_string(TOP + body + BOT, fmt=fmt, cfg=cfg, **ctx)


# ---------------------------------------------------------------------------
# Report 8 — Executive dashboard
# ---------------------------------------------------------------------------

EXEC = """
<h1>Executive Engineering Dashboard</h1>
<div class="sub">{{ projects }} · last {{ d.window_days }} days · generated {{ now }}</div>
{% for group,label in [('delivery','Delivery'),('productivity','Productivity'),('quality','Quality'),('risk','Risk')] %}
  <h2>{{ label }}</h2>
  <div class="cards">
    {% for k,v in d[group].items() %}
      <div class="card"><div class="n">{{ v if v is not none else '—' }}</div><div class="l">{{ k }}</div></div>
    {% endfor %}
  </div>
{% endfor %}
<h2>Top stuck items</h2>
<table><tr><th>Key</th><th>Summary</th><th>Stage</th><th>Days in stage</th><th>Assignee</th></tr>
{% for i in d.stuck_list %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td>
<td>{{ i.stage }}</td><td><span class="pill warn">{{ fmt(i.timeline.days_in_stage(i.stage)) }}</span></td>
<td>{{ i.assignee }}</td></tr>
{% else %}<tr><td colspan="5" class="muted">Nothing stuck beyond threshold.</td></tr>{% endfor %}
</table>
"""


@bp.route("/exec")
def exec_dashboard():
    d = R.executive_dashboard(dataset(), days_back=cfg.STUCK_THRESHOLD_DAYS if False else 7)
    return page(EXEC, d=d, projects=", ".join(jc.PROJECT_KEYS),
                now=time.strftime("%Y-%m-%d %H:%M"))


# ---------------------------------------------------------------------------
# Report 1 — Daily movement
# ---------------------------------------------------------------------------

DAILY = """
<h1>Daily Work Movement</h1>
<div class="sub">{{ d.day_start.strftime('%Y-%m-%d %H:%M') }} → {{ d.day_end.strftime('%H:%M') }}</div>
<div class="cards">
{% for k,v in d.counts.items() %}<div class="card"><div class="n">{{ v }}</div><div class="l">{{ k }}</div></div>{% endfor %}
</div>
<h2>Tickets that moved</h2>
<table><tr><th>Key</th><th>Summary</th><th>Current stage</th><th>Assignee</th></tr>
{% for i in d.moved %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td>
<td>{{ i.stage }}</td><td>{{ i.assignee }}</td></tr>
{% else %}<tr><td colspan="4" class="muted">No movement in window.</td></tr>{% endfor %}
</table>
"""


@bp.route("/reports/daily")
def daily():
    return page(DAILY, d=R.daily_movement(dataset()))


# ---------------------------------------------------------------------------
# Report 3 — Developer productivity
# ---------------------------------------------------------------------------

DEV = """
<h1>Developer Productivity</h1>
<div class="sub">Last {{ d.window_days }} days · output = moved to {{ outstage }} · attributed to the person who made the move</div>
<table><tr><th>Developer</th><th>Output</th><th>Avg dev duration</th><th>Reopened</th><th>Quality score</th></tr>
{% for r in d.rows %}
<tr><td>{{ r.name }}</td><td>{{ r.output }}</td><td>{{ fmt(r.avg_dev_days) }}</td>
<td>{{ r.reopened }}</td>
<td>{% if r.quality_score is not none %}<span class="pill {{ 'ok' if r.quality_score>=0.8 else 'warn' }}">{{ r.quality_score }}</span>{% else %}—{% endif %}</td></tr>
{% else %}<tr><td colspan="5" class="muted">No developer output in window.</td></tr>{% endfor %}
</table>
<p class="muted">Quality score = Completed / (Completed + Reopened). A flow indicator, not a performance score.</p>
"""


@bp.route("/reports/developers")
def developers():
    win = int(request.args.get("days", jc.WINDOW_DAYS))
    return page(DEV, d=R.developer_productivity(dataset(), win), outstage=cfg.DEV_OUTPUT_STAGE)


# ---------------------------------------------------------------------------
# Report 4 — QA productivity
# ---------------------------------------------------------------------------

QA = """
<h1>QA Productivity</h1>
<div class="sub">Last {{ d.window_days }} days · attributed to the person who performed the QA transition</div>
<table><tr><th>QA</th><th>Verified</th><th>Rejected</th><th>Rejection rate</th><th>Avg testing duration</th></tr>
{% for r in d.rows %}
<tr><td>{{ r.name }}</td><td>{{ r.verified }}</td><td>{{ r.rejected }}</td>
<td>{{ r.rejection_rate if r.rejection_rate is not none else '—' }}</td><td>{{ fmt(r.avg_test_days) }}</td></tr>
{% else %}<tr><td colspan="5" class="muted">No QA activity in window.</td></tr>{% endfor %}
</table>
"""


@bp.route("/reports/qa")
def qa():
    win = int(request.args.get("days", jc.WINDOW_DAYS))
    return page(QA, d=R.qa_productivity(dataset(), win))


# ---------------------------------------------------------------------------
# Report 6 — Status duration
# ---------------------------------------------------------------------------

SD = """
<h1>Status Duration Analysis</h1>
<div class="sub">Where tickets spend time, and the current worst offenders</div>
<h2>Average time per stage</h2>
<table><tr><th>Stage</th><th>Avg days</th><th>Median days</th><th>Tickets</th></tr>
{% for r in d.rows %}
<tr><td>{{ r.stage }}</td><td>{{ r.avg_days }}</td><td>{{ r.median_days }}</td><td>{{ r.tickets }}</td></tr>
{% endfor %}
</table>
<h2>Currently stuck the longest</h2>
<table><tr><th>Key</th><th>Summary</th><th>Stage</th><th>Days in stage</th><th>Assignee</th></tr>
{% for i in d.offenders %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td>
<td>{{ i.stage }}</td><td>{{ fmt(i.timeline.days_in_stage(i.stage)) }}</td><td>{{ i.assignee }}</td></tr>
{% endfor %}
</table>
"""


@bp.route("/reports/status-duration")
def status_duration():
    return page(SD, d=R.status_duration(dataset()))


# ---------------------------------------------------------------------------
# Report 7 — Release readiness
# ---------------------------------------------------------------------------

REL = """
<h1>Release Readiness</h1>
<div class="sub">Pick a fix version</div>
<div class="sectionbox">
{% for v in versions %}<a href="/reports/release?version={{ v|urlencode }}" class="pill" style="margin:3px">{{ v }}</a>{% endfor %}
{% if not versions %}<span class="muted">No fix versions found.</span>{% endif %}
</div>
{% if d %}
<h1>{{ d.version }}</h1>
<div class="cards">
 <div class="card"><div class="n">{{ d.completion_pct }}%</div><div class="l">Complete ({{ d.done }}/{{ d.total }})</div></div>
 <div class="card"><div class="n">{{ d.open_critical }}</div><div class="l">Open critical bugs</div></div>
 <div class="card"><div class="n">{{ d.open_high }}</div><div class="l">Open high bugs</div></div>
 <div class="card"><div class="n">{{ d.pending_qa }}</div><div class="l">Pending QA</div></div>
 <div class="card"><div class="n">{{ d.risk_score }}</div><div class="l">Release risk score</div></div>
</div>
<h2>Open bugs</h2>
<table><tr><th>Key</th><th>Summary</th><th>Priority</th><th>Stage</th></tr>
{% for i in d.open_bugs_list %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td>
<td>{{ i.priority }}</td><td>{{ i.stage }}</td></tr>
{% else %}<tr><td colspan="4" class="muted">No open bugs. 🎉</td></tr>{% endfor %}
</table>
{% endif %}
"""


@bp.route("/reports/release")
def release():
    versions = sorted({v.get("name") for v in jc.fetch_project_versions()
                       if not v.get("released")}, reverse=True)
    chosen = request.args.get("version")
    d = None
    if chosen:
        d = R.release_readiness(jc.fetch_issues_for_version(chosen), chosen)
    return page(REL, versions=versions, d=d)


# ---------------------------------------------------------------------------
# Report 2 — Sprint health
# ---------------------------------------------------------------------------

SPRINT = """
<h1>Sprint Health</h1>
{% if not configured %}
<div class="sectionbox"><b>Sprint reporting needs a board id.</b>
<p class="muted">Set <code>JIRA_BOARD_IDS</code> (comma-separated) and restart. Find a board id in
its URL: <code>.../jira/software/projects/LIFEDATAV2/boards/<b>123</b></code>. The team already
uses sprints, so once a board is configured this report populates automatically.</p></div>
{% else %}
{% for s in sprints %}
<h2>{{ s.name }}</h2>
<div class="cards">
 <div class="card"><div class="n">{{ s.completion_pct }}%</div><div class="l">Complete ({{ s.done }}/{{ s.total }})</div></div>
 <div class="card"><div class="n">{{ s.remaining }}</div><div class="l">Remaining</div></div>
 <div class="card"><div class="n">{{ s.spillover|length }}</div><div class="l">Spillover risk</div></div>
</div>
<table><tr><th>Stage</th><th>Tickets</th></tr>
{% for st,c in s.by_stage.items() %}<tr><td>{{ st }}</td><td>{{ c }}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">No active sprints found.</p>{% endfor %}
{% endif %}
"""


@bp.route("/reports/sprints")
def sprints():
    if not cfg.BOARD_IDS:
        return page(SPRINT, configured=False, sprints=[])
    return page(SPRINT, configured=True, sprints=R.sprint_health(jc.fetch_active_sprints()))


# ---------------------------------------------------------------------------
# Report 5 — Individual activity
# ---------------------------------------------------------------------------

INDIV = """
<h1>Individual Activity — {{ d.person }}</h1>
<div class="sub">Last {{ d.window_days }} days</div>
<div class="cards">
 <div class="card"><div class="n">{{ d.assigned }}</div><div class="l">Assigned</div></div>
 <div class="card"><div class="n">{{ d.completed }}</div><div class="l">Completed</div></div>
 <div class="card"><div class="n">{{ d.open }}</div><div class="l">Open</div></div>
 <div class="card"><div class="n">{{ d.active_days_total }}</div><div class="l">Active days (elapsed)</div></div>
</div>
<h2>Completed</h2>
<table><tr><th>Key</th><th>Summary</th><th>Cycle</th></tr>
{% for i in d.completed_list %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td><td>{{ fmt(i.timeline.cycle_days) }}</td></tr>
{% else %}<tr><td colspan="3" class="muted">None.</td></tr>{% endfor %}
</table>
<h2>Open</h2>
<table><tr><th>Key</th><th>Summary</th><th>Stage</th><th>Days in stage</th></tr>
{% for i in d.open_list %}
<tr><td><a href="{{ i.url }}" target="_blank">{{ i.key }}</a></td><td>{{ i.summary }}</td>
<td>{{ i.stage }}</td><td>{{ fmt(i.timeline.days_in_stage(i.stage)) }}</td></tr>
{% else %}<tr><td colspan="4" class="muted">None.</td></tr>{% endfor %}
</table>
"""


@bp.route("/reports/individual/<person>")
def individual(person):
    win = int(request.args.get("days", 30))
    return page(INDIV, d=R.individual_activity(dataset(), person, win))


# ---------------------------------------------------------------------------
# Time in Status — per-ticket time spent in each status, any timeframe
# ---------------------------------------------------------------------------

def _window_spec(args):
    """Resolve request args into the window bounds + fetch JQL + display params.

    Returns: (start_dt, end_dt, label, mode, params, fetch_jql)
      mode 'window'   -> count only time accrued inside [start,end); fetch every ticket
                         that OVERLAPPED the window (even if untouched).
      mode 'lifetime' -> lifetime totals; fetch tickets updated within the window.
    """
    now = A.now_utc()
    rng = args.get("range", "7d")
    mode = args.get("mode", "window")

    if rng == "24h":
        start, end, label = now - dt.timedelta(hours=24), now, "past 24 hours"
    elif rng == "30d":
        start, end, label = now - dt.timedelta(days=30), now, "past 30 days"
    elif rng == "custom" and args.get("from"):
        frm, to = args.get("from"), args.get("to") or now.strftime("%Y-%m-%d")
        start = dt.datetime.fromisoformat(frm).replace(tzinfo=dt.timezone.utc)
        end = dt.datetime.fromisoformat(to).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
        label = f"{frm} → {to}"
    else:
        rng = "7d"
        start, end, label = now - dt.timedelta(days=7), now, "past 7 days"

    s_str, e_str = start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
    if mode == "lifetime":
        fetch_jql = f'updated >= "{s_str}" AND updated <= "{e_str}"'
    else:
        # Any ticket that existed and was not-yet-resolved during part of the window.
        fetch_jql = (f'created <= "{e_str}" AND '
                     f'(resolutiondate >= "{s_str}" OR resolution is EMPTY)')

    params = {"range": rng, "mode": mode}
    if rng == "custom":
        params["from"] = args.get("from", "")
        params["to"] = args.get("to", "")
    return start, end, label, mode, params, fetch_jql


# small cache keyed by (fetch_jql, mode) so reload/CSV don't double-fetch
_TIS = {}


def _time_in_status_data(start, end, mode, fetch_jql):
    key = (fetch_jql, mode)
    now = time.time()
    hit = _TIS.get(key)
    if hit and now - hit[1] < _TTL:
        return hit[0]
    issues = R.load_issues(jc.fetch_issues_by_time(fetch_jql))
    window = (start, end) if mode == "window" else None
    # Only include tickets that actually changed status during the timeframe.
    data = R.time_in_status(issues, window=window, change_window=(start, end))
    _TIS[key] = (data, now)
    return data


TIS = """
<h1>Time in Status</h1>
<div class="sub">
  Only tickets that <b>changed status at least once during the {{ label }}</b>.
  {% if mode=='window' %}Each cell is the time accrued in that status inside the window.{% else %}Each cell is lifetime time in that status.{% endif %}
  {{ d.count }} tickets.
</div>
<div class="sectionbox">
  <a class="pill {{ 'ok' if params.range=='24h' else '' }}" href="?range=24h&mode={{ mode }}">Past 24h</a>
  <a class="pill {{ 'ok' if params.range=='7d' else '' }}" href="?range=7d&mode={{ mode }}">Past 7 days</a>
  <a class="pill {{ 'ok' if params.range=='30d' else '' }}" href="?range=30d&mode={{ mode }}">Past 30 days</a>
  <form method="get" style="display:inline-block;margin-left:14px">
    <input type="hidden" name="range" value="custom"><input type="hidden" name="mode" value="{{ mode }}">
    From <input type="date" name="from" value="{{ params.get('from','') }}">
    To <input type="date" name="to" value="{{ params.get('to','') }}">
    <button class="pill" type="submit">Apply range</button>
  </form>
  <span style="margin-left:14px">Mode:
    <a class="pill {{ 'ok' if mode=='window' else '' }}" href="?{{ query_for_mode('window') }}">In-window</a>
    <a class="pill {{ 'ok' if mode=='lifetime' else '' }}" href="?{{ query_for_mode('lifetime') }}">Lifetime</a>
  </span>
  <a class="pill" style="float:right" href="/reports/time-in-status.csv?{{ query }}">Download CSV</a>
</div>
<div style="overflow-x:auto">
<table>
 <tr><th>Key</th><th>Summary</th><th>Assignee</th><th>Current</th><th>Total</th>
 {% for s in d.statuses %}<th>{{ s }}</th>{% endfor %}</tr>
 {% for r in d.rows %}
 <tr>
   <td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td>
   <td>{{ r.issue.summary }}</td>
   <td>{{ r.issue.assignee }}</td>
   <td><span class="pill">{{ r.current }}</span></td>
   <td><b>{{ r.total_days }}d</b></td>
   {% for s in d.statuses %}<td>{{ (r.per_status[s]|string + 'd') if s in r.per_status else '·' }}</td>{% endfor %}
 </tr>
 {% else %}<tr><td colspan="6" class="muted">No tickets in this timeframe.</td></tr>{% endfor %}
</table>
</div>
<p class="muted">
  {% if mode=='window' %}Each cell = days that ticket sat in that status <b>within the selected window</b>. Totals can't exceed the window length.{% else %}Each cell = lifetime days in that status (capped at resolved/now).{% endif %}
  "·" = no time in that status.
</p>
"""


def _query(params):
    return "&".join(f"{k}={v}" for k, v in params.items() if v != "")


@bp.route("/reports/time-in-status")
def time_in_status_view():
    start, end, label, mode, params, fetch_jql = _window_spec(request.args)
    d = _time_in_status_data(start, end, mode, fetch_jql)

    def query_for_mode(m):
        p = dict(params); p["mode"] = m
        return _query(p)

    return page(TIS, d=d, label=label, mode=mode, params=params,
                query=_query(params), query_for_mode=query_for_mode)


@bp.route("/reports/time-in-status.csv")
def time_in_status_csv():
    start, end, label, mode, params, fetch_jql = _window_spec(request.args)
    d = _time_in_status_data(start, end, mode, fetch_jql)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Key", "Summary", "Assignee", "Current status", "Total days"] + d["statuses"])
    for r in d["rows"]:
        i = r["issue"]
        w.writerow([i.key, i.summary, i.assignee, r["current"], r["total_days"]]
                   + [r["per_status"].get(s, "") for s in d["statuses"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=time_in_status.csv"})


# ---------------------------------------------------------------------------
# JSON feed for all reports (automation)
# ---------------------------------------------------------------------------

@bp.route("/api/reports.json")
def reports_json():
    issues = dataset()
    ed = R.executive_dashboard(issues, 7)
    return jsonify({
        "executive": {k: ed[k] for k in ("delivery", "productivity", "quality", "risk")},
        "developers": R.developer_productivity(issues, jc.WINDOW_DAYS)["rows"],
        "qa": R.qa_productivity(issues, jc.WINDOW_DAYS)["rows"],
        "status_duration": R.status_duration(issues)["rows"],
    })
