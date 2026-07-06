"""
screens_web.py
--------------
The v3 screen layer (PRD v3 §4): Settings, My Day, Attention Board, QA Handoff,
Flow Analytics, Quality, Sprint & Planning, Ticket Investigator, and the shared
purpose-grouped navigation + global filter bar. Engines live in checklist.py,
attention.py, activity.py, dev_reports.py, and reports.py — this module is
presentation and wiring only.
"""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, Response, jsonify, redirect, render_template_string, request

import activity
import analytics as A
import attention
import checklist
import config as legacy
import dev_reports as dr
import jira_client as jc
import settings as st

v3 = Blueprint("v3", __name__)


def _issues(project=None):
    return dr.load_dev_issues(jc.fetch_dev_dataset(project), jc.detect_custom_fields())


# ---------------------------------------------------------------------------
# Shared chrome — purpose-grouped nav (FR-U2) + global filter bar (FR-U1)
# ---------------------------------------------------------------------------

NAV = [
    ("/my-day", "My Day"), ("/attention", "Attention"), ("/qa", "QA"),
    ("/flow", "Flow"), ("/quality", "Quality"), ("/planning", "Planning"),
    ("/investigate", "Investigate"), ("/exec", "Trends"), ("/settings", "Settings"),
]

CHROME_TOP = """
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
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:8px}
 th,td{text-align:left;padding:9px 13px;border-bottom:1px solid #ebecf0;font-size:13px}
 th{background:#fafbfc;color:#6b778c;position:sticky;top:0}
 tr:hover td{background:#f7f8fa}
 a{color:#0052cc;text-decoration:none}a:hover{text-decoration:underline}
 .pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#dfe1e6}
 .warn{background:#fff7e6;color:#974f00}.bad{background:#ffebe6;color:#bf2600}.ok{background:#e3fcef;color:#006644}
 .muted{color:#6b778c;font-size:12px}
 .sectionbox{background:#fff;border-radius:8px;padding:16px 18px;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:16px}
 .banner{background:#fff7e6;border:1px solid #ffe2b3;color:#974f00;border-radius:8px;padding:10px 16px;margin-bottom:16px;font-size:13px}
 .fresh{color:#6b778c;font-size:11px;text-align:right;margin:4px 0}
 .filterbar{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;background:#fff;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:16px}
 .filterbar label{font-size:11px;color:#5e6c84}
 .filterbar input,.filterbar select{display:block;padding:6px 9px;border:1px solid #dfe1e6;border-radius:6px;font-size:13px;margin-top:2px}
 .btn{background:#0052cc;color:#fff;padding:7px 14px;border-radius:6px;font-size:13px;border:none;cursor:pointer;display:inline-block;text-decoration:none}
 .btn:hover{background:#0747a6;text-decoration:none;color:#fff}
 .btn-ghost{background:#fff;color:#42526e;border:1px solid #dfe1e6;padding:6px 13px;border-radius:6px;font-size:13px;cursor:pointer;text-decoration:none;display:inline-block}
 .chip{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:11px;font-size:11px;font-weight:600;margin:1px 3px 1px 0}
 .checkrow{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:10px;font-size:12px;margin:1px 4px 1px 0}
 .c-pass{background:#e3fcef;color:#006644}.c-fail{background:#ffebe6;color:#bf2600}.c-na{background:#f4f5f7;color:#8993a4}
 .glossary{border-bottom:1px dotted #b3bac5;cursor:help}
</style>
<nav>
 <span class="brand">LifeData Eng Reports</span>
 {NAVLINKS}
</nav>
"""

# The loading overlay from the legacy chrome is reused for consistency.
def _overlay():
    import reports_web
    return reports_web.LOADING_OVERLAY


def unmapped_banner():
    """Warning banner listing statuses seen in data but not classified (FR-C1)."""
    try:
        seen = {(raw.get("fields", {}).get("status") or {}).get("name", "")
                for raw in jc.fetch_dev_dataset(None)}
    except Exception:
        return ""
    missing = st.unmapped_statuses({s for s in seen if s})
    if not missing:
        return ""
    return (f'<div class="banner"><b>{len(missing)} status(es) need classification</b> — '
            f'metrics exclude them: {", ".join(missing)}. '
            f'<a href="/settings">Classify in Settings →</a></div>')


def page(body, active="", show_banner=True, **ctx):
    navlinks = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in NAV)
    chrome = CHROME_TOP.replace("{NAVLINKS}", navlinks) + _overlay()
    banner = unmapped_banner() if show_banner else ""
    fresh = dt.datetime.now().strftime("%H:%M")
    shell = (chrome + '<div class="wrap">'
             + f'<div class="fresh">data as of {fresh} · cached ~5 min</div>'
             + banner + body + "</div>")
    return render_template_string(shell, request=request, st=st, **ctx)


FILTER_BAR = """
<form method="get" class="filterbar" id="globalFilters">
  <label>Project<input name="project" value="{{ request.args.get('project','') }}" placeholder="all" style="width:100px"></label>
  <label>Developer<input name="developer" value="{{ request.args.get('developer','') }}" placeholder="name or accountId" style="width:150px"></label>
  <label>Start<input type="date" name="start" value="{{ request.args.get('start','') }}"></label>
  <label>End<input type="date" name="end" value="{{ request.args.get('end','') }}"></label>
  {{ extra_filters|default('')|safe }}
  <button class="btn" type="submit">Apply</button>
  <a class="btn-ghost" href="{{ request.path }}">Clear</a>
</form>
<script>
(function(){
  var f=document.getElementById('globalFilters'); if(!f)return;
  var KEY='jira_reports_filters';
  var qs=new URLSearchParams(location.search);
  if(![...qs.keys()].length){
    try{var saved=JSON.parse(localStorage.getItem(KEY)||'{}');
      ['project','developer','start','end'].forEach(function(k){
        if(saved[k]){var el=f.querySelector('[name='+k+']'); if(el&&!el.value)el.value=saved[k];}
      });}catch(e){}
  }
  f.addEventListener('submit',function(){
    var data={}; ['project','developer','start','end'].forEach(function(k){
      var el=f.querySelector('[name='+k+']'); if(el&&el.value)data[k]=el.value;});
    try{localStorage.setItem(KEY,JSON.stringify(data));}catch(e){}
  });
})();
</script>
"""


def parse_filters():
    project = (request.args.get("project") or "").strip() or None
    developer = (request.args.get("developer") or "").strip() or None
    def d(v):
        try:
            return dt.datetime.fromisoformat(v).replace(tzinfo=dt.timezone.utc) if v else None
        except ValueError:
            return None
    start = d(request.args.get("start"))
    end = d(request.args.get("end"))
    if end:
        end += dt.timedelta(days=1)
    return project, developer, start, end


def csv_response(columns, rows, filename):
    import csv as _csv
    import io
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([c["text"] if isinstance(c, dict) else c for c in r])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---------------------------------------------------------------------------
# Screen 0 — Settings (FR-C1..C6)
# ---------------------------------------------------------------------------

SETTINGS_TMPL = """
<h1>Settings</h1>
<div class="sub">Status classification, thresholds, and feature gates — changes apply immediately, no deploy needed</div>
{% if saved %}<div class="banner" style="background:#e3fcef;border-color:#abf5d1;color:#006644">Settings saved.</div>{% endif %}
<form method="post">
<div class="sectionbox">
  <h2 style="margin-top:0">Status classification <span class="muted">(every status must be assigned to exactly one bucket)</span></h2>
  <table style="box-shadow:none">
    <tr><th>Status</th><th>Bucket</th><th>Aging threshold (days)</th><th></th></tr>
    {% for status in statuses %}
    <tr>
      <td>{{ status }}</td>
      <td><select name="bucket__{{ status }}">
        <option value="">— unmapped —</option>
        {% for b in buckets %}<option value="{{ b }}" {% if mapping.get(status)==b %}selected{% endif %}>{{ bucket_labels[b] }}</option>{% endfor %}
      </select></td>
      <td><input type="number" step="0.5" min="0" name="threshold__{{ status }}"
                 value="{{ thresholds.get(status, '') }}" placeholder="{{ bucket_default(status) }}" style="width:80px"></td>
      <td>{% if status in unmapped %}<span class="pill bad">needs classification</span>{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  <p class="muted">Blank threshold = the bucket default below. Unmapped statuses are excluded from metrics and flagged on every screen.</p>
</div>
<div class="sectionbox">
  <h2 style="margin-top:0">Bucket threshold defaults</h2>
  {% for b in buckets %}
  <label style="margin-right:16px;font-size:13px">{{ bucket_labels[b] }}
    <input type="number" step="0.5" min="0" name="bucket_threshold__{{ b }}"
           value="{{ s.bucket_thresholds.get(b) if s.bucket_thresholds.get(b) is not none else '' }}" style="width:70px"></label>
  {% endfor %}
</div>
<div class="sectionbox">
  <h2 style="margin-top:0">Feature gates <span class="muted">(off = feature shows a teaching empty state)</span></h2>
  {% for g, label in gate_labels %}
  <label style="display:block;font-size:13px;margin:6px 0">
    <input type="checkbox" name="gate__{{ g }}" {% if s.gates.get(g) %}checked{% endif %}> {{ label }}</label>
  {% endfor %}
</div>
<div class="sectionbox">
  <h2 style="margin-top:0">My Day checklist items</h2>
  {% for c, label in check_labels %}
  <label style="display:inline-block;font-size:13px;margin:4px 14px 4px 0">
    <input type="checkbox" name="check__{{ c }}" {% if s.checklist_items.get(c) %}checked{% endif %}> {{ label }}</label>
  {% endfor %}
</div>
<div class="sectionbox">
  <h2 style="margin-top:0">Rules &amp; integrations</h2>
  <label style="font-size:13px">Handoff comment window (hours before transition)
    <input type="number" min="0" name="handoff_window_hours" value="{{ s.handoff_window_hours }}" style="width:70px"></label><br><br>
  <label style="font-size:13px">Silent after (days without activity)
    <input type="number" min="1" name="silent_days" value="{{ s.silent_days }}" style="width:70px"></label><br><br>
  <label style="font-size:13px">Investigator gap spacer (days)
    <input type="number" min="1" name="gap_days" value="{{ s.gap_days }}" style="width:70px"></label><br><br>
  <label style="font-size:13px">PR/build keywords (comma-separated)<br>
    <input name="pr_keywords" value="{{ s.pr_keywords|join(', ') }}" style="width:96%"></label><br><br>
  <label style="font-size:13px">Blocked labels (comma-separated)<br>
    <input name="blocked_labels" value="{{ s.blocked_labels|join(', ') }}" style="width:96%"></label><br><br>
  <label style="font-size:13px">Sprint board IDs (comma-separated)<br>
    <input name="board_ids" value="{{ s.board_ids|join(', ') }}" style="width:96%"></label><br><br>
  <label style="font-size:13px">Start-date field id <span class="muted">(auto-detected; override here)</span><br>
    <input name="start_date_field" value="{{ s.start_date_field or '' }}" style="width:96%"></label><br><br>
  <label style="font-size:13px">Teams webhook URL (morning digest)<br>
    <input name="teams_webhook_url" value="{{ s.teams_webhook_url }}" style="width:96%"></label><br><br>
  <label style="font-size:13px">Default landing role
    <select name="default_role">
      {% for r in ['developer','lead','exec'] %}<option value="{{ r }}" {% if s.default_role==r %}selected{% endif %}>{{ r }}</option>{% endfor %}
    </select></label>
</div>
<button class="btn" type="submit">Save settings</button>
</form>
"""

GATE_LABELS = [
    ("worklogs_required", "Worklogs required (enables worklog checks + completeness views)"),
    ("estimates_used", "Estimates/story points used (enables estimate checks + point metrics)"),
    ("due_dates_required", "Due dates required (enables Overdue + slip metrics)"),
    ("start_dates_required", "Start dates required (enables start-date rules + reschedule count)"),
    ("sprints_enabled", "Sprints enabled (enables Sprint Health; needs board IDs)"),
]
CHECK_LABELS = [
    ("status_mapped", "Status classified"), ("comment_today", "Comment today"),
    ("worklog_today", "Worklog today (gated)"), ("start_date", "Start date present (gated)"),
    ("due_date", "Due date present (gated)"), ("not_over_threshold", "Not over aging threshold"),
    ("handoff_comment", "Handoff comment when moved to QA"), ("blocked_reason", "Blocked reason comment"),
]


def _statuses_seen():
    seen = set(st.load()["status_buckets"])
    try:
        for raw in jc.fetch_dev_dataset(None):
            name = (raw.get("fields", {}).get("status") or {}).get("name", "")
            if name:
                seen.add(name)
    except Exception:
        pass
    return sorted(seen)


@v3.route("/settings", methods=["GET", "POST"])
def settings_screen():
    s = st.load()
    saved = False
    if request.method == "POST":
        form = request.form
        s["status_buckets"] = {}
        s["status_thresholds"] = {}
        for k, v in form.items():
            if k.startswith("bucket__") and v:
                s["status_buckets"][k[len("bucket__"):]] = v
            elif k.startswith("threshold__") and v.strip():
                try:
                    s["status_thresholds"][k[len("threshold__"):]] = float(v)
                except ValueError:
                    pass
            elif k.startswith("bucket_threshold__"):
                b = k[len("bucket_threshold__"):]
                try:
                    s["bucket_thresholds"][b] = float(v) if v.strip() else None
                except ValueError:
                    pass
        for g, _ in GATE_LABELS:
            s["gates"][g] = f"gate__{g}" in form
        for c, _ in CHECK_LABELS:
            s["checklist_items"][c] = f"check__{c}" in form
        for num_key in ("handoff_window_hours", "silent_days", "gap_days"):
            try:
                s[num_key] = max(int(form.get(num_key) or s[num_key]), 0)
            except ValueError:
                pass
        for list_key in ("pr_keywords", "blocked_labels", "board_ids"):
            s[list_key] = [x.strip() for x in (form.get(list_key) or "").split(",") if x.strip()]
        s["start_date_field"] = (form.get("start_date_field") or "").strip() or None
        s["teams_webhook_url"] = (form.get("teams_webhook_url") or "").strip()
        if form.get("default_role") in ("developer", "lead", "exec"):
            s["default_role"] = form["default_role"]
        st.save(s)
        saved = True
    statuses = _statuses_seen()
    unmapped = set(st.unmapped_statuses(set(statuses)))

    def bucket_default(status):
        b = s["status_buckets"].get(status)
        v = s["bucket_thresholds"].get(b) if b else None
        return v if v is not None else "—"

    return page(SETTINGS_TMPL, active="/settings", show_banner=False, s=s, saved=saved,
                statuses=statuses, unmapped=unmapped, mapping=s["status_buckets"],
                thresholds=s["status_thresholds"], buckets=st.BUCKETS,
                bucket_labels=st.BUCKET_LABELS, bucket_default=bucket_default,
                gate_labels=GATE_LABELS, check_labels=CHECK_LABELS)


# ---------------------------------------------------------------------------
# Screen 1 — My Day (FR-M1/M2/M4/M5)
# ---------------------------------------------------------------------------

MYDAY_TMPL = """
<h1>My Day</h1>
<div class="sub">Per-ticket end-of-day checklist — fix the red items before ending the day · <a href="/my-day/rollup?{{ request.query_string.decode() }}">admin roll-up</a> · <a href="/my-day/feed?{{ request.query_string.decode() }}">activity feed</a></div>
""" + FILTER_BAR.replace("{{ extra_filters|default('')|safe }}",
  """<label>Day<input type="date" name="day" value="{{ request.args.get('day','') }}"></label>""") + """
{% if not request.args.get('developer') %}
<div class="sectionbox"><b>Pick your name</b> to see your checklist:
  {% for dev in developers %}<a class="pill" style="margin:3px" href="?developer={{ dev|urlencode }}{{ day_qs }}">{{ dev }}</a>{% endfor %}
  {% if not developers %}<span class="muted">No active tickets found.</span>{% endif %}
</div>
{% endif %}
{% if d %}
<div class="cards">
  <div class="card"><div class="n">{{ d.rows|length }}</div><div class="l">Tickets to review</div></div>
  <div class="card"><div class="n" style="color:{{ '#bf2600' if d.total_fails else '#006644' }}">{{ d.total_fails }}</div><div class="l">Open items</div></div>
</div>
{% for r in d.rows %}
<div class="sectionbox" style="padding:12px 16px">
  <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div><a href="{{ r.issue.url }}" target="_blank"><b>{{ r.issue.key }}</b></a> {{ r.issue.summary }}</div>
    <div><span class="pill">{{ r.issue.type }}</span> <span class="pill">{{ r.issue.status }}</span></div>
  </div>
  <div style="margin-top:8px">
  {% for cid, label, state, why in r.checks %}
    <span class="checkrow c-{{ state }}" title="{{ why }}">{{ '✓' if state=='pass' else ('✗' if state=='fail' else '—') }} {{ label }}</span>
  {% endfor %}
  </div>
</div>
{% else %}<p class="muted">Nothing on the checklist — no active tickets for this developer.</p>{% endfor %}
{% endif %}
"""


@v3.route("/my-day")
def my_day_screen():
    project, developer, _s, _e = parse_filters()
    day = _day_arg()
    issues = _issues(project)
    devs = sorted({i.assignee for i in issues
                   if st.bucket_of(i.status, i.category) in ("active_dev", "rework")
                   and i.assignee != "Unassigned"})
    d = checklist.my_day(issues, developer, day, dr._dev_match) if developer else None
    day_qs = f"&day={request.args.get('day')}" if request.args.get("day") else ""
    return page(MYDAY_TMPL, active="/my-day", d=d, developers=devs, day_qs=day_qs)


def _day_arg():
    try:
        return dt.date.fromisoformat(request.args.get("day", ""))
    except ValueError:
        return dt.datetime.now(dt.timezone.utc).date()


ROLLUP_TMPL = """
<h1>End-of-day roll-up</h1>
<div class="sub">% of active tickets with an EOD signal (comment, worklog, or any update) on {{ d.day }} · <a href="/my-day">back to My Day</a></div>
""" + FILTER_BAR.replace("{{ extra_filters|default('')|safe }}",
  """<label>Day<input type="date" name="day" value="{{ request.args.get('day','') }}"></label>""") + """
<div class="cards">
  <div class="card"><div class="n">{{ d.pct }}%</div><div class="l">Tickets with EOD signal ({{ d.signaled }}/{{ d.total }})</div></div>
</div>
<table>
<tr><th>Developer</th><th>Active tickets</th><th>With EOD signal</th><th>%</th></tr>
{% for r in d.rows %}
<tr><td>{{ r.developer }}</td><td>{{ r.tickets }}</td><td>{{ r.signaled }}</td>
<td><span class="pill {{ 'ok' if r.pct >= 80 else ('warn' if r.pct >= 50 else 'bad') }}">{{ r.pct }}%</span></td></tr>
{% else %}<tr><td colspan="4" class="muted">No active tickets.</td></tr>{% endfor %}
</table>
"""


@v3.route("/my-day/rollup")
def my_day_rollup():
    project, _dev, _s, _e = parse_filters()
    day = _day_arg()
    d = checklist.rollup(_issues(project), day)
    return page(ROLLUP_TMPL, active="/my-day", d=d)


FEED_TMPL = """
<h1>Activity feed</h1>
<div class="sub">Unified event stream: transitions, comments, worklogs, field changes (FR-M5) · <a href="/my-day">back to My Day</a></div>
""" + FILTER_BAR + """
<p class="muted">{{ feed|length }} event(s). <a href="/api/v2/feed.csv?{{ request.query_string.decode() }}" download>Download CSV</a></p>
<table>
<tr><th>When</th><th>Type</th><th>Actor</th><th>Issue</th><th>Summary</th><th>Detail</th></tr>
{% for e in feed[:500] %}
<tr><td>{{ e.ts.strftime('%Y-%m-%d %H:%M') }}</td><td><span class="pill">{{ e.kind }}</span></td>
<td>{{ e.actor }}</td><td><a href="{{ e.issue.url }}" target="_blank">{{ e.issue.key }}</a></td>
<td>{{ e.issue.summary }}</td>
<td>{% if e.kind=='comment' %}{{ e.detail[:120] }}{% elif e.kind=='worklog' %}{{ (e.seconds/3600)|round(1) }}h {{ e.detail[:80] }}{% else %}{{ e.frm }} → {{ e.to }}{% endif %}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No events for the selected filters.</td></tr>{% endfor %}
</table>
"""


def _feed_rows():
    project, developer, start, end = parse_filters()
    if not start and not end:
        start = A.now_utc() - dt.timedelta(days=7)  # default window keeps it fast
    return activity.build_feed(_issues(project), developer, start, end, dr._dev_match)


@v3.route("/my-day/feed")
def my_day_feed():
    return page(FEED_TMPL, active="/my-day", feed=_feed_rows())


@v3.route("/api/v2/feed.csv")
def feed_csv():
    rows = [[e.ts.strftime("%Y-%m-%d %H:%M"), e.kind, e.actor, e.issue.key,
             e.issue.summary, e.detail or f"{e.frm} → {e.to}"] for e in _feed_rows()]
    return csv_response(["When", "Type", "Actor", "Issue", "Summary", "Detail"], rows, "activity_feed.csv")


@v3.route("/api/v2/myday.json")
def myday_json():
    project, developer, _s, _e = parse_filters()
    d = checklist.my_day(_issues(project), developer, _day_arg(), dr._dev_match)
    return jsonify({"day": d["day"].isoformat(), "total_fails": d["total_fails"],
                    "rows": [{"key": r["issue"].key, "fails": r["fails"],
                              "checks": [{"id": c, "label": l, "state": s, "why": w}
                                         for c, l, s, w in r["checks"]]} for r in d["rows"]]})


# ---------------------------------------------------------------------------
# Screen 2 — Attention Board (FR-A1/A2)
# ---------------------------------------------------------------------------

ATTN_TMPL = """
<h1>Attention Board</h1>
<div class="sub">Every ticket needing intervention, worst first — reason chips stack per ticket</div>
""" + FILTER_BAR.replace("{{ extra_filters|default('')|safe }}",
  """<label>Reason<select name="reason"><option value="">all</option>
  {% for k in d.kinds %}<option value="{{ k }}" {% if request.args.get('reason')==k %}selected{% endif %}>{{ k }}</option>{% endfor %}
  </select></label>""") + """
<p class="muted">{{ d.rows|length }} ticket(s) need attention. <a href="/api/v2/attention.csv?{{ request.query_string.decode() }}" download>Download CSV</a></p>
<table>
<tr><th>Issue</th><th>Summary</th><th>Developer</th><th>Status</th><th>Reasons</th></tr>
{% for r in d.rows %}
<tr>
 <td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td>
 <td>{{ r.issue.summary }}</td><td>{{ r.issue.assignee }}</td><td>{{ r.issue.status }}</td>
 <td>{% for reason in r.reasons %}<span class="chip {{ 'bad' if reason.kind in ('silent','aging','overdue','disposition') else 'warn' }}">⚠ {{ reason.tag }}</span>{% endfor %}</td>
</tr>
{% else %}<tr><td colspan="5" class="muted">Nothing needs attention. 🎉</td></tr>{% endfor %}
</table>
"""


def _attention_board():
    project, developer, _s, _e = parse_filters()
    reason = (request.args.get("reason") or "").strip() or None
    return attention.board(_issues(project), developer, reason, dr._dev_match)


@v3.route("/attention")
def attention_screen():
    return page(ATTN_TMPL, active="/attention", d=_attention_board())


@v3.route("/api/v2/attention.csv")
def attention_csv():
    d = _attention_board()
    rows = [[r["issue"].key, r["issue"].summary, r["issue"].assignee, r["issue"].status,
             "; ".join(x["tag"] for x in r["reasons"]), round(r["severity"], 1)]
            for r in d["rows"]]
    return csv_response(["Issue", "Summary", "Developer", "Status", "Reasons", "Severity"],
                        rows, "attention_board.csv")


@v3.route("/api/v2/attention.json")
def attention_json():
    d = _attention_board()
    return jsonify([{"key": r["issue"].key, "summary": r["issue"].summary,
                     "developer": r["issue"].assignee, "status": r["issue"].status,
                     "reasons": [x["tag"] for x in r["reasons"]],
                     "severity": round(r["severity"], 2)} for r in d["rows"]])


# ---------------------------------------------------------------------------
# Placeholder shells (filled in Phases 2–4) — teaching empty states, not 404s
# ---------------------------------------------------------------------------

SHELL_TMPL = """
<h1>{{ title }}</h1>
<div class="sub">{{ sub }}</div>
<div class="sectionbox"><p class="muted">{{ teach }}</p>{{ links|safe }}</div>
"""


@v3.route("/qa")
def qa_shell():
    return page(SHELL_TMPL, active="/qa", title="QA Handoff",
                sub="Handoff feed, handoff checks, returned-from-QA",
                teach="This screen arrives in Phase 2 of the v3 rollout.",
                links="")


@v3.route("/flow")
def flow_shell():
    return page(SHELL_TMPL, active="/flow", title="Flow Analytics",
                sub="Cycle time, stage breakdown, bottlenecks, focus",
                teach="Full Flow Analytics arrives in Phase 3. Time in Status is available now:",
                links='<a class="btn" href="/reports/time-in-status">Time in Status →</a>')


@v3.route("/quality")
def quality_shell():
    return page(SHELL_TMPL, active="/quality", title="Quality",
                sub="Bug fix quality, reopen loops, return-rate trends",
                teach="This screen arrives in Phase 3 of the v3 rollout.", links="")


@v3.route("/planning")
def planning_shell():
    return page(SHELL_TMPL, active="/planning", title="Sprint & Planning",
                sub="Commitment vs completion, planning hygiene, due-date slip",
                teach="Release Readiness is the interim commitment view until sprint boards are configured:",
                links='<a class="btn" href="/reports/release">Release Readiness →</a>')


@v3.route("/investigate")
def investigate_shell():
    return page(SHELL_TMPL, active="/investigate", title="Ticket Investigator",
                sub="Full forensic timeline for one ticket",
                teach="The timeline UI arrives in Phase 2 of the v3 rollout.", links="")
