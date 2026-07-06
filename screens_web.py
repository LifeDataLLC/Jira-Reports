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

import config as legacy
import jira_client as jc
import settings as st

v3 = Blueprint("v3", __name__)


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
