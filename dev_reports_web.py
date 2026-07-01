"""
dev_reports_web.py
------------------
Web layer for the 18 developer-discipline reports (see dev_reports.py). One
catalog page plus a single generic table renderer + CSV export shared by every
report, with the spec's common filter bar: project, developer, start date, end
date (all optional; the Ticket Movement Timeline additionally needs an issue key).
"""

from __future__ import annotations

import csv
import datetime as dt
import io

from flask import Blueprint, Response, render_template_string, request

import config as cfg
import dev_reports as dr
import jira_client as jc
import reports_web  # shared chrome (nav + loading overlay)

devbp = Blueprint("devreports", __name__)

# slug -> (number, name, builder, cadence, purpose)
CATALOG = [
    ("daily-activity", 1, "Daily Developer Activity", dr.daily_activity, "Daily",
     "Complete daily activity trail: issue changes, comments, worklogs."),
    ("silent-tickets", 2, "Silent / No Daily Update Tickets", dr.silent_tickets, "Daily",
     "Active tickets that have not been updated recently."),
    ("multiple-active", 3, "Multiple Active Tickets Violation", dr.multiple_active, "Daily/Weekly",
     "Developers with more than one active ticket at the same time."),
    ("eod-discipline", 4, "End-of-Day Discipline", dr.eod_discipline, "Daily",
     "Do active tickets have a worklog, comment, or update on the selected day?"),
    ("rfqa-contribution", 5, "Ready for QA Contribution", dr.rfqa_contribution, "Daily/Weekly",
     "Tickets moved to Ready for QA, credited to the transition author."),
    ("returned-from-qa", 6, "Returned from QA / Reopened", dr.returned_from_qa, "Weekly",
     "Tickets that moved from QA statuses back to development."),
    ("cycle-time", 7, "Cycle Time by Developer", dr.cycle_time, "Weekly/Sprint close",
     "Development start → Ready for QA → Done, in hours."),
    ("stuck-aging", 8, "Stuck Ticket Aging", dr.stuck_aging, "Daily/Weekly",
     "Unfinished tickets ordered by time in their current status."),
    ("worklog-completeness", 9, "Worklog Completeness", dr.worklog_completeness, "Weekly",
     "Logged hours vs updated tickets for the period."),
    ("no-estimate", 10, "Tickets Without Estimate", dr.no_estimate, "Weekly",
     "Active tickets missing an estimate or story points."),
    ("overdue", 11, "Overdue Tickets", dr.overdue, "Weekly",
     "Unfinished tickets past their due date."),
    ("handoff-quality", 12, "Developer Handoff Quality", dr.handoff_quality, "Daily/Weekly",
     "Do Ready-for-QA tickets carry testing notes and PR/build references?"),
    ("status-no-comment", 13, "Status Change Without Comment", dr.status_no_comment, "Weekly",
     "Transitions without a nearby explanatory comment by the same user."),
    ("blocked", 14, "Blocked Tickets", dr.blocked, "Daily/Weekly",
     "Tickets blocked by status or labels."),
    ("sprint-commitment", 15, "Sprint Commitment vs Completion", dr.sprint_commitment, "Sprint close",
     "Committed vs completed work by sprint and developer."),
    ("timeline", 16, "Ticket Movement Timeline", dr.ticket_timeline, "As needed",
     "Complete lifecycle of one ticket: changelog, comments, worklogs."),
    ("focus", 17, "Developer Focus", dr.developer_focus, "Weekly",
     "Distinct tickets touched per developer per day (context switching)."),
    ("bug-quality", 18, "Bug Fix Quality", dr.bug_quality, "Weekly/Sprint close",
     "Bug volume, completions, QA returns, and resolution time per developer."),
]
BY_SLUG = {c[0]: c for c in CATALOG}


def _parse_date(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _filters():
    project = (request.args.get("project") or "").strip() or None
    developer = (request.args.get("developer") or "").strip() or None
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    if end:
        end += dt.timedelta(days=1)  # inclusive end date
    return project, developer, start, end


def _dataset(project):
    return dr.load_dev_issues(jc.fetch_dev_dataset(project), jc.detect_custom_fields())


def _run(slug):
    fn = BY_SLUG[slug][3]
    project, developer, start, end = _filters()
    issues = _dataset(project)
    kwargs = {"developer": developer, "start": start, "end": end}
    if slug == "timeline":
        kwargs["issue_key"] = request.args.get("issue_key", "")
    if slug == "stuck-aging":
        try:
            kwargs["threshold_days"] = max(int(request.args.get("threshold") or 0), 0)
        except ValueError:
            pass
    return fn(issues, **kwargs)


CATALOG_TMPL = """
<h1>Developer Reports</h1>
<div class="sub">18 Jira REST reports for daily discipline, QA handoff, delivery flow, and planning hygiene</div>
{% for cad, items in groups %}
<h2>{{ cad }}</h2>
<div class="cards" style="align-items:stretch">
  {% for slug, num, name, desc in items %}
  <a class="card" href="/dev-reports/{{ slug }}" style="min-width:230px;max-width:340px;text-decoration:none;color:inherit">
    <div style="font-weight:700;font-size:14px;color:#0052cc">{{ num }}. {{ name }}</div>
    <div class="muted" style="margin-top:4px">{{ desc }}</div>
  </a>
  {% endfor %}
</div>
{% endfor %}
"""

REPORT_TMPL = """
<h1>{{ num }}. {{ name }}</h1>
<div class="sub">{{ desc }} · recommended: {{ cadence }}</div>
<form method="get" class="sectionbox" style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
  {% if slug == 'timeline' %}
  <label style="font-size:12px;color:#5e6c84">Issue key<br>
    <input name="issue_key" value="{{ request.args.get('issue_key','') }}" placeholder="LIFEDATAV2-1234" required
           style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px"></label>
  {% endif %}
  <label style="font-size:12px;color:#5e6c84">Project<br>
    <input name="project" value="{{ request.args.get('project','') }}" placeholder="all synced"
           style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px;width:110px"></label>
  <label style="font-size:12px;color:#5e6c84">Developer<br>
    <input name="developer" value="{{ request.args.get('developer','') }}" placeholder="name or accountId"
           style="padding:7px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px;width:150px"></label>
  <label style="font-size:12px;color:#5e6c84">Start date<br>
    <input type="date" name="start" value="{{ request.args.get('start','') }}"
           style="padding:6px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px"></label>
  <label style="font-size:12px;color:#5e6c84">End date<br>
    <input type="date" name="end" value="{{ request.args.get('end','') }}"
           style="padding:6px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px"></label>
  {% if slug == 'stuck-aging' %}
  <label style="font-size:12px;color:#5e6c84">Min days in status<br>
    <input type="number" name="threshold" min="0" value="{{ request.args.get('threshold','') }}"
           style="padding:6px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px;width:70px"></label>
  {% endif %}
  <button class="pill ok" type="submit" style="border:none;cursor:pointer;padding:8px 16px">Run report</button>
  <a class="pill" href="/dev-reports/{{ slug }}">Clear</a>
  <a class="pill" style="margin-left:auto" href="/dev-reports/{{ slug }}.csv?{{ request.query_string.decode() }}" download>Download CSV</a>
  <a class="pill" href="/dev-reports">All reports</a>
</form>
<p class="muted">{{ d.note }} · {{ d.rows|length }} row(s).</p>
<div style="overflow-x:auto">
<table>
  <tr>{% for c in d.columns %}<th>{{ c }}</th>{% endfor %}</tr>
  {% for row in d.rows %}
  <tr>{% for cell in row %}
    <td>{% if cell is mapping %}<a href="{{ cell.url }}" target="_blank">{{ cell.text }}</a>{% else %}{{ cell if cell is not none else '—' }}{% endif %}</td>
  {% endfor %}</tr>
  {% else %}<tr><td colspan="{{ d.columns|length }}" class="muted">No rows for the selected filters.</td></tr>{% endfor %}
</table>
</div>
"""


@devbp.route("/dev-reports")
def catalog():
    order = ["Daily", "Daily/Weekly", "Weekly", "Weekly/Sprint close", "Sprint close", "As needed"]
    groups = []
    for cad in order:
        items = [(s, n, name, desc) for s, n, name, _fn, c, desc in CATALOG if c == cad]
        if items:
            groups.append((cad, items))
    return reports_web.page(CATALOG_TMPL, groups=groups)


@devbp.route("/dev-reports/<slug>")
def report(slug):
    if slug not in BY_SLUG:
        return reports_web.page("<h1>Unknown report</h1>"), 404
    s, num, name, _fn, cadence, desc = BY_SLUG[slug]
    if slug == "timeline" and not request.args.get("issue_key"):
        d = {"columns": [], "rows": [], "note": "Enter an issue key above to build the timeline."}
    else:
        d = _run(slug)
    return reports_web.page(REPORT_TMPL, slug=slug, num=num, name=name,
                            cadence=cadence, desc=desc, d=d)


@devbp.route("/dev-reports/<slug>.csv")
def report_csv(slug):
    if slug not in BY_SLUG:
        return Response("unknown report", status=404)
    d = _run(slug)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{BY_SLUG[slug][1]}. {BY_SLUG[slug][2]}"])
    w.writerow([d["note"]])
    w.writerow([])
    w.writerow(d["columns"])
    for row in d["rows"]:
        w.writerow([c["text"] if isinstance(c, dict) else c for c in row])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={slug}.csv"})
