"""
app.py
------
A small internal web app that reports on developer activity in Jira.

Run:
    pip install -r requirements.txt
    export JIRA_BASE_URL=https://lifedata.atlassian.net
    export JIRA_EMAIL=you@lifedatacorp.com
    export JIRA_API_TOKEN=*****
    python app.py
    # open http://localhost:5000

Pages:
    /                      team overview + per-developer summary table
    /developer/<name>      one developer's completed + in-progress detail
    /report.xlsx           download the whole thing as an Excel workbook
    /api/report.json       raw JSON (handy for a future dashboard/automation)

The numbers come from jira_client.build_report(), which reads the Jira changelog
to compute true In-Progress -> Done cycle time and time-in-current-status.
Results are cached for a few minutes so page loads are fast and gentle on the API.
"""

from __future__ import annotations

import io
import time

from flask import Flask, Response, abort, jsonify, render_template_string

import jira_client as jc
import reports_web

app = Flask(__name__)
# Eight executive reports (daily movement, sprint, dev/QA productivity, status
# duration, release readiness, executive dashboard, individual activity) live here.
app.register_blueprint(reports_web.bp)

# ---- tiny in-memory cache so we don't hammer the Jira API on every refresh ----
_CACHE: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # seconds


def get_reports(force: bool = False):
    if force or not _CACHE["data"] or (time.time() - _CACHE["ts"] > _CACHE_TTL):
        _CACHE["data"] = jc.build_report(fetch_changelogs=True)
        _CACHE["ts"] = time.time()
    return _CACHE["data"]


def fmt(v, suffix="d"):
    return f"{v}{suffix}" if v is not None else "—"


# ---------------------------------------------------------------------------
# Templates (kept inline to keep this a small, copy-pasteable project)
# ---------------------------------------------------------------------------

BASE_CSS = """
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; color: #172b4d; background: #f4f5f7; }
  header { background: #0052cc; color: #fff; padding: 18px 28px; }
  header h1 { margin: 0; font-size: 20px; }
  header .sub { opacity: .85; font-size: 13px; margin-top: 4px; }
  .wrap { max-width: 1100px; margin: 24px auto; padding: 0 20px; }
  .cards { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
  .card { background: #fff; border-radius: 8px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(9,30,66,.12); flex: 1; min-width: 160px; }
  .card .n { font-size: 28px; font-weight: 700; }
  .card .l { color: #6b778c; font-size: 13px; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(9,30,66,.12); }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #ebecf0; font-size: 14px; }
  th { background: #fafbfc; color: #6b778c; font-weight: 600; }
  tr:hover td { background: #f7f8fa; }
  a { color: #0052cc; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #dfe1e6; }
  .warn { background: #ffebe6; color: #bf2600; }
  .muted { color: #6b778c; font-size: 13px; }
  h2 { font-size: 16px; margin: 28px 0 10px; }
  .toolbar { margin-bottom: 16px; }
  .btn { background:#0052cc;color:#fff;padding:8px 14px;border-radius:6px;font-size:13px; }
</style>
""" + reports_web.LOADING_OVERLAY

OVERVIEW_TMPL = BASE_CSS + """
<header>
  <h1>Developer Activity Report</h1>
  <div class="sub">{{ projects }} &middot; completed in last {{ window }} days &middot; generated {{ generated }}</div>
</header>
<div class="wrap">
  <div class="cards">
    <div class="card"><div class="n">{{ total_done }}</div><div class="l">Tickets completed</div></div>
    <div class="card"><div class="n">{{ total_wip }}</div><div class="l">In progress now</div></div>
    <div class="card"><div class="n">{{ team_cycle }}</div><div class="l">Median cycle time</div></div>
    <div class="card"><div class="n">{{ team_oldest }}</div><div class="l">Oldest WIP (in status)</div></div>
  </div>

  <div class="toolbar">
    <a class="btn" href="/exec">Executive dashboard &amp; reports →</a>
    <a class="btn" href="/report.xlsx" download>Download Excel</a>
  </div>

  <table>
    <tr>
      <th>Developer</th><th>Open assigned</th><th>Completed</th><th>Avg cycle</th><th>Median cycle</th>
      <th>In progress</th><th>Oldest WIP</th>
    </tr>
    {% for d in devs %}
    <tr>
      <td><a href="/developer/{{ d.name|urlencode }}">{{ d.name }}</a></td>
      <td>{{ d.open_count }}</td>
      <td>{{ d.throughput }}</td>
      <td>{{ fmt(d.avg_cycle) }}</td>
      <td>{{ fmt(d.median_cycle) }}</td>
      <td>{{ d.in_progress|length }}</td>
      <td>{{ fmt(d.oldest_in_progress) }}</td>
    </tr>
    {% endfor %}
  </table>
  <p class="muted">Cycle time = first entry into an In&nbsp;Progress status &rarr; Done, from the issue changelog.
  Oldest WIP = days the ticket has sat in its current status. Grouped by current assignee.</p>
</div>
"""

DEV_TMPL = BASE_CSS + """
<header>
  <h1>{{ d.name }}</h1>
  <div class="sub"><a href="/" style="color:#cfe0ff">&larr; All developers</a></div>
</header>
<div class="wrap">
  <div class="cards">
    <div class="card"><div class="n">{{ d.open_count }}</div><div class="l">Open assigned</div></div>
    <div class="card"><div class="n">{{ d.throughput }}</div><div class="l">Completed ({{ window }}d)</div></div>
    <div class="card"><div class="n">{{ fmt(d.median_cycle) }}</div><div class="l">Median cycle time</div></div>
    <div class="card"><div class="n">{{ d.in_progress|length }}</div><div class="l">In progress</div></div>
    <div class="card"><div class="n">{{ fmt(d.oldest_in_progress) }}</div><div class="l">Oldest WIP</div></div>
  </div>

  <h2>In progress</h2>
  <table>
    <tr><th>Key</th><th>Summary</th><th>Current status</th><th>Days in status</th></tr>
    {% for t in d.in_progress %}
    <tr>
      <td><a href="{{ t.url }}" target="_blank">{{ t.key }}</a></td>
      <td>{{ t.summary }}</td>
      <td>{{ t.status }}</td>
      <td>{% if t.days_in_status and t.days_in_status > 14 %}<span class="pill warn">{{ fmt(t.days_in_status) }}</span>{% else %}{{ fmt(t.days_in_status) }}{% endif %}</td>
    </tr>
    {% else %}<tr><td colspan="4" class="muted">Nothing in progress.</td></tr>{% endfor %}
  </table>

  <h2>Completed</h2>
  <table>
    <tr><th>Key</th><th>Summary</th><th>Type</th><th>Lead</th><th>Cycle</th></tr>
    {% for t in d.completed %}
    <tr>
      <td><a href="{{ t.url }}" target="_blank">{{ t.key }}</a></td>
      <td>{{ t.summary }}</td>
      <td><span class="pill">{{ t.issue_type }}</span></td>
      <td>{{ fmt(t.lead_days) }}</td>
      <td>{{ fmt(t.cycle_days) }}</td>
    </tr>
    {% else %}<tr><td colspan="5" class="muted">None in window.</td></tr>{% endfor %}
  </table>

  <h2>Currently assigned <span class="muted">(all open tickets)</span></h2>
  <table>
    <tr><th>Key</th><th>Summary</th><th>Type</th><th>Status</th><th>Open age</th></tr>
    {% for t in d.assigned %}
    <tr>
      <td><a href="{{ t.url }}" target="_blank">{{ t.key }}</a></td>
      <td>{{ t.summary }}</td>
      <td><span class="pill">{{ t.issue_type }}</span></td>
      <td>{{ t.status }}</td>
      <td>{{ fmt(t.age_days) }}</td>
    </tr>
    {% else %}<tr><td colspan="5" class="muted">No open tickets assigned.</td></tr>{% endfor %}
  </table>
</div>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def overview():
    reports = get_reports()
    devs = sorted(reports.values(), key=lambda d: (-d.throughput, -len(d.in_progress)))
    total_done = sum(d.throughput for d in devs)
    total_wip = sum(len(d.in_progress) for d in devs)
    all_cycle = [t.cycle_days for d in devs for t in d.completed if t.cycle_days is not None]
    all_oldest = [d.oldest_in_progress for d in devs if d.oldest_in_progress is not None]
    from statistics import median
    return render_template_string(
        OVERVIEW_TMPL, devs=devs, fmt=fmt,
        projects=", ".join(jc.PROJECT_KEYS), window=jc.WINDOW_DAYS,
        generated=time.strftime("%Y-%m-%d %H:%M"),
        total_done=total_done, total_wip=total_wip,
        team_cycle=fmt(round(median(all_cycle), 1) if all_cycle else None),
        team_oldest=fmt(max(all_oldest) if all_oldest else None),
    )


@app.route("/developer/<name>")
def developer(name):
    reports = get_reports()
    d = reports.get(name)
    if not d:
        abort(404)
    return render_template_string(DEV_TMPL, d=d, fmt=fmt, window=jc.WINDOW_DAYS)


@app.route("/api/report.json")
def report_json():
    reports = get_reports()
    out = {}
    for name, d in reports.items():
        out[name] = {
            "throughput": d.throughput,
            "open_assigned": d.open_count,
            "avg_cycle_days": d.avg_cycle,
            "median_cycle_days": d.median_cycle,
            "in_progress": len(d.in_progress),
            "oldest_wip_days": d.oldest_in_progress,
            "completed": [{"key": t.key, "summary": t.summary, "lead_days": t.lead_days,
                           "cycle_days": t.cycle_days} for t in d.completed],
            "wip": [{"key": t.key, "summary": t.summary, "status": t.status,
                     "days_in_status": t.days_in_status} for t in d.in_progress],
            "assigned": [{"key": t.key, "summary": t.summary, "type": t.issue_type,
                          "status": t.status, "age_days": t.age_days} for t in d.assigned],
        }
    return jsonify(out)


@app.route("/report.xlsx")
def report_xlsx():
    import openpyxl
    from openpyxl.styles import Font

    reports = get_reports()
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Summary"
    ws.append(["Developer", "Open assigned", "Completed", "Avg cycle (d)", "Median cycle (d)",
               "In progress", "Oldest WIP (d)"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for d in sorted(reports.values(), key=lambda d: -d.throughput):
        ws.append([d.name, d.open_count, d.throughput, d.avg_cycle, d.median_cycle,
                   len(d.in_progress), d.oldest_in_progress])

    wc = wb.create_sheet("Completed")
    wc.append(["Developer", "Key", "Summary", "Type", "Lead (d)", "Cycle (d)"])
    for c in wc[1]:
        c.font = Font(bold=True)
    for d in reports.values():
        for t in d.completed:
            wc.append([d.name, t.key, t.summary, t.issue_type, t.lead_days, t.cycle_days])

    wp = wb.create_sheet("In Progress")
    wp.append(["Developer", "Key", "Summary", "Current status", "Days in status"])
    for c in wp[1]:
        c.font = Font(bold=True)
    for d in reports.values():
        for t in d.in_progress:
            wp.append([d.name, t.key, t.summary, t.status, t.days_in_status])

    wa = wb.create_sheet("Assigned (open)")
    wa.append(["Developer", "Key", "Summary", "Type", "Status", "Open age (d)"])
    for c in wa[1]:
        c.font = Font(bold=True)
    for d in reports.values():
        for t in d.assigned:
            wa.append([d.name, t.key, t.summary, t.issue_type, t.status, t.age_days])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=developer_report.xlsx"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
