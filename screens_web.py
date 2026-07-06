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


@v3.route("/planning")
def planning_shell():
    return page(SHELL_TMPL, active="/planning", title="Sprint & Planning",
                sub="Commitment vs completion, planning hygiene, due-date slip",
                teach="Release Readiness is the interim commitment view until sprint boards are configured:",
                links='<a class="btn" href="/reports/release">Release Readiness →</a>')


# ---------------------------------------------------------------------------
# Screen 3 — QA Handoff (FR-Q1..Q4)
# ---------------------------------------------------------------------------

QA_TMPL = """
<h1>QA Handoff</h1>
<div class="sub">Handoffs credited to the transition author · checks are binary Pass / Needs info</div>
""" + FILTER_BAR + """
<h2>Return rate by developer <span class="muted">(returns attributed to the most recent handoff author)</span></h2>
<table>
<tr><th>Developer</th><th>Handoffs</th><th>Returns</th><th>Return rate</th></tr>
{% for r in rates %}
<tr><td>{{ r.developer }}</td><td>{{ r.handoffs }}</td><td>{{ r.returns }}</td>
<td><span class="pill {{ 'ok' if (r.rate_pct or 0) < 25 else ('warn' if (r.rate_pct or 0) < 50 else 'bad') }}">{{ r.rate_label }}</span></td></tr>
{% else %}<tr><td colspan="4" class="muted">No handoffs in the window.</td></tr>{% endfor %}
</table>

<h2>Handoff feed <span class="muted">(transitions into QA)</span> · <a href="/api/v2/handoffs.csv?{{ request.query_string.decode() }}" download>CSV</a></h2>
<table>
<tr><th>When</th><th>Moved by</th><th>Issue</th><th>Summary</th><th>Previous → New</th><th>Current status</th><th>Assignee now</th><th>Comment</th><th>PR/build</th><th>Result</th></tr>
{% for h in handoffs %}
<tr><td>{{ h.ts.strftime('%Y-%m-%d %H:%M') }}</td><td>{{ h.developer }}</td>
<td><a href="{{ h.issue.url }}" target="_blank">{{ h.issue.key }}</a></td><td>{{ h.issue.summary }}</td>
<td>{{ h.prev_status }} → {{ h.new_status }}</td><td>{{ h.issue.status }}</td><td>{{ h.issue.assignee }}</td>
<td>{{ '✓' if h.has_comment else '✗' }}</td><td>{{ '✓' if h.has_pr else '✗' }}</td>
<td><span class="pill {{ 'ok' if h.result=='Pass' else 'warn' }}">{{ h.result }}</span></td></tr>
{% else %}<tr><td colspan="10" class="muted">No handoffs in the window.</td></tr>{% endfor %}
</table>

<h2>Returned from QA <span class="muted">(back-transitions)</span> · <a href="/api/v2/returns.csv?{{ request.query_string.decode() }}" download>CSV</a></h2>
<table>
<tr><th>When</th><th>Returned by</th><th>Issue</th><th>Summary</th><th>From → To</th><th>Current developer</th><th>Return reason</th></tr>
{% for r in returns %}
<tr><td>{{ r.ts.strftime('%Y-%m-%d %H:%M') }}</td><td>{{ r.returned_by }}</td>
<td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td><td>{{ r.issue.summary }}</td>
<td>{{ r.from_status }} → {{ r.to_status }}</td><td>{{ r.issue.assignee }}</td>
<td class="muted">{{ r.reason[:140] if r.reason else '— no comment near transition —' }}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No returns in the window.</td></tr>{% endfor %}
</table>
"""


def _qa_data():
    import qa_handoff as qh
    project, developer, start, end = parse_filters()
    if not start and not end:
        start = A.now_utc() - dt.timedelta(days=14)
    issues = _issues(project)
    return (qh.handoff_feed(issues, developer, start, end, dr._dev_match),
            qh.returned_feed(issues, developer, start, end, dr._dev_match),
            qh.return_rates(issues, start, end))


@v3.route("/qa")
def qa_screen():
    handoffs, returns, rates = _qa_data()
    return page(QA_TMPL, active="/qa", handoffs=handoffs, returns=returns, rates=rates)


@v3.route("/api/v2/handoffs.csv")
def handoffs_csv():
    handoffs, _r, _ra = _qa_data()
    rows = [[h["ts"].strftime("%Y-%m-%d %H:%M"), h["developer"], h["issue"].key,
             h["issue"].summary, h["prev_status"], h["new_status"], h["issue"].status,
             h["issue"].assignee, "Yes" if h["has_comment"] else "No",
             "Yes" if h["has_pr"] else "No", h["result"]] for h in handoffs]
    return csv_response(["When", "Moved by", "Issue", "Summary", "Previous", "New",
                         "Current status", "Current assignee", "Handoff comment",
                         "PR reference", "Result"], rows, "qa_handoffs.csv")


@v3.route("/api/v2/returns.csv")
def returns_csv():
    _h, returns, _ra = _qa_data()
    rows = [[r["ts"].strftime("%Y-%m-%d %H:%M"), r["returned_by"], r["issue"].key,
             r["issue"].summary, r["from_status"], r["to_status"], r["issue"].assignee,
             r["reason"][:200]] for r in returns]
    return csv_response(["When", "Returned by", "Issue", "Summary", "From", "To",
                         "Current developer", "Reason"], rows, "qa_returns.csv")


@v3.route("/api/v2/qa.json")
def qa_json():
    handoffs, returns, rates = _qa_data()
    return jsonify({
        "rates": rates,
        "handoffs": [{"ts": h["ts"].isoformat(), "developer": h["developer"],
                      "key": h["issue"].key, "result": h["result"]} for h in handoffs],
        "returns": [{"ts": r["ts"].isoformat(), "returned_by": r["returned_by"],
                     "key": r["issue"].key, "reason": r["reason"][:200]} for r in returns]})


# ---------------------------------------------------------------------------
# Screen 7 — Ticket Investigator (FR-T1..T4)
# ---------------------------------------------------------------------------

BUCKET_COLORS = {"todo": "#8993a4", "active_dev": "#0065ff", "qa_stage": "#ffab00",
                 "paused": "#ff7452", "rework": "#de350b", "done": "#36b37e",
                 None: "#c1c7d0"}

INVEST_TMPL = """
<h1>Ticket Investigator</h1>
<div class="sub">Full forensic timeline for one ticket — transitions, comments, worklogs, field changes</div>
<form method="get" class="filterbar">
  <label>Issue key<input name="key" value="{{ request.args.get('key','') }}" placeholder="LIFEDATAV2-1234" required></label>
  <label>From<input type="date" name="start" value="{{ request.args.get('start','') }}"></label>
  <label>To<input type="date" name="end" value="{{ request.args.get('end','') }}"></label>
  <button class="btn" type="submit">Investigate</button>
</form>
{% if err %}<div class="banner">{{ err }}</div>{% endif %}
{% if issue %}
<div class="sectionbox">
  <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div><a href="{{ issue.url }}" target="_blank"><b>{{ issue.key }}</b></a> {{ issue.summary }}</div>
    <div><span class="pill">{{ issue.type }}</span> <span class="pill">{{ issue.status }}</span> <span class="pill">{{ issue.assignee }}</span>
    <a class="btn-ghost" href="{{ issue.url }}" target="_blank">Open in Jira ↗</a></div>
  </div>
  {% if ribbon %}
  <div style="display:flex;height:22px;border-radius:6px;overflow:hidden;margin-top:12px;font-size:11px;color:#fff;font-weight:600">
    {% for seg in ribbon %}<div title="{{ seg.label }}: {{ seg.days }}d ({{ seg.pct }}%)"
      style="width:{{ seg.pct }}%;background:{{ seg.color }};display:flex;align-items:center;justify-content:center;overflow:hidden;text-shadow:0 1px 1px rgba(0,0,0,.35)">{% if seg.pct >= 10 %}{{ seg.label }} {{ seg.days }}d{% endif %}</div>{% endfor %}
  </div>
  <div class="muted" style="margin-top:4px">{% for seg in ribbon %}<span style="margin-right:12px"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{{ seg.color }};margin-right:3px"></span>{{ seg.label }} {{ seg.days }}d</span>{% endfor %}</div>
  {% endif %}
</div>
<div style="border-left:2px solid #dfe1e6;margin-left:10px;padding-left:22px">
  {% for item in timeline %}
    {% if item.gap %}
    <div style="margin:14px 0;padding:8px 14px;background:#fff7e6;border:1px dashed #ffc46b;border-radius:8px;display:inline-block;color:#974f00;font-size:13px">
      ⏸ {{ item.gap }} days — no activity</div>
    {% else %}
    <div style="margin:10px 0;position:relative">
      <span style="position:absolute;left:-29px;top:3px;width:12px;height:12px;border-radius:50%;background:{{ item.color }};border:2px solid #fff;box-shadow:0 0 0 1px #dfe1e6"></span>
      <span class="muted">{{ item.e.ts.strftime('%Y-%m-%d %H:%M') }}</span>
      <b style="margin:0 6px">{{ item.icon }} {{ item.e.kind }}</b>
      <span>{{ item.e.actor }}</span>
      {% if item.e.kind in ('status','assignee','duedate','startdate','flag','sprint') %}
        <span class="muted">{{ item.e.frm or '—' }} → {{ item.e.to or '—' }}</span>
      {% elif item.e.kind == 'worklog' %}
        <span class="pill">{{ (item.e.seconds/3600)|round(1) }}h</span> <span class="muted">{{ item.e.detail[:120] }}</span>
      {% else %}
        <div class="muted" style="margin:2px 0 0 6px;max-width:820px">{{ item.e.detail[:400] }}</div>
      {% endif %}
    </div>
    {% endif %}
  {% else %}<p class="muted">No events in the selected range.</p>{% endfor %}
</div>
{% elif not request.args.get('key') %}
<div class="sectionbox"><p class="muted">Enter an issue key to reconstruct its full history. The Investigator answers
“why did this ticket take six weeks?” — inactivity gaps, reopen loops, and QA parking become visible at a glance.</p></div>
{% endif %}
"""

_EVENT_ICON = {"status": "⇄", "assignee": "👤", "comment": "💬", "worklog": "⏱",
               "duedate": "📅", "startdate": "📅", "flag": "🚩", "sprint": "🏁"}


@v3.route("/investigate")
def investigate_screen():
    key = (request.args.get("key") or "").strip().upper()
    if not key:
        return page(INVEST_TMPL, active="/investigate", issue=None, err=None)
    _p, _d, start, end = parse_filters()
    issue = next((i for i in _issues(None) if i.key.upper() == key), None)
    if issue is None:
        try:
            raw = jc.fetch_single_issue(key)  # live, uncached, full history
        except Exception:
            raw = None
        if raw:
            issue = dr.load_dev_issues([raw], jc.detect_custom_fields())[0]
    if issue is None:
        return page(INVEST_TMPL, active="/investigate", issue=None,
                    err=f"Issue {key} not found (or Jira unreachable). Check the key.")
    events = [e for e in activity.events_for(issue)
              if (not start or e.ts >= start) and (not end or e.ts < end)]
    gap_days = st.load()["gap_days"]
    timeline, prev = [], None
    for e in events:
        if prev is not None:
            gap = (e.ts - prev).total_seconds() / 86400
            if gap >= gap_days:
                timeline.append({"gap": round(gap)})
        timeline.append({"e": e, "icon": _EVENT_ICON.get(e.kind, "•"),
                         "color": "#0065ff" if e.kind == "status" else "#c1c7d0", "gap": None})
        prev = e.ts
    # Stage ribbon (FR-T3): lifetime seconds per bucket from the status timeline.
    per_bucket = {}
    for status, enter, exit_ in issue.timeline.segments:
        b = st.bucket_of(status) or "unmapped"
        per_bucket[b] = per_bucket.get(b, 0) + (exit_ - enter).total_seconds()
    total = sum(per_bucket.values()) or 1
    ribbon = [{"label": st.BUCKET_LABELS.get(b, b), "days": round(secs / 86400, 1),
               "pct": round(100 * secs / total, 1),
               "color": BUCKET_COLORS.get(b if b != "unmapped" else None)}
              for b, secs in sorted(per_bucket.items(), key=lambda kv: -kv[1]) if secs > 0]
    return page(INVEST_TMPL, active="/investigate", issue=issue, err=None,
                timeline=timeline, ribbon=ribbon)


# ---------------------------------------------------------------------------
# Screen 4 — Flow Analytics (FR-F1..F6)
# ---------------------------------------------------------------------------

FLOW_TMPL = """
<h1>Flow Analytics</h1>
<div class="sub">Cycle time, stage breakdown, bottlenecks, focus · medians and p85, never bare averages · <a href="/reports/time-in-status">Time in Status →</a></div>
""" + FILTER_BAR + """
<div class="cards">
  <div class="card"><div class="n">{{ hfmt(stats.dev_to_qa.median) }}</div><div class="l">{{ g('dev_to_qa','Dev → QA')|safe }} median (n={{ stats.dev_to_qa.n }})</div></div>
  <div class="card"><div class="n">{{ hfmt(stats.dev_to_qa.p85) }}</div><div class="l">{{ g('p85','Dev → QA p85')|safe }}</div></div>
  <div class="card"><div class="n">{{ hfmt(stats.cycle.median) }}</div><div class="l">{{ g('cycle_time','Cycle time')|safe }} median (n={{ stats.cycle.n }})</div></div>
  <div class="card"><div class="n">{{ hfmt(stats.cycle.p85) }}</div><div class="l">{{ g('p85','Cycle p85')|safe }}</div></div>
</div>

<h2>{{ g('multiple_active','Multiple active tickets')|safe }} <span class="muted">(rule: one active ticket; QA-stage excluded)</span></h2>
<table>
<tr><th>Developer</th><th>Active count</th><th>Tickets</th></tr>
{% for v in violations %}
<tr><td>{{ v.developer }}</td><td><span class="pill bad">{{ v.count }}</span></td>
<td>{% for t in v.tickets %}<a href="{{ t.url }}" target="_blank">{{ t.key }}</a> <span class="muted">({{ t.status }})</span>{{ ', ' if not loop.last }}{% endfor %}</td></tr>
{% else %}<tr><td colspan="3" class="muted">No one holds more than one active-dev ticket. 🎉</td></tr>{% endfor %}
</table>

<h2>{{ g('bottleneck','Team bottleneck')|safe }} <span class="muted">(median days per status)</span></h2>
<table>
<tr><th>Status</th><th>Bucket</th><th>Median days</th><th>p85 days</th><th>Tickets</th><th></th></tr>
{% for b in bneck[:12] %}
<tr><td>{{ b.status }}</td><td><span class="pill">{{ b.bucket }}</span></td>
<td>{{ b.median_days }}</td><td>{{ b.p85_days }}</td><td>{{ b.n }}</td>
<td><div style="background:#dfe7f5;border-radius:4px;height:10px;width:100%;max-width:220px"><div style="background:#0052cc;border-radius:4px;height:10px;width:{{ (100 * b.median_days / bneck[0].median_days)|round|int if bneck else 0 }}%"></div></div></td></tr>
{% else %}<tr><td colspan="6" class="muted">No stage data.</td></tr>{% endfor %}
</table>

<h2>Per-ticket stage breakdown <span class="muted">(active + recently completed)</span> · <a href="/api/v2/flow.csv?{{ request.query_string.decode() }}" download>CSV</a></h2>
<table>
<tr><th>Issue</th><th>Summary</th><th>Developer</th><th>{{ g('dev_to_qa','Dev → QA h')|safe }}</th><th>{{ g('cycle_time','Cycle h')|safe }}</th><th>{{ g('reopen_loop','Rework loops')|safe }}</th><th style="min-width:240px">Stage share</th></tr>
{% for r in rows[:80] %}
<tr><td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td>
<td>{{ r.issue.summary[:60] }}</td><td>{{ r.issue.assignee }}</td>
<td>{{ r.dev_to_qa_h if r.dev_to_qa_h is not none else '—' }}</td>
<td>{{ r.cycle_h if r.cycle_h is not none else '—' }}</td>
<td>{% if r.rework_loops >= 2 %}<span class="pill bad">{{ r.rework_loops }}</span>{% elif r.rework_loops %}<span class="pill warn">{{ r.rework_loops }}</span>{% else %}0{% endif %}</td>
<td><div style="display:flex;height:16px;border-radius:4px;overflow:hidden">
  {% for s in r.segments %}<div title="{{ s.bucket }}: {{ s.days }}d ({{ s.pct }}%)" style="width:{{ s.pct }}%;background:{{ bucket_colors.get(s.bucket,'#c1c7d0') }}"></div>{% endfor %}
</div></td></tr>
{% else %}<tr><td colspan="7" class="muted">No tickets entered development in the window.</td></tr>{% endfor %}
</table>
<div class="muted">{% for b, c in bucket_colors.items() %}{% if b %}<span style="margin-right:12px"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{{ c }};margin-right:3px"></span>{{ bucket_labels.get(b,b) }}</span>{% endif %}{% endfor %}</div>

<h2>{{ g('focus','Developer focus')|safe }} <span class="muted">(distinct tickets touched per day)</span></h2>
<table>
<tr><th>Date</th><th>Developer</th><th>Distinct tickets</th><th>Total activities</th><th>Status changes</th><th>Comments</th><th>Worklogs</th></tr>
{% for f in focus[:60] %}
<tr><td>{{ f[0] }}</td><td>{{ f[1] }}</td>
<td>{% if f[2] > 3 %}<span class="pill warn">{{ f[2] }}</span>{% else %}{{ f[2] }}{% endif %}</td>
<td>{{ f[3] }}</td><td>{{ f[4] }}</td><td>{{ f[5] }}</td><td>{{ f[6] }}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No activity in the window.</td></tr>{% endfor %}
</table>
"""


def _hfmt(hours):
    if hours is None:
        return "—"
    return f"{hours/24:.1f}d" if hours >= 48 else f"{hours:.0f}h"


def _flow_data():
    import flow_quality as fq
    project, developer, start, end = parse_filters()
    if not start and not end:
        start = A.now_utc() - dt.timedelta(days=30)
    issues = _issues(project)
    rows = fq.cycle_rows(issues, developer, start, end, dr._dev_match)
    focus = dr.developer_focus(issues, developer=developer, start=start, end=end)["rows"]
    return (rows, fq.cycle_stats(rows), fq.bottleneck(issues),
            fq.multiple_active(issues, developer, dr._dev_match), focus)


@v3.route("/flow")
def flow_screen():
    from metrics_glossary import gloss
    rows, stats, bneck, violations, focus = _flow_data()
    return page(FLOW_TMPL, active="/flow", rows=rows, stats=stats, bneck=bneck,
                violations=violations, focus=focus, hfmt=_hfmt, g=gloss,
                bucket_colors=BUCKET_COLORS, bucket_labels=st.BUCKET_LABELS)


@v3.route("/api/v2/flow.csv")
def flow_csv():
    rows, _s, _b, _v, _f = _flow_data()
    out = [[r["issue"].key, r["issue"].summary, r["issue"].assignee,
            r["dev_to_qa_h"], r["cycle_h"], r["rework_loops"],
            "; ".join(f"{s['bucket']}={s['days']}d" for s in r["segments"])] for r in rows]
    return csv_response(["Issue", "Summary", "Developer", "Dev to QA hours",
                         "Cycle hours", "Rework loops", "Stage breakdown"], out, "flow.csv")


@v3.route("/api/v2/flow.json")
def flow_json():
    rows, stats, bneck, violations, _f = _flow_data()
    return jsonify({"stats": stats, "bottleneck": bneck,
                    "violations": [{"developer": v["developer"], "count": v["count"],
                                    "tickets": [t.key for t in v["tickets"]]} for v in violations],
                    "tickets": [{"key": r["issue"].key, "dev_to_qa_h": r["dev_to_qa_h"],
                                 "cycle_h": r["cycle_h"], "rework_loops": r["rework_loops"]}
                                for r in rows]})


# ---------------------------------------------------------------------------
# Screen 5 — Quality (FR-QL1..QL3)
# ---------------------------------------------------------------------------

QUALITY_TMPL = """
<h1>Quality</h1>
<div class="sub">Bug lens, reopen loops, team return-rate trend · coaching data, not a scoreboard</div>
""" + FILTER_BAR + """
<h2>{{ g('return_rate','Bug fix quality by developer')|safe }} · <a href="/api/v2/quality.csv?{{ request.query_string.decode() }}" download>CSV</a></h2>
<table>
<tr><th>Developer</th><th>Bugs</th><th>Completed</th><th>{{ g('return','Returned from QA')|safe }}</th><th>{{ g('median','Median resolution')|safe }}</th><th>{{ g('return_rate','Return rate')|safe }}</th></tr>
{% for r in bugs %}
<tr><td>{{ r.developer }}</td><td>{{ r.count }}</td><td>{{ r.done }}</td><td>{{ r.returned }}</td>
<td>{{ hfmt(r.median_hours) }}</td><td>{{ r.rate_label }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No bugs in the window.</td></tr>{% endfor %}
</table>

<h2>{{ g('reopen_loop','Reopen loops')|safe }} <span class="muted">(2+ rework cycles)</span></h2>
<table>
<tr><th>Issue</th><th>Summary</th><th>Developer</th><th>Status</th><th>Loops</th></tr>
{% for r in loops %}
<tr><td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td>
<td>{{ r.issue.summary }}</td><td>{{ r.issue.assignee }}</td><td>{{ r.issue.status }}</td>
<td><span class="pill bad">{{ r.loops }}×</span></td></tr>
{% else %}<tr><td colspan="5" class="muted">No reopen loops. 🎉</td></tr>{% endfor %}
</table>

<h2>Team return-rate trend <span class="muted">(weekly, raw counts shown)</span></h2>
<table>
<tr><th>Week</th><th>Handoffs</th><th>Returns</th><th>Return rate</th><th></th></tr>
{% for w in trend %}
<tr><td>{{ w.week }}</td><td>{{ w.handoffs }}</td><td>{{ w.returns }}</td><td>{{ w.rate_label }}</td>
<td><div style="background:#dfe7f5;border-radius:4px;height:10px;width:100%;max-width:200px"><div style="background:{{ '#de350b' if (w.rate_pct or 0) >= 50 else '#0052cc' }};border-radius:4px;height:10px;width:{{ w.rate_pct or 0 }}%"></div></div></td></tr>
{% else %}<tr><td colspan="5" class="muted">No handoffs recorded in the trend window.</td></tr>{% endfor %}
</table>
"""


def _quality_data():
    import flow_quality as fq
    project, developer, start, end = parse_filters()
    issues = _issues(project)
    return (fq.bug_lens(issues, developer, start, end, dr._dev_match),
            fq.reopen_loops(issues), fq.return_trend(issues))


@v3.route("/quality")
def quality_screen():
    from metrics_glossary import gloss
    bugs, loops, trend = _quality_data()
    return page(QUALITY_TMPL, active="/quality", bugs=bugs, loops=loops, trend=trend,
                hfmt=_hfmt, g=gloss)


@v3.route("/api/v2/quality.csv")
def quality_csv():
    bugs, _l, _t = _quality_data()
    rows = [[b["developer"], b["count"], b["done"], b["returned"],
             b["median_hours"], b["rate_label"]] for b in bugs]
    return csv_response(["Developer", "Bugs", "Completed", "Returned",
                         "Median resolution hours", "Return rate"], rows, "quality.csv")


@v3.route("/api/v2/quality.json")
def quality_json():
    bugs, loops, trend = _quality_data()
    return jsonify({"bugs": bugs, "trend": trend,
                    "reopen_loops": [{"key": r["issue"].key, "loops": r["loops"]} for r in loops]})
