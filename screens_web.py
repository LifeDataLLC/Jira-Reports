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
import secrets
import threading

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


_parsed_lock = threading.Lock()
_parsed_cache: dict = {}   # scope -> (raw_list, custom_fields, parsed_issues)


def _issues(project=None):
    """Parsed DevIssues for a project scope, memoized against the raw dataset.

    Turning the raw Jira payload into DevIssues (changelog → events, comments,
    and a full Timeline per ticket) costs ~1s for a few thousand tickets, and it
    used to run on EVERY request even though the raw data was already cached.
    jira_client hands back the same list object until a refresh replaces it, so
    an identity check tells us whether the parse is still valid — we hold a
    reference to that list, which keeps the identity comparison sound.
    """
    raw = jc.fetch_dev_dataset(project)
    cf = jc.detect_custom_fields()
    with _parsed_lock:
        hit = _parsed_cache.get(project)
        if hit is not None and hit[0] is raw and hit[1] == cf:
            return hit[2]
    parsed = dr.load_dev_issues(raw, cf)      # parse outside the lock
    with _parsed_lock:
        _parsed_cache[project] = (raw, cf, parsed)
    return parsed


def _issues_in_range(project, start, end):
    """Issues for the given project scope, further restricted — when a date or
    range is selected — to tickets edited (a comment or status change) within it.
    With no date selected, returns the full set. Applies to every report."""
    issues = _issues(project)
    if start or end:
        issues = [i for i in issues if activity.edited_in_range(i, start, end)]
    return issues


# ---------------------------------------------------------------------------
# Shared chrome — purpose-grouped nav (FR-U2) + global filter bar (FR-U1)
# ---------------------------------------------------------------------------

NAV = [
    ("/my-day", "My Day"), ("/attention", "Attention"), ("/qa", "QA"),
    ("/flow", "Flow"), ("/active-time", "Time-Spent Dashboards"), ("/quality", "Quality"),
    ("/release", "Release"), ("/investigate", "Investigate"), ("/exec", "Trends"),
    ("/settings", "Settings"),
]

CHROME_TOP = """
<style>
 :root{--green:#1fa963;--green-d:#17864e;--green-t:#e9f6ef;--ink:#212121;--ink2:#333;
   --muted:#6b6b6b;--line:#e7e8e7;--bg:#f6f7f6;--white:#fff;--red:#d64545;--red-t:#fbeaea;
   --amber:#b7791f;--amber-t:#fdf3e3;--radius:12px}
 *{box-sizing:border-box}
 body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}
 nav{background:var(--ink);padding:0 18px;display:flex;gap:2px;flex-wrap:wrap;align-items:center;min-height:52px}
 nav a{color:#c9cbc9;text-decoration:none;font-size:13px;padding:8px 12px;border-radius:8px;font-weight:500}
 nav a:hover{color:#fff;background:rgba(255,255,255,.09)}
 nav a.active{color:#fff;background:var(--green)}
 nav .brand{color:#fff;font-weight:800;margin-right:14px;font-size:15px;letter-spacing:-.2px}
 nav .brand .dot{color:var(--green)}
 .wrap{max-width:1120px;margin:26px auto;padding:0 20px}
 h1{font-size:24px;margin:0 0 4px;font-weight:800;letter-spacing:-.4px}
 h2{font-size:16px;margin:26px 0 10px;font-weight:700}
 .sub{color:var(--muted);font-size:13.5px;margin-bottom:20px}
 a{color:var(--green-d);text-decoration:none}a:hover{text-decoration:underline}
 .cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
 .card{background:var(--white);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;flex:1;min-width:150px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .card .n{font-size:26px;font-weight:800}.card .l{color:var(--muted);font-size:12px;margin-top:2px}
 table{width:100%;border-collapse:collapse;background:var(--white);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;margin-bottom:8px}
 th,td{text-align:left;padding:10px 13px;border-bottom:1px solid var(--line);font-size:13px}
 th{background:#fafbfa;color:var(--muted);font-weight:600;position:sticky;top:0;cursor:pointer;user-select:none}
 tr:hover td{background:#fafbfa}
 .pill{display:inline-block;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:600;background:#eef0ee;color:var(--ink2)}
 .pill.ok,.ok{background:var(--green-t);color:var(--green-d)}
 .pill.warn,.warn{background:var(--amber-t);color:var(--amber)}
 .pill.bad,.bad{background:var(--red-t);color:var(--red)}
 .muted{color:var(--muted);font-size:12px}
 .sectionbox{background:var(--white);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .banner{background:var(--amber-t);border:1px solid #f0dcae;color:var(--amber);border-radius:var(--radius);padding:11px 16px;margin-bottom:16px;font-size:13px}
 .fresh{color:#9a9a9a;font-size:11px;text-align:right;margin:2px 0 10px}
 .filterbar{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;background:var(--white);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .filterbar label{font-size:11px;color:var(--muted);font-weight:600}
 .filterbar input,.filterbar select{display:block;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;margin-top:3px;background:#fff;color:var(--ink)}
 .filterbar input:focus,.filterbar select:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px rgba(31,169,99,.18)}
 .btn{background:var(--green);color:#fff;padding:9px 16px;border-radius:8px;font-size:13px;font-weight:600;border:none;cursor:pointer;display:inline-block;text-decoration:none}
 .btn:hover{background:var(--green-d);text-decoration:none;color:#fff}
 .btn-ghost{background:#fff;color:var(--ink2);border:1px solid var(--line);padding:8px 14px;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;display:inline-block}
 .btn-ghost:hover{background:#f2f3f2;text-decoration:none}
 .chip{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;margin:1px 3px 1px 0}
 .checkrow,.check{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;font-size:12.5px;font-weight:500;margin:3px 6px 0 0;border:1px solid transparent}
 .c-pass{background:var(--green-t);color:var(--green-d)}
 .c-fail{background:var(--red-t);color:var(--red);border-color:#f0c8c8;font-weight:600}
 .c-na{background:#f1f2f1;color:#9a9a9a}
 .glossary{border-bottom:1px dotted #b3b3b3;cursor:help}
 .controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
 .controls .ctl-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-right:2px}
 .chipbtn{background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px 13px;font-size:12.5px;color:var(--ink2);cursor:pointer;font-weight:500}
 .chipbtn:hover{border-color:#bdbfbd}
 .chipbtn.active{background:var(--green);border-color:var(--green);color:#fff;font-weight:600}
 .md-summary{background:var(--white);border:1px solid var(--line);border-radius:var(--radius);padding:15px 18px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .md-progress{height:9px;background:#eceeec;border-radius:999px;overflow:hidden;margin-bottom:10px}
 .md-progress-bar{height:100%;background:var(--green);border-radius:999px}
 .md-summary-text{font-size:13px;color:var(--muted);display:flex;gap:18px;flex-wrap:wrap;align-items:baseline}
 .md-summary-text .big{color:var(--ink);font-size:16px;font-weight:800}
 .md-kpi{display:inline-flex;align-items:center;gap:6px}
 .md-kpi .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
 .md-card{background:var(--white);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);padding:14px 18px;margin-bottom:12px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .md-card.clean{border-left-color:var(--green)}
 .md-card.attention{border-left-color:var(--red)}
 .md-card.active-now{box-shadow:0 0 0 3px var(--green-t),0 3px 12px rgba(31,169,99,.18)}
 .md-ribbon{display:inline-flex;align-items:center;gap:7px;background:var(--green);color:#fff;font-size:11px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;padding:4px 11px;border-radius:999px;margin-bottom:9px}
 .md-ribbon .live{width:7px;height:7px;border-radius:50%;background:#fff;animation:mdpulse 1.5s infinite}
 @keyframes mdpulse{0%{box-shadow:0 0 0 0 rgba(255,255,255,.7)}70%{box-shadow:0 0 0 6px rgba(255,255,255,0)}100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}}
 .md-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start}
 .md-title{font-size:14.5px;line-height:1.45}
 .md-title a{font-weight:800;color:var(--ink)}
 .md-tags{display:flex;gap:6px;flex-wrap:wrap;align-items:center;flex-shrink:0}
 .md-checks{margin-top:10px}
</style>
<nav>
 <span class="brand">LifeData<span class="dot">.</span> Reports</span>
 {NAVLINKS}
</nav>

<script>
document.addEventListener('click',function(ev){
  var th=ev.target.closest('th'); if(!th)return;
  var table=th.closest('table'); if(!table)return;
  var header=table.querySelector('tr'); if(!header||th.parentNode!==header)return;
  var col=Array.prototype.indexOf.call(header.children,th);
  var asc=th.getAttribute('data-dir')!=='asc';
  Array.prototype.forEach.call(header.children,function(h){h.removeAttribute('data-dir');
    var i=h.querySelector('.sort-ind'); if(i)i.remove();});
  th.setAttribute('data-dir',asc?'asc':'desc');
  var ind=document.createElement('span'); ind.className='sort-ind'; ind.style.color='#1fa963';
  ind.style.fontSize='10px'; ind.textContent=asc?' ▲':' ▼'; th.appendChild(ind);
  var rows=Array.prototype.slice.call(table.querySelectorAll('tr')).filter(function(r){
    return r!==header && !r.querySelector('td[colspan]');});
  function val(r){var c=r.children[col]; if(!c)return '';
    var t=c.textContent.trim(); var n=parseFloat(t.replace(/[^0-9.\\-]/g,''));
    return (t!=='' && !isNaN(n) && /\\d/.test(t)) ? n : t.toLowerCase();}
  rows.sort(function(a,b){var x=val(a),y=val(b);
    if(typeof x!==typeof y){x=String(x);y=String(y);}
    return x<y?(asc?-1:1):(x>y?(asc?1:-1):0);});
  rows.forEach(function(r){table.appendChild(r);});
});
</script>
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
    import auth
    user = auth.current_user()
    admin = bool(user and user.get("role") == "admin")
    # Until the other screens are ready, employees only get My Day, Release and
    # their own Time-Spent dashboard; admins see everything. (Route access is
    # also enforced server-side in app.py — this list only controls what the nav
    # offers.)
    items = list(NAV) if admin else [(h, l) for h, l in NAV
                                     if h in ("/my-day", "/release", "/active-time")]
    navlinks = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in items)
    if admin:
        navlinks += '<a href="/admin/users">Users</a>'
    if user:
        navlinks += (f'<span style="margin-left:auto;color:#9a9c9a;font-size:12px">'
                     f'{user["email"]} ({user["role"]}) · '
                     f'<a href="/change-password" style="color:#fff;text-decoration:underline">Change password</a> · '
                     f'<a href="/logout" style="color:#fff;text-decoration:underline">Log out</a></span>')
    chrome = CHROME_TOP.replace("{NAVLINKS}", navlinks) + _overlay()
    banner = unmapped_banner() if (show_banner and admin) else ""
    fresh = dt.datetime.now().strftime("%H:%M")
    shell = (chrome + '<div class="wrap">'
             + f'<div class="fresh">data as of {fresh} · cached ~5 min</div>'
             + banner + body + "</div>")
    _inject_filter_ctx(ctx, user, admin)
    return render_template_string(shell, request=request, st=st, **ctx)


def _inject_filter_ctx(ctx, user, admin):
    """Provide the project + developer dropdown options to every screen's
    FILTER_BAR. Role-aware, mirroring My Day: admins pick any (or all)
    developer; employees are locked to their one linked developer. Explicit
    values passed by a route still win (setdefault)."""
    psel, _scope = current_project_selection()
    report = jc.report_projects()
    # "All spaces" only makes sense when more than one space is configured.
    proj_opts = report if len(report) <= 1 else [{"key": "all", "name": "All spaces"}] + report
    ctx.setdefault("filter_projects", proj_opts)
    ctx.setdefault("filter_project_selected", psel)
    # Echo whatever the user typed. A screen that applies a default window when
    # these are blank should pass its own values instead, so the boxes name the
    # range actually on screen rather than sitting empty beside it.
    ctx.setdefault("filter_start", (request.args.get("start") or "").strip())
    ctx.setdefault("filter_end", (request.args.get("end") or "").strip())
    if admin:
        import auth
        ctx.setdefault("filter_devs", auth.visible_developers())
        ctx.setdefault("filter_dev_selected", (request.args.get("developer") or "").strip())
        ctx.setdefault("filter_dev_locked", False)
    else:
        own = (user or {}).get("developer_id") or (user or {}).get("developer")
        own_name = (user or {}).get("developer") or own
        ctx.setdefault("filter_devs", [{"id": own, "name": own_name}] if own else [])
        ctx.setdefault("filter_dev_selected", own or "")
        ctx.setdefault("filter_dev_locked", True)


PROJECT_SELECT = """
  <label>Project
    <select name="project">
      {% for p in filter_projects %}<option value="{{ p.key }}"{% if p.key == filter_project_selected %} selected{% endif %}>{{ p.name }}</option>{% endfor %}
    </select>
  </label>"""

DEV_SELECT = """
  <label>Developer
    <select name="developer"{% if filter_dev_locked %} disabled title="Your account is linked to one developer"{% endif %}>
      {% if not filter_dev_locked %}<option value="">All developers</option>{% endif %}
      {% for d in filter_devs %}<option value="{{ d.id }}"{% if d.id == filter_dev_selected or d.name == filter_dev_selected %} selected{% endif %}>{{ d.name }}</option>{% endfor %}
    </select>
  </label>"""

FILTER_BAR = """
<form method="get" class="filterbar" id="globalFilters">""" + PROJECT_SELECT + DEV_SELECT + """
  <label>Start<input type="date" name="start" value="{{ filter_start }}"></label>
  <label>End<input type="date" name="end" value="{{ filter_end }}"></label>
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
        var el=f.querySelector('[name='+k+']');
        if(saved[k]&&el&&!el.disabled&&!el.value)el.value=saved[k];
      });}catch(e){}
  }
  f.addEventListener('submit',function(){
    var data={}; ['project','developer','start','end'].forEach(function(k){
      var el=f.querySelector('[name='+k+']'); if(el&&!el.disabled&&el.value)data[k]=el.value;});
    try{localStorage.setItem(KEY,JSON.stringify(data));}catch(e){}
  });
})();
</script>
"""


def current_project_selection():
    """(selected_value, fetch_scope). selected_value is 'all' or a single project
    key (for the dropdown); fetch_scope is what to pass to fetch_dev_dataset —
    the comma-joined spaces for 'all', else the single key.

    With no explicit choice the default follows the admin's configured scope
    (Settings → Projects shown in views), so today's behavior is preserved and
    'All'/the other space are opt-in."""
    keys = jc.report_project_keys()
    raw = (request.args.get("project") or "").strip()
    if raw == "all":
        return "all", ",".join(keys)
    if raw and raw in keys:
        return raw, raw
    configured = [k for k in jc.configured_projects() if k in keys]
    if len(configured) == 1:
        return configured[0], configured[0]
    return "all", ",".join(keys)


def parse_filters():
    """Shared filter values for the FILTER_BAR screens. Employees are locked to
    their own linked developer (as on My Day), regardless of the URL."""
    import auth
    user = auth.current_user()
    if user and user.get("role") != "admin":
        # linked developer, or a sentinel that matches nothing if unlinked
        developer = user.get("developer_id") or user.get("developer") or "\x00nomatch"
    else:
        developer = (request.args.get("developer") or "").strip() or None
    _selected, project = current_project_selection()
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
<form method="post" style="margin-bottom:14px">
  <button class="btn-ghost" name="load_workflow" value="1" type="submit"
    onclick="return confirm('Overwrite the status classification, thresholds, and active-status lanes with the LIFEDATAV2 workflow defaults? Your other settings are kept.')">
    ↻ Load LIFEDATAV2 workflow defaults</button>
  <span class="muted">maps every workflow status to a bucket, sets the active-work lanes + pauses, and enables worklog/due-date rules</span>
</form>
<form method="post">
<div class="sectionbox">
  <h2 style="margin-top:0">Projects shown in views</h2>
  <p class="muted">Choose which Jira project spaces to include across every screen. Check both to combine them.</p>
  {% for pr in projects_list %}
  <label style="display:inline-block;font-size:13px;margin:3px 18px 3px 0">
    <input type="checkbox" name="project" value="{{ pr.key }}" {% if pr.key in selected_projects %}checked{% endif %}> {{ pr.name }} <span class="muted">({{ pr.key }})</span></label>
  {% else %}<span class="muted">No projects visible to the token (or Jira unreachable).</span>{% endfor %}
  <p class="muted">Leave all unchecked to use the default: <code>{{ default_projects }}</code>.</p>
</div>
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
      <td>{% if status in unmapped %}<span class="pill bad">needs classification</span>{% endif %}
        {% if status in s.active_statuses %}<span class="pill ok" title="Active work status — one at a time per lane; pause at end of day">⚡ active · {{ s.active_statuses[status].lane }}{% if s.active_statuses[status].pause %} → {{ s.active_statuses[status].pause }}{% endif %}</span>{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  <p class="muted">Blank threshold = the bucket default below. Unmapped statuses are excluded from metrics and flagged on every screen.
  <b>⚡ active</b> statuses are the blue "actively working" statuses (one per lane at a time; move to their pause at end of day).</p>
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
  <label style="font-size:13px">Mark <b>stale</b> after (days without a status change)
    <input type="number" min="1" name="stale_days" value="{{ s.stale_days }}" style="width:70px"></label><br><br>
  <label style="font-size:13px">Investigator gap spacer (days)
    <input type="number" min="1" name="gap_days" value="{{ s.gap_days }}" style="width:70px"></label><br><br>
  <label style="font-size:13px">Expected delivery pace (tickets reaching dev-complete / week; 0 = unset)
    <input type="number" min="0" step="0.5" name="release_capacity_per_week" value="{{ s.release_capacity_per_week }}" style="width:70px"></label><br><br>
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
<div class="sectionbox">
  <h2 style="margin-top:0">Developer dropdown</h2>
  <p class="muted">Check a developer to <b>hide</b> them from the My Day dropdown — e.g. past employees who still appear on old tickets.</p>
  {% for dvp in developers %}
  <label style="display:inline-block;font-size:13px;margin:3px 16px 3px 0">
    <input type="checkbox" name="hide_dev" value="{{ dvp.id }}" {% if dvp.id in hidden or dvp.name in hidden %}checked{% endif %}> Hide {{ dvp.name }}</label>
  {% else %}<span class="muted">No developers found in the synced data yet.</span>{% endfor %}
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
    ("due_date", "Due date present (gated)"), ("past_due", "Past due date"),
    ("has_release", "Belongs to a release"),
]


def _statuses_seen():
    # Pull from every reportable space so an admin can classify Support statuses
    # too — otherwise the "All spaces" view shows them as unclassified.
    seen = set(st.load()["status_buckets"])
    try:
        for raw in jc.fetch_dev_dataset(",".join(jc.report_project_keys())):
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
    if request.method == "POST" and request.form.get("load_workflow"):
        import auth
        st.apply_workflow(s)
        st.save(s)
        return page(SETTINGS_TMPL, active="/settings", show_banner=False, s=s, saved=True,
                    statuses=_statuses_seen(), unmapped=set(st.unmapped_statuses(set(_statuses_seen()))),
                    mapping=s["status_buckets"], thresholds=s["status_thresholds"],
                    buckets=st.BUCKETS, bucket_labels=st.BUCKET_LABELS,
                    bucket_default=lambda status: (s["bucket_thresholds"].get(s["status_buckets"].get(status)) or "—"),
                    gate_labels=GATE_LABELS, check_labels=CHECK_LABELS,
                    developers=auth.all_developers(), hidden=set(s.get("hidden_developers", [])),
                    projects_list=jc.list_projects(), selected_projects=set(s.get("projects", [])),
                    default_projects=", ".join(jc.PROJECT_KEYS))
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
        for num_key in ("handoff_window_hours", "silent_days", "gap_days", "stale_days"):
            try:
                # min 1: zero would mark EVERY ticket silent/stale (x >= 0 is always true)
                s[num_key] = max(int(form.get(num_key) or s[num_key]), 1)
            except ValueError:
                pass
        try:  # release capacity allows 0 (= unset)
            s["release_capacity_per_week"] = max(float(form.get("release_capacity_per_week") or 0), 0)
        except ValueError:
            pass
        for list_key in ("pr_keywords", "blocked_labels", "board_ids"):
            s[list_key] = [x.strip() for x in (form.get(list_key) or "").split(",") if x.strip()]
        s["start_date_field"] = (form.get("start_date_field") or "").strip() or None
        s["teams_webhook_url"] = (form.get("teams_webhook_url") or "").strip()
        if form.get("default_role") in ("developer", "lead", "exec"):
            s["default_role"] = form["default_role"]
        s["hidden_developers"] = form.getlist("hide_dev")
        new_projects = form.getlist("project")
        if new_projects != s.get("projects", []):
            s["projects"] = new_projects
            jc.clear_cache()  # project scope changed → drop stale Jira data
        st.save(s)
        saved = True
    statuses = _statuses_seen()
    unmapped = set(st.unmapped_statuses(set(statuses)))

    def bucket_default(status):
        b = s["status_buckets"].get(status)
        v = s["bucket_thresholds"].get(b) if b else None
        return v if v is not None else "—"

    import auth
    return page(SETTINGS_TMPL, active="/settings", show_banner=False, s=s, saved=saved,
                statuses=statuses, unmapped=unmapped, mapping=s["status_buckets"],
                thresholds=s["status_thresholds"], buckets=st.BUCKETS,
                bucket_labels=st.BUCKET_LABELS, bucket_default=bucket_default,
                gate_labels=GATE_LABELS, check_labels=CHECK_LABELS,
                developers=auth.all_developers(), hidden=set(s.get("hidden_developers", [])),
                projects_list=jc.list_projects(), selected_projects=set(s.get("projects", [])),
                default_projects=", ".join(jc.PROJECT_KEYS))


# ---------------------------------------------------------------------------
# Screen 1 — My Day (FR-M1/M2/M4/M5)
# ---------------------------------------------------------------------------

MYDAY_TMPL = """
<h1>My Day</h1>
<div class="sub">{% if show_all %}All your open assigned tickets — the status of your whole workload{% else %}Tickets edited in the selected dates, plus everything you're actively working on — clear the red items before you sign off{% endif %}{% if is_admin %} · <a href="/my-day/rollup?{{ request.query_string.decode() }}">team roll-up</a> · <a href="/my-day/feed?{{ request.query_string.decode() }}">activity feed</a>{% endif %}</div>
<form method="get" class="filterbar">
  <label>Project
    <select name="project" onchange="this.form.submit()">
      {% for p in filter_projects %}<option value="{{ p.key }}"{% if p.key == filter_project_selected %} selected{% endif %}>{{ p.name }}</option>{% endfor %}
    </select>
  </label>
  <label>Developer<select name="developer" {% if not is_admin %}{% if dev_options|length <= 1 %}disabled{% endif %}{% endif %} onchange="this.form.submit()">
    {% if is_admin %}<option value="">— select a developer —</option>{% endif %}
    {% for o in dev_options %}<option value="{{ o.id }}" {% if o.id == selected_dev %}selected{% endif %}>{{ o.name }}</option>{% endfor %}
  </select></label>
  <label>From<input type="date" name="start" value="{{ request.args.get('start','') }}" {% if show_all %}disabled title="Not used while showing all assigned tickets"{% endif %} onchange="this.form.submit()"></label>
  <label>To<input type="date" name="end" value="{{ request.args.get('end','') }}" {% if show_all %}disabled title="Not used while showing all assigned tickets"{% endif %} onchange="this.form.submit()"></label>
  <label style="display:flex;flex-direction:row;align-items:center;gap:6px;font-size:13px;color:var(--ink);font-weight:500;align-self:flex-end;padding-bottom:7px">
    <input type="checkbox" name="all" value="1" style="width:auto;margin:0;padding:0;box-shadow:none" {% if show_all %}checked{% endif %} onchange="this.form.submit()"> Show all assigned tickets
  </label>
  <noscript><button class="btn" type="submit">Apply</button></noscript>
</form>
{% if not selected_dev %}
<div class="sectionbox"><p class="muted">{% if is_admin %}Select a developer above to see their checklist.{% else %}Your account isn't linked to a developer, so there's nothing to show. Ask an admin to link it.{% endif %}</p></div>
{% endif %}
{% if d %}
{% set total = d.rows|length %}
{% set clean = d.rows|selectattr('fails','equalto',0)|list|length %}
{% set stale = d.rows|selectattr('stale')|list|length %}
{% set active = d.rows|selectattr('active')|list|length %}
{% if total %}
<div class="md-summary">
  <div class="md-progress"><div class="md-progress-bar" style="width:{{ (100*clean/total)|round|int }}%"></div></div>
  <div class="md-summary-text">
    <span><span class="big">{{ clean }}</span> / {{ total }} tickets up to date</span>
    <span class="md-kpi"><span class="dot" style="background:#d64545"></span>{{ total-clean }} need attention</span>
    <span class="md-kpi"><span class="dot" style="background:#b7791f"></span>{{ stale }} stale</span>
    <span class="md-kpi"><span class="dot" style="background:#1fa963"></span>{{ active }} active now</span>
  </div>
</div>
{% endif %}
<div class="controls">
  <span class="ctl-label">Filter</span>
  <button type="button" class="chipbtn active" data-filter="all">All</button>
  <button type="button" class="chipbtn" data-filter="active">⚡ Working now</button>
  {% for cid, label in check_labels %}
  {% if cid == 'past_due' %}
  <button type="button" class="chipbtn" data-filter="past_due">Past due</button>
  {% else %}
  <button type="button" class="chipbtn" data-filter="{{ cid }}">Missing: {{ label|replace('Belongs to a release','release')|replace('Due date set','due date')|replace('Status classified','status')|replace('Comment today','comment')|replace('Within aging threshold','within threshold') }}</button>
  {% endif %}
  {% endfor %}
  <button type="button" class="chipbtn" data-filter="stale">Stale</button>
</div>
<div id="mdCards">
{% for r in d.rows %}
<div class="md-card mdcard {{ 'clean' if r.fails == 0 else 'attention' }}{{ ' active-now' if r.active else '' }}" data-fail="{{ r.fail_ids|join(',') }}" data-stale="{{ 1 if r.stale else 0 }}" data-active="{{ 1 if r.active else 0 }}">
  {% if r.active %}<span class="md-ribbon"><span class="live"></span>⚡ Working now{% if r.lane %} · {{ r.lane }}{% endif %}{% if r.active_for %} · active {{ r.active_for }}{% endif %}</span>{% endif %}
  <div class="md-head">
    <div class="md-title"><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a> {{ r.issue.summary }}{% if r.last_activity_str %} <span class="muted" style="font-weight:400">· {{ r.last_activity_str }}</span>{% endif %}</div>
    <div class="md-tags">
      {% if r.stale %}<span class="pill bad" title="No status change in {{ r.stale_days }} days">⏳ stale {{ r.stale_days|round|int }}d</span>{% endif %}
      <span class="pill">{{ r.issue.type }}</span>
      <span class="pill">{{ r.issue.status }}</span>
    </div>
  </div>
  <div class="md-checks">
  {% for cid, label, state, why in r.checks %}
    <span class="check c-{{ state }}" title="{{ why }}">{{ '✓' if state=='pass' else ('✕' if state=='fail' else '–') }} {{ label }}</span>
  {% endfor %}
  </div>
</div>
{% else %}<p class="muted">Nothing on the checklist — no open tickets for this developer.</p>{% endfor %}
</div>
<p class="muted" id="mdEmpty" style="display:none">No tickets match that filter. 🎉</p>
<script>
(function(){
  var cards=[].slice.call(document.querySelectorAll('.mdcard'));
  var empty=document.getElementById('mdEmpty');
  document.querySelectorAll('.chipbtn').forEach(function(btn){
    btn.addEventListener('click',function(){
      document.querySelectorAll('.chipbtn').forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active');
      var f=btn.getAttribute('data-filter'), shown=0;
      cards.forEach(function(c){
        var fails=(c.getAttribute('data-fail')||'').split(',');
        var ok = f==='all' ? true
               : f==='stale' ? c.getAttribute('data-stale')==='1'
               : f==='active' ? c.getAttribute('data-active')==='1'
               : fails.indexOf(f)>=0;
        c.style.display = ok ? '' : 'none';
        if(ok) shown++;
      });
      if(empty) empty.style.display = shown ? 'none' : '';
    });
  });
})();
</script>
{% endif %}
"""


@v3.route("/my-day")
def my_day_screen():
    import auth
    from metrics_glossary import gloss
    user = auth.current_user()
    is_admin = bool(user and user.get("role") == "admin")
    own = (user or {}).get("developer_id") or (user or {}).get("developer")
    own_name = (user or {}).get("developer") or own
    if is_admin:
        dev_options = auth.visible_developers()
        if own and not any(o["id"] == own for o in dev_options):
            dev_options = [{"id": own, "name": own_name}] + dev_options
        selected_dev = (request.args.get("developer") or "").strip() \
            if "developer" in request.args else (own or "")
    else:
        # Employees are locked to their linked developer, regardless of the URL.
        dev_options = [{"id": own, "name": own_name}] if own else []
        selected_dev = own or ""
    start, end = _day_range_arg()
    show_all = request.args.get("all") == "1"
    _psel, scope = current_project_selection()
    d = (checklist.my_day(_issues(scope), selected_dev, start, end, dr.dev_match_exact,
                          show_all=show_all)
         if selected_dev else None)
    # The comment check's label follows the selected window ("Comment today" /
    # "Comment on Jul 05" / "Comment in range") — keep the filter chips in step.
    labels = dict(checklist.CHECK_LABELS)
    labels["comment_today"] = checklist.comment_check_label(start, end)
    return page(MYDAY_TMPL, active="/my-day", d=d, g=gloss, show_all=show_all,
                is_admin=is_admin, dev_options=dev_options, selected_dev=selected_dev,
                check_labels=[(cid, labels[cid]) for cid in checklist.CHECK_ORDER])


def _day_arg():
    """The roll-up's single day. Falls back to the end of a My Day date range so
    following the 'team roll-up' link keeps the dates you were looking at,
    instead of silently snapping back to today."""
    try:
        return dt.date.fromisoformat(request.args.get("day", ""))
    except ValueError:
        return _day_range_arg()[1]


def _day_range_arg():
    """(start, end) dates for My Day. Neither given -> today..today; only a start
    -> start..today; only an end -> that single day. Reversed input is tolerated."""
    def d(v):
        try:
            return dt.date.fromisoformat(v) if v else None
        except ValueError:
            return None
    today = dt.datetime.now(dt.timezone.utc).date()
    s, e = d(request.args.get("start")), d(request.args.get("end"))
    if not s and not e:
        return today, today
    if s and not e:
        return s, max(s, today)
    if e and not s:
        return e, e
    return (s, e) if s <= e else (e, s)


ROLLUP_TMPL = """
<h1>End-of-day roll-up</h1>
<div class="sub">% of tickets in an active or paused status with an EOD signal (comment, worklog, or any update) on {{ d.day }} · <a href="/my-day">back to My Day</a></div>
""" + FILTER_BAR.replace("{{ extra_filters|default('')|safe }}",
  """<label>Day<input type="date" name="day" value="{{ request.args.get('day','') }}"></label>""") + """
<div class="cards">
  <div class="card"><div class="n">{{ d.pct }}%</div><div class="l">In active/paused status with an EOD signal ({{ d.signaled }}/{{ d.total }})</div></div>
</div>
<p class="muted"><b>Active</b> = currently in a blue working status (investigation, development, review, or a testing lane) — someone is working on it now. Paused counts because pausing at end of day is itself the signal. Queue statuses (To Do, Ready for QA, …) are excluded.</p>
<table>
<tr><th>Developer</th><th>In active/paused status</th><th>With EOD signal</th><th>%</th></tr>
{% for r in d.rows %}
<tr><td>{{ r.developer }}</td><td>{{ r.tickets }}</td><td>{{ r.signaled }}</td>
<td><span class="pill {{ 'ok' if r.pct >= 80 else ('warn' if r.pct >= 50 else 'bad') }}">{{ r.pct }}%</span></td></tr>
{% else %}<tr><td colspan="4" class="muted">No tickets in an active or paused status.</td></tr>{% endfor %}
</table>
"""


@v3.route("/my-day/rollup")
def my_day_rollup():
    from metrics_glossary import gloss
    project, _dev, _s, _e = parse_filters()
    day = _day_arg()
    d = checklist.rollup(_issues(project), day)
    return page(ROLLUP_TMPL, active="/my-day", d=d, g=gloss)


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
    return activity.build_feed(_issues(project), developer, start, end, dr.dev_match_exact)


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
    start, end = _day_range_arg()
    d = checklist.my_day(_issues(project), developer, start, end, dr.dev_match_exact,
                         show_all=request.args.get("all") == "1")
    return jsonify({"start": d["start"].isoformat(), "end": d["end"].isoformat(),
                    "total_fails": d["total_fails"],
                    "rows": [{"key": r["issue"].key, "fails": r["fails"],
                              "checks": [{"id": c, "label": l, "state": s, "why": w}
                                         for c, l, s, w in r["checks"]]} for r in d["rows"]]})


# ---------------------------------------------------------------------------
# Screen 2 — Attention Board (FR-A1/A2)
# ---------------------------------------------------------------------------

ATTN_TMPL = """
<h1>Attention Board</h1>
<div class="sub">Work already in progress that's slipping — worst first, reason chips stack per ticket</div>
""" + FILTER_BAR.replace("{{ extra_filters|default('')|safe }}",
  """<label>Reason<select name="reason"><option value="">all</option>
  {% for k in d.kinds %}<option value="{{ k }}" {% if request.args.get('reason')==k %}selected{% endif %}>{{ k }}</option>{% endfor %}
  </select></label>""") + """
<div class="sectionbox" style="padding:10px 14px;margin-bottom:14px">
  <p class="muted" style="margin:0">This board surfaces <b>open tickets that need a nudge</b> — aging, silent, overdue, blocked, or missing a release. It covers work that's <b>in progress, paused, or bounced back from QA</b>, so the <b>not-yet-started backlog (To&nbsp;Do)</b> and completed tickets don't appear here.</p>
</div>
<p class="muted">{{ d.rows|length }} ticket(s) need attention. <a href="/api/v2/attention.csv?{{ request.query_string.decode() }}" download>Download CSV</a></p>
<table>
<tr><th>Issue</th><th>Summary</th><th>Developer</th><th>Status</th><th>Reasons</th><th></th></tr>
{% for r in d.rows %}
<tr>
 <td><a href="{{ r.issue.url }}" target="_blank">{{ r.issue.key }}</a></td>
 <td>{{ r.issue.summary }}</td><td>{{ r.issue.assignee }}</td><td>{{ r.issue.status }}</td>
 <td>{% for reason in r.reasons %}<span class="chip {{ 'bad' if reason.kind in ('silent','aging','past_due') else 'warn' }}" title="{{ reason_help.get(reason.kind, '') }}">⚠ {{ reason.tag }}</span>{% endfor %}</td>
 <td><button type="button" class="btn-ghost nudge" data-msg="Hi! Quick check on {{ r.issue.key }} ({{ r.issue.summary|replace('\"','') }}) — it's showing {{ r.reasons|map(attribute='tag')|join(', ') }}. Could you add an update, or move it to Backlog / set a new start date if it's parked? Thanks! {{ r.issue.url }}">Copy nudge</button></td>
</tr>
{% else %}<tr><td colspan="6" class="muted">Nothing needs attention. 🎉</td></tr>{% endfor %}
</table>
<script>
document.addEventListener('click',function(ev){
  var b=ev.target.closest('.nudge'); if(!b)return;
  navigator.clipboard.writeText(b.getAttribute('data-msg')).then(function(){
    var t=b.textContent; b.textContent='Copied ✓';
    setTimeout(function(){b.textContent=t;},1500);
  });
});
</script>
"""


def _attention_board():
    project, developer, start, end = parse_filters()
    reason = (request.args.get("reason") or "").strip() or None
    return attention.board(_issues_in_range(project, start, end), developer, reason, dr.dev_match_exact)


# Attention reason kind -> glossary term, so every chip explains itself on hover.
_REASON_HELP_TERMS = {"silent": "silent", "aging": "aging", "past_due": "past_due",
                      "no_release": "no_release", "dates": "missing_dates",
                      "blocked": "blocked", "blocked_hint": "blocked"}


def _reason_help() -> dict:
    from metrics_glossary import GLOSSARY
    return {kind: GLOSSARY.get(term, "") for kind, term in _REASON_HELP_TERMS.items()}


@v3.route("/attention")
def attention_screen():
    return page(ATTN_TMPL, active="/attention", d=_attention_board(),
                reason_help=_reason_help())


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


@v3.route("/release")
def release_screen():
    """Release Readiness as a top-level screen (replaces Planning in the nav)."""
    import reports_web as rw
    try:
        win = int(request.args.get("win", 14))
    except (TypeError, ValueError):
        win = 14
    ctx = rw.release_context(request.args.get("version"), window_days=win)
    return page(rw.REL, active="/release", fmt=rw.fmt, **ctx)


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
    issues = _issues_in_range(project, start, end)  # edited-in-range ticket filter
    if not start and not end:
        start = A.now_utc() - dt.timedelta(days=14)  # default window for the feeds
    return (qh.handoff_feed(issues, developer, start, end, dr.dev_match_exact),
            qh.returned_feed(issues, developer, start, end, dr.dev_match_exact),
            qh.return_rates(issues, start, end, developer, dr.dev_match_exact))


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
_EVENT_COLOR = {"status": "#0065ff", "assignee": "#00b8d9", "comment": "#6b778c",
                "worklog": "#6554c0", "duedate": "#ffab00", "startdate": "#ffab00",
                "flag": "#de350b", "sprint": "#36b37e"}


def _ticket_timeline_data(key, start=None, end=None):
    """One ticket's full forensic timeline: events (status/assignee/comments/
    worklogs/field changes) with inactivity-gap markers, plus a lifetime
    stage-time ribbon. Shared by the standalone Investigator page and the
    Release timeline's per-ticket history popup, so they can never disagree.
    Returns (issue, timeline, ribbon) — issue is None if the key isn't found.
    """
    key = (key or "").strip().upper()
    if not key:
        return None, None, None
    issue = next((i for i in _issues(None) if i.key.upper() == key), None)
    if issue is None:
        try:
            raw = jc.fetch_single_issue(key)  # live, uncached, full history
        except Exception:
            raw = None
        if raw:
            issue = dr.load_dev_issues([raw], jc.detect_custom_fields())[0]
    if issue is None:
        return None, None, None
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
                         "color": _EVENT_COLOR.get(e.kind, "#8993a4"), "gap": None})
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
    return issue, timeline, ribbon


@v3.route("/investigate")
def investigate_screen():
    key = (request.args.get("key") or "").strip().upper()
    if not key:
        return page(INVEST_TMPL, active="/investigate", issue=None, err=None)
    _p, _d, start, end = parse_filters()
    issue, timeline, ribbon = _ticket_timeline_data(key, start, end)
    if issue is None:
        return page(INVEST_TMPL, active="/investigate", issue=None,
                    err=f"Issue {key} not found (or Jira unreachable). Check the key.")
    return page(INVEST_TMPL, active="/investigate", issue=issue, err=None,
                timeline=timeline, ribbon=ribbon)


# ---------------------------------------------------------------------------
# Ticket history fragment — a compact, self-styled timeline used by the
# Release timeline's "History" popup (and reusable anywhere else that wants
# a lightweight per-ticket history view without the full Investigator chrome).
# ---------------------------------------------------------------------------

HISTORY_FRAGMENT_TMPL = """
<style>
 .th-frag{font-family:inherit}
 .th-head{display:flex;flex-direction:column;gap:6px;padding-bottom:14px;margin-bottom:14px;border-bottom:1px solid #eef1ef}
 .th-top{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
 .th-key a{font-size:15px;font-weight:800;color:#17864e;text-decoration:none}
 .th-key a:hover{text-decoration:underline}
 .th-badges{display:flex;gap:6px}
 .th-badge{font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;background:#eef1ef;color:#3a453e}
 .th-badge.status{background:#e6f0ff;color:#0052cc}
 .th-summary{font-size:14px;color:#1c2620;font-weight:600;line-height:1.4}
 .th-meta{display:flex;gap:14px;font-size:12.5px;color:#6b756e}
 .th-ribbon{display:flex;height:20px;border-radius:6px;overflow:hidden;margin-top:2px}
 .th-ribbon-seg{display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff;overflow:hidden;white-space:nowrap;text-shadow:0 1px 1px rgba(0,0,0,.3)}
 .th-ribbon-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:#6b756e;margin-top:6px}
 .th-ribbon-legend span{display:inline-flex;align-items:center;gap:5px}
 .th-dot{width:8px;height:8px;border-radius:50%;flex:none;display:inline-block}
 .th-timeline{position:relative;margin-top:18px;padding-left:26px}
 .th-timeline::before{content:"";position:absolute;left:9px;top:6px;bottom:6px;width:2px;background:#e4e7e5}
 .th-node{position:relative;padding:4px 0 16px;font-size:12.5px;font-weight:700;color:#3a453e}
 .th-node-dot{position:absolute;left:-26px;top:5px;width:14px;height:14px;border-radius:50%;background:#98a099;border:3px solid #fff;box-shadow:0 0 0 1.5px #98a099}
 .th-node-dot.current{background:#1fa963;box-shadow:0 0 0 1.5px #1fa963}
 .th-gap{position:relative;margin:6px 0 14px;padding:7px 12px;background:#fff8ea;border:1px dashed #e8b84b;border-radius:8px;display:inline-block;color:#8a5a14;font-size:12px;font-weight:600}
 .th-event{position:relative;padding-bottom:16px}
 .th-event-dot{position:absolute;left:-26px;top:1px;width:14px;height:14px;border-radius:50%;font-size:8px;display:flex;align-items:center;justify-content:center;color:#fff;border:2px solid #fff;box-shadow:0 0 0 1px #e4e7e5}
 .th-event-top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:12.5px}
 .th-event-top b{color:#1c2620;font-weight:700}
 .th-event-kind{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#98a099}
 .th-event-ts{margin-left:auto;color:#98a099;font-size:11.5px;white-space:nowrap}
 .th-event-detail{margin-top:3px;font-size:12.5px;color:#3a453e;line-height:1.4}
 .th-arrow{color:#98a099;margin:0 2px}
 .th-empty{color:#6b756e;font-size:12.5px;padding:12px 0}
 .th-error{color:#a82f2f;font-size:13px;padding:20px 0;text-align:center}
</style>
<div class="th-frag">
  <div class="th-head">
    <div class="th-top">
      <div class="th-key"><a href="{{ issue.url }}" target="_blank">{{ issue.key }}</a></div>
      <div class="th-badges"><span class="th-badge">{{ issue.type }}</span><span class="th-badge status">{{ issue.status }}</span></div>
    </div>
    <div class="th-summary">{{ issue.summary }}</div>
    <div class="th-meta">
      <span>👤 {{ issue.assignee }}</span>
      {% if issue.created %}<span>Created {{ issue.created.strftime('%b %-d, %Y') }}</span>{% endif %}
    </div>
    {% if ribbon %}
    <div class="th-ribbon">
      {% for seg in ribbon %}<div class="th-ribbon-seg" style="width:{{ seg.pct }}%;background:{{ seg.color }}" title="{{ seg.label }}: {{ seg.days }}d ({{ seg.pct }}%)">{% if seg.pct >= 12 %}{{ seg.label }}{% endif %}</div>{% endfor %}
    </div>
    <div class="th-ribbon-legend">{% for seg in ribbon %}<span><span class="th-dot" style="background:{{ seg.color }}"></span>{{ seg.label }} · {{ seg.days }}d</span>{% endfor %}</div>
    {% endif %}
  </div>
  <div class="th-timeline">
    <div class="th-node"><span class="th-node-dot"></span>Created{% if issue.created %} · {{ issue.created.strftime('%b %-d, %Y · %H:%M') }}{% endif %}</div>
    {% for item in timeline %}
      {% if item.gap %}
      <div class="th-gap">⏸ {{ item.gap }} days of inactivity</div>
      {% else %}
      <div class="th-event">
        <span class="th-event-dot" style="background:{{ item.color }}">{{ item.icon }}</span>
        <div class="th-event-top"><b>{{ item.e.actor }}</b><span class="th-event-kind">{{ item.e.kind }}</span><span class="th-event-ts">{{ item.e.ts.strftime('%b %-d, %Y · %H:%M') }}</span></div>
        {% if item.e.kind in ('status','assignee','duedate','startdate','flag','sprint') %}
          <div class="th-event-detail">{{ item.e.frm or '—' }} <span class="th-arrow">→</span> {{ item.e.to or '—' }}</div>
        {% elif item.e.kind == 'worklog' %}
          <div class="th-event-detail"><span class="th-badge">{{ (item.e.seconds/3600)|round(1) }}h logged</span> {{ item.e.detail[:160] }}</div>
        {% else %}
          <div class="th-event-detail">{{ item.e.detail[:400] }}</div>
        {% endif %}
      </div>
      {% endif %}
    {% else %}
      <div class="th-empty">No recorded activity yet.</div>
    {% endfor %}
    <div class="th-node"><span class="th-node-dot current"></span>Now · {{ issue.status }}</div>
  </div>
</div>
"""

HISTORY_FRAGMENT_ERR = """<div class="th-error">Issue {{ key }} not found (or Jira unreachable).</div>"""


@v3.route("/api/v2/ticket-history/<key>")
def ticket_history_fragment(key):
    issue, timeline, ribbon = _ticket_timeline_data(key)
    if issue is None:
        return render_template_string(HISTORY_FRAGMENT_ERR, key=(key or "").strip().upper()), 404
    return render_template_string(HISTORY_FRAGMENT_TMPL, issue=issue, timeline=timeline, ribbon=ribbon)


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

<h2>{{ g('multiple_active','Multiple active tickets')|safe }} <span class="muted">(rule: one active ticket per lane — dev / QA / staging / production)</span></h2>
<table>
<tr><th>Developer</th><th>Lane</th><th>Active count</th><th>Tickets</th></tr>
{% for v in violations %}
<tr><td>{{ v.developer }}</td><td><span class="pill">{{ v.lane_label }}</span></td><td><span class="pill bad">{{ v.count }}</span></td>
<td>{% for t in v.tickets %}<a href="{{ t.url }}" target="_blank">{{ t.key }}</a> <span class="muted">({{ t.status }})</span>{{ ', ' if not loop.last }}{% endfor %}</td></tr>
{% else %}<tr><td colspan="4" class="muted">No one holds more than one active ticket in any lane. 🎉</td></tr>{% endfor %}
</table>

<h2>{{ g('bottleneck','Team bottleneck')|safe }} <span class="muted">(median days per status)</span></h2>
<table>
<tr><th>Status</th><th>Bucket</th><th>Median days</th><th>p85 days</th><th>Tickets</th><th></th></tr>
{% for b in bneck[:12] %}
<tr><td>{{ b.status }}</td><td><span class="pill">{{ b.bucket }}</span></td>
<td>{{ b.median_days }}</td><td>{{ b.p85_days }}</td><td>{{ b.n }}</td>
<td><div style="background:#e7e8e7;border-radius:4px;height:10px;width:100%;max-width:220px"><div style="background:#1fa963;border-radius:4px;height:10px;width:{{ (100 * b.median_days / bneck[0].median_days)|round|int if bneck else 0 }}%"></div></div></td></tr>
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
    issues = _issues_in_range(project, start, end)  # edited-in-range ticket filter
    win_start = start if (start or end) else A.now_utc() - dt.timedelta(days=30)
    rows = fq.cycle_rows(issues, developer, win_start, end, dr.dev_match_exact)
    focus = dr.developer_focus(issues, developer=developer, start=win_start, end=end)["rows"]
    return (rows, fq.cycle_stats(rows), fq.bottleneck(issues),
            fq.multiple_active(issues, developer, dr.dev_match_exact), focus)


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
# Time-Spent Dashboards — a landing dashboard (per-developer summary + busiest
# tickets, each independently rangeable) and a per-developer dashboard drilling
# into one person's tickets, raw vs. de-duplicated active time, and inflation
# from overlapping tickets. Admin-only for now (see app.py's
# _EMPLOYEE_PREFIXES).
# ---------------------------------------------------------------------------

def _dur(seconds):
    """'3d 4h' / '18h' / '25m' — the app's existing duration wording, in days
    first so a number can never be misread as hours-of-effort.

    Rounds to whole hours BEFORE splitting off days, so a shade under 7 days
    reads "7d" rather than "6d 24h". (app.hdur, which this mirrors, subtracts
    days first and can leave a 24-hour remainder; worth fixing there too, but
    that helper backs the legacy report pages.)"""
    if not seconds:
        return "0h"
    if seconds < 3600:
        return f"{max(round(seconds / 60), 1)}m"
    hours = round(seconds / 3600)
    if hours < 24:
        return f"{hours}h"
    d, h = divmod(hours, 24)
    return f"{d}d {h}h" if h else f"{d}d"


# A real week is a handful of substantial tickets plus a long tail of brief
# touches — a dev can easily end a week having been on 14. Listing all of them
# at equal weight buries the few that mattered, so the tail is folded away by
# default and summarised instead.
#
# Where to cut is decided by the data, not by a fixed row count: take tickets
# in descending order until they account for _HEAD_SHARE of the person's time.
# A concentrated week collapses to two or three rows; an evenly fragmented one
# shows more, because in that case the spread IS the finding. The bounds stop
# either extreme from being useless.
_HEAD_SHARE = 85
_HEAD_MIN, _HEAD_MAX = 3, 8


def _split_head_tail(tickets, total):
    """(head, tail) — the tickets carrying most of the time, and the rest."""
    if len(tickets) <= _HEAD_MIN:
        return tickets, []
    run, cut = 0, len(tickets)
    for n, t in enumerate(tickets, start=1):
        run += t["active_seconds"]
        if total and 100 * run / total >= _HEAD_SHARE:
            cut = n
            break
    cut = max(_HEAD_MIN, min(cut, _HEAD_MAX))
    # Leaving a tail of one just to hide it costs a click and saves no space.
    if len(tickets) - cut == 1:
        cut = len(tickets)
    return tickets[:cut], tickets[cut:]


def _group_by_dev(rows, window_seconds=None):
    """Group active_time() rows by developer — used with rows already scoped
    to one person, so it produces a single group: that developer's ticket
    list for the dashboard, in the shape the per-ticket section renders.

    Two deliberate choices, both driven by the 14-tickets-in-a-week case:

    Bars share ONE scale across the whole page, so a given length always means
    the same duration wherever it appears — an earlier version sized each
    developer's track against the busiest developer while sizing segments
    against that developer's own total, which made equal-looking bars mean
    different amounts, exactly backwards for a chart built for comparison.

    That shared scale only survives because the long tail is collapsed: with
    one 6d ticket and a dozen 20-minute touches, every tail bar would round to
    an invisible sliver and the chart would say nothing about 13 of 14 rows.
    Showing the top few keeps the visible rows within comparable magnitudes,
    and the tail is reported as a count and a total rather than as a row of
    hairlines."""
    groups = {}
    for r in rows:
        g = groups.setdefault(r["developer"], {
            "developer": r["developer"], "seconds": 0, "logged": 0, "tickets": []})
        g["seconds"] += r["active_seconds"]
        g["logged"] += r["logged_seconds"]
        g["tickets"].append(r)
    out = list(groups.values())
    out.sort(key=lambda g: -g["seconds"])
    longest = max((r["active_seconds"] for r in rows), default=0) or 1
    for g in out:
        g["label"] = _dur(g["seconds"])
        g["logged_label"] = _dur(g["logged"])
        g["count"] = len(g["tickets"])
        g["tickets"].sort(key=lambda t: -t["active_seconds"])
        # Concurrency is the reason a person's total can exceed the window:
        # two tickets active at once each accrue the same wall-clock hours.
        g["over_window"] = bool(window_seconds and g["seconds"] > window_seconds * 1.02)
        for t in g["tickets"]:
            t["bar_pct"] = round(100 * t["active_seconds"] / longest, 1)
            # Share of this person's own time — what the split bar encodes.
            t["share_pct"] = round(100 * t["active_seconds"] / (g["seconds"] or 1), 2)
            t["label"] = _dur(t["active_seconds"])
            t["logged_label"] = _dur(t["logged_seconds"])
            t["color"] = _status_color(t["issue"].status)
            # What stage(s) this ticket was in while being worked, as segments
            # of its OWN bar (not a separate caption line): usually one
            # segment — the current status — but a ticket the dev carried
            # across a handoff shows its real composition, e.g. mostly
            # Development with a sliver of QA Testing.
            secs = t["statuses"] or {t["issue"].status: t["active_seconds"]}
            total = sum(secs.values()) or 1
            t["segments"] = [
                {"status": s, "pct": round(100 * v / total, 2),
                 "color": _status_color(s), "label": _dur(v)}
                for s, v in sorted(secs.items(), key=lambda kv: -kv[1])]
        g["top"], g["rest"] = _split_head_tail(g["tickets"], g["seconds"])
        g["rest_label"] = _dur(sum(t["active_seconds"] for t in g["rest"]))
        # How concentrated the week was: one ticket's share of it. With 14
        # tickets this is the number that says "scattered" or "heads-down".
        g["top_share"] = g["tickets"][0]["share_pct"] if g["tickets"] else 0
        seen = {}
        for t in g["tickets"]:
            for seg in t["segments"]:
                seen.setdefault(seg["status"], seg["color"])
        g["statuses_seen"] = sorted(seen.items())
    return out


_RANGE_DAYS = {"7d": 7, "14d": 14, "30d": 30}
_DEFAULT_RANGE = "7d"


def _resolve_range(range_key, custom_start=None, custom_end=None):
    """(start, end, resolved_range_key) for a preset (7d/14d/30d) or custom.

    "custom" is a MODE, not just a pair of dates: clicking the Custom pill
    the first time carries no start/end yet, and still has to come back as
    resolved_range_key == "custom" — or the template's {% if range_key ==
    'custom' %} never fires, the date inputs never appear, and the click
    silently lands back on the 7-day default with no way to tell why. Fixed
    dates only refine the window; the fallback (last 7 days) keeps the cards
    and ticket list showing something sane in the meantime."""
    tomorrow = A.now_utc().date() + dt.timedelta(days=1)
    if range_key == "custom":
        end_date = (dt.date.fromisoformat(custom_end) if custom_end
                   else tomorrow - dt.timedelta(days=1))
        start_date = (dt.date.fromisoformat(custom_start) if custom_start
                     else end_date - dt.timedelta(days=_RANGE_DAYS[_DEFAULT_RANGE] - 1))
        start = dt.datetime.combine(start_date, dt.time.min, dt.timezone.utc)
        end = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, dt.timezone.utc)
        return start, end, "custom"
    key = range_key if range_key in _RANGE_DAYS else _DEFAULT_RANGE
    end = dt.datetime.combine(tomorrow, dt.time.min, dt.timezone.utc)
    return end - dt.timedelta(days=_RANGE_DAYS[key]), end, key


def _range_label(start, end, range_key):
    """Plain wording for a resolved window ('in the past 7 days' / 'Aug 1 →
    Aug 10'). `end` is exclusive, so the last day named is the day before it."""
    if range_key in _RANGE_DAYS:
        return f"in the past {_RANGE_DAYS[range_key]} days"
    last = end - dt.timedelta(days=1)
    if last.date() == start.date():
        return f"on {start.strftime('%b %-d')}"
    return f"{start.strftime('%b %-d')} → {last.strftime('%b %-d')}"


def _dash_link(dev_id, project_sel, range_key, custom_start="", custom_end=""):
    """Query string for a Time-Spent Dashboards link that keeps the current
    developer and project while switching the range — so clicking a preset
    pill doesn't silently reset the project filter."""
    p = {"dev": dev_id, "project": project_sel, "range": range_key,
        "start": custom_start, "end": custom_end}
    return "&".join(f"{k}={v}" for k, v in p.items() if v)


TICKET_HISTORY_MODAL = """
<style>
 .th-modal{display:none;position:fixed;inset:0;z-index:200;background:rgba(23,30,26,.45);align-items:center;justify-content:center;padding:24px}
 .th-modal.open{display:flex}
 .th-modal-card{background:#fff;border-radius:14px;max-width:720px;width:100%;max-height:85vh;overflow-y:auto;padding:26px 28px;box-shadow:0 20px 60px rgba(9,30,20,.28);position:relative}
 .th-modal-close{position:absolute;top:14px;right:16px;background:none;border:none;font-size:22px;line-height:1;color:#98a099;cursor:pointer;padding:4px}
 .th-modal-close:hover{color:#3a453e}
 .th-modal-loading{color:#6b756e;font-size:13px;padding:40px 0;text-align:center}
 .tskey{font-weight:700}
</style>
<div id="thModal" class="th-modal" onclick="if(event.target===this)thClose()">
  <div class="th-modal-card">
    <button type="button" class="th-modal-close" onclick="thClose()" aria-label="Close">&times;</button>
    <div id="thModalBody" class="th-modal-body"><div class="th-modal-loading">Loading history…</div></div>
  </div>
</div>
<script>
function thOpen(key){
  var m=document.getElementById('thModal'), b=document.getElementById('thModalBody');
  b.innerHTML='<div class="th-modal-loading">Loading history…</div>';
  m.classList.add('open');
  document.body.style.overflow='hidden';
  fetch('/api/v2/ticket-history/'+encodeURIComponent(key))
    .then(function(r){return r.text();})
    .then(function(t){ b.innerHTML = t; })
    .catch(function(){ b.innerHTML='<div class="th-error">Could not load history for '+key+'.</div>'; });
}
function thClose(){
  document.getElementById('thModal').classList.remove('open');
  document.body.style.overflow='';
}
document.addEventListener('keydown', function(e){ if(e.key==='Escape') thClose(); });
</script>
"""


TIME_SPENT_TMPL = """
<h1>Time-Spent Dashboards</h1>
<div class="sub">Where the team's active time went, and a dashboard per developer for the detail.</div>
""" + TICKET_HISTORY_MODAL + """
<style>
 .ts-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
 @media (max-width:760px){.ts-grid{grid-template-columns:1fr}}
 .ts-devrow{display:grid;grid-template-columns:minmax(0,1fr) 130px 56px;gap:10px;align-items:center;padding:5px 0}
 .ts-devrow:hover{background:#fafbfa}
 .ts-name{font-size:13px;font-weight:600;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .ts-track{background:#eef0ee;border-radius:5px;height:14px;overflow:hidden}
 .ts-fill{height:100%;min-width:3px;border-radius:5px;background:var(--green)}
 .ts-tickrow{display:grid;grid-template-columns:88px minmax(0,1fr) 90px 56px;gap:10px;align-items:center;padding:5px 0;cursor:pointer}
 .ts-tickrow:hover{background:#fafbfa}
 .ts-tickrow:focus-visible{outline:2px solid var(--green);outline-offset:2px}
 .ts-sum{font-size:12.5px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .ts-devgrid{display:grid;grid-template-columns:repeat(auto-fill, minmax(180px, 1fr));gap:12px}
 .ts-devbtn{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:20px 16px;font-size:16px;font-weight:700;color:var(--ink2);cursor:pointer;text-decoration:none;display:flex;align-items:center;justify-content:center;text-align:center;min-height:64px}
 .ts-devbtn:hover{border-color:var(--green);color:var(--green-d);background:var(--green-t);text-decoration:none}
</style>

<div class="ts-grid">
  <div class="sectionbox">
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      <h2 style="margin:0">Time spent, by developer</h2>
      <span>
        {% for rk, label in [('7d','7d'),('14d','14d'),('30d','30d')] %}<a class="pill {{ 'ok' if dev_range == rk }}" href="?dev_range={{ rk }}&ticket_range={{ ticket_range }}">{{ label }}</a>{% endfor %}
      </span>
    </div>
    <div class="muted" style="margin-bottom:8px">Actual working time {{ dev_range_label }}, counting at most one ticket per instant so overlapping tickets aren't double-counted.</div>
    {% for d in dev_rows %}
    <a class="ts-devrow" href="/active-time?dev={{ d.developer_id }}" style="text-decoration:none;color:inherit">
      <div class="ts-name">{{ d.developer }}</div>
      <div class="ts-track"><div class="ts-fill" style="width:{{ d.pct }}%"></div></div>
      <div style="font-size:12.5px;font-weight:700;text-align:right">{{ d.label }}</div>
    </a>
    {% else %}<div class="muted">Nobody had active time {{ dev_range_label }}.</div>{% endfor %}
  </div>

  <div class="sectionbox">
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      <h2 style="margin:0">Busiest tickets</h2>
      <span>
        {% for rk, label in [('7d','7d'),('14d','14d'),('30d','30d')] %}<a class="pill {{ 'ok' if ticket_range == rk }}" href="?dev_range={{ dev_range }}&ticket_range={{ rk }}">{{ label }}</a>{% endfor %}
      </span>
    </div>
    <div class="muted" style="margin-bottom:8px">Most time in a working status {{ ticket_range_label }}, summed across everyone who worked it.</div>
    {% for t in top_tickets %}
    <div class="ts-tickrow" onclick="thOpen('{{ t.issue.key }}')" role="button" tabindex="0" title="View work history" onkeydown="if(event.key==='Enter')thOpen('{{ t.issue.key }}')">
      <a href="{{ t.issue.url }}" target="_blank" class="tskey" onclick="event.stopPropagation()">{{ t.issue.key }}</a>
      <div class="ts-sum" title="{{ t.issue.summary }}">{{ t.issue.summary }}</div>
      <div style="font-size:12.5px">{% if t.people > 1 %}<span class="pill" title="{{ t.people }} people worked this ticket">{{ t.people }} people</span>{% endif %}</div>
      <div style="font-size:12.5px;font-weight:700;text-align:right">{{ t.label }}</div>
    </div>
    {% else %}<div class="muted">No tickets had active time {{ ticket_range_label }}.</div>{% endfor %}
  </div>
</div>

<h2>Select a Developer for Full Dashboard</h2>
<div class="sectionbox">
  <div class="ts-devgrid">
  {% for d in all_devs %}<a class="ts-devbtn" href="/active-time?dev={{ d.id }}">{{ d.name }}</a>{% else %}<span class="muted">No developers found.</span>{% endfor %}
  </div>
</div>
"""


DEV_NOT_FOUND_TMPL = """
<h1>Time-Spent Dashboards</h1>
<div class="sub">No developer matches that link. It may be stale, or the account was hidden in Settings.</div>
<div class="sectionbox"><a href="/active-time">← Back to Time-Spent Dashboards</a></div>
"""


# An employee whose account isn't linked to a selectable Jira developer has no
# time of their own to show, and must not fall through to the team roster.
DEV_UNLINKED_TMPL = """
<h1>Time Spent</h1>
<div class="sub">We can't tell which Jira developer you are.</div>
<div class="sectionbox">
  <p class="muted">Time spent is drawn from the Jira account that did the work, so there's
  nothing to show until your login is linked to a Jira developer that still appears in
  the team's project. Ask an admin to check the link on your account.</p>
  <a href="/my-day">← Back to My Day</a>
</div>
"""


DEV_DASHBOARD_TMPL = """
<h1>{{ dev_name }} <span style="font-weight:500;font-size:15px;color:var(--muted)">· Time Spent</span></h1>
<div class="sub">{{ window_label }}</div>
""" + TICKET_HISTORY_MODAL + """
<div class="filterbar">
  <form method="get" style="display:contents">
    <input type="hidden" name="dev" value="{{ dev_id }}">
    <input type="hidden" name="range" value="{{ range_key }}">
    <input type="hidden" name="start" value="{{ custom_start }}">
    <input type="hidden" name="end" value="{{ custom_end }}">""" + PROJECT_SELECT + """
    <button class="btn" type="submit">Apply</button>
  </form>
  <div style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap">
    <a class="pill {{ 'ok' if range_key == '7d' }}" href="?{{ link('7d') }}">Past 7 days</a>
    <a class="pill {{ 'ok' if range_key == '14d' }}" href="?{{ link('14d') }}">Past 14 days</a>
    <a class="pill {{ 'ok' if range_key == '30d' }}" href="?{{ link('30d') }}">Past 30 days</a>
    <a class="pill {{ 'ok' if range_key == 'custom' }}" href="?{{ link('custom') }}">Custom</a>
  </div>
  {% if range_key == 'custom' %}
  <form method="get" style="display:contents">
    <input type="hidden" name="dev" value="{{ dev_id }}">
    <input type="hidden" name="project" value="{{ filter_project_selected }}">
    <input type="hidden" name="range" value="custom">
    <label>From<input type="date" name="start" value="{{ custom_start }}"></label>
    <label>To<input type="date" name="end" value="{{ custom_end }}"></label>
    <button class="btn" type="submit">Apply range</button>
  </form>
  {% endif %}
</div>

<div class="cards">
  <div class="card"><div class="n">{{ dur(totals.raw_seconds) }}</div><div class="l">Total time worked <span class="muted">(sum across tickets)</span></div></div>
  <div class="card"><div class="n">{{ dur(totals.inflated_seconds) }}</div><div class="l">Inflated by overlapping tickets</div></div>
  <div class="card"><div class="n">{{ dur(totals.dedup_seconds) }}</div><div class="l">Actually spent working <span class="muted">(max 1 ticket at once)</span></div></div>
</div>
<div class="muted" style="margin-top:-14px;margin-bottom:20px">"Total" adds up every ticket's elapsed time in a working status; when two tickets were active at once, that stretch counts toward both, which is where the inflation comes from. "Actually spent working" counts it once.</div>

<style>
 .at-row{display:grid;grid-template-columns:100px minmax(0,1fr) 180px 116px;gap:12px;align-items:center;padding:7px 4px;border-radius:8px;cursor:pointer}
 .at-row:hover{background:#fafbfa}
 .at-row:focus-visible{outline:2px solid var(--green);outline-offset:-2px}
 .at-sum{font-size:12.5px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .at-track{background:#eef0ee;border-radius:5px;height:18px;overflow:hidden;display:flex}
 .at-track .at-fill{height:100%;display:flex;min-width:4px;border-radius:5px;overflow:hidden}
 .at-track .at-fill > div{height:100%}
 .at-track .at-fill > div + div{border-left:1px solid #fff}
 .at-dur{font-size:12.5px;font-weight:700;text-align:right;white-space:nowrap}
 .at-split{display:flex;height:12px;border-radius:4px;overflow:hidden;background:#eef0ee;min-width:190px;flex:1;max-width:340px}
 .at-split div{border-right:1px solid #fff}
 .at-split div:last-child{border-right:none}
 .at-more{cursor:pointer;font-size:12.5px;color:var(--green-d);font-weight:600;padding:8px 4px 2px}
 .at-more:hover{text-decoration:underline}
 details[open] .at-more{margin-bottom:2px}
 .at-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11.5px;color:var(--muted);margin:2px 4px 12px}
 .at-legend span{display:inline-flex;align-items:center;gap:5px}
 .at-legend i{width:9px;height:9px;border-radius:2px;flex:none}
</style>
<h2>Tickets worked <span class="muted">(one row per ticket, longest stretch shown at full width; click a row for its full history, the key opens it in Jira)</span></h2>
<div class="sectionbox">
{% if group %}
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <span class="muted"><b style="color:var(--ink2)">{{ group.count }}</b> {{ 'ticket' if group.count == 1 else 'tickets' }} · {{ group.label }} total{% if group.logged %} · {{ group.logged_label }} logged{% endif %}</span>
    {% if group.count > 1 %}
    <div class="at-split" title="How this developer's time split across their {{ group.count }} tickets">
      {% for t in group.tickets %}<div title="{{ t.issue.key }}: {{ t.label }} ({{ t.share_pct|round|int }}%)" style="width:{{ t.share_pct }}%;background:{{ t.color }}"></div>{% endfor %}
    </div>
    <span class="muted">{{ group.top_share|round|int }}% on {{ group.tickets[0].issue.key }}</span>
    {% endif %}
  </div>
  {% if group.statuses_seen|length > 1 %}
  <div class="at-legend">{% for s, c in group.statuses_seen %}<span><i style="background:{{ c }}"></i>{{ s }}</span>{% endfor %}</div>
  {% endif %}
  {% for t in group.top %}
  <div class="at-row" onclick="thOpen('{{ t.issue.key }}')" role="button" tabindex="0" title="View work history" onkeydown="if(event.key==='Enter')thOpen('{{ t.issue.key }}')">
    <a href="{{ t.issue.url }}" target="_blank" class="tskey" onclick="event.stopPropagation()">{{ t.issue.key }}</a>
    <div class="at-sum" title="{{ t.issue.summary }}">{{ t.issue.summary }}</div>
    <div class="at-track">
      <div class="at-fill" style="width:{{ t.bar_pct }}%">
        {% for seg in t.segments %}<div title="{{ seg.status }}: {{ seg.label }}" style="width:{{ seg.pct }}%;background:{{ seg.color }}"></div>{% endfor %}
      </div>
    </div>
    <div class="at-dur">{{ t.label }}{% if t.logged_seconds %} <span class="muted" style="font-weight:500">· {{ t.logged_label }}</span>{% endif %}</div>
  </div>
  {% endfor %}
  {% if group.rest %}
  <details>
    <summary class="at-more">{{ group.rest|length }} more {{ 'ticket' if group.rest|length == 1 else 'tickets' }} · {{ group.rest_label }} between them</summary>
    {% for t in group.rest %}
    <div class="at-row" onclick="thOpen('{{ t.issue.key }}')" role="button" tabindex="0" title="View work history" onkeydown="if(event.key==='Enter')thOpen('{{ t.issue.key }}')">
      <a href="{{ t.issue.url }}" target="_blank" class="tskey" onclick="event.stopPropagation()">{{ t.issue.key }}</a>
      <div class="at-sum" title="{{ t.issue.summary }}">{{ t.issue.summary }}</div>
      <div class="at-track">
        <div class="at-fill" style="width:{{ t.bar_pct }}%">
          {% for seg in t.segments %}<div title="{{ seg.status }}: {{ seg.label }}" style="width:{{ seg.pct }}%;background:{{ seg.color }}"></div>{% endfor %}
        </div>
      </div>
      <div class="at-dur">{{ t.label }}{% if t.logged_seconds %} <span class="muted" style="font-weight:500">· {{ t.logged_label }}</span>{% endif %}</div>
    </div>
    {% endfor %}
  </details>
  {% endif %}
{% else %}<div class="muted">No tickets in a working status {{ window_label }}.</div>{% endif %}
</div>
{% if not own_only %}<div class="muted"><a href="/active-time">← Back to Time-Spent Dashboards</a></div>{% endif %}
"""


def _ids_by_name():
    """Display name -> accountId for everyone in the dataset.

    auth.all_developers() only knows CURRENT assignees. A past owner of a
    ticket they've since handed off may never be a current assignee anywhere
    in the dataset — but the changelog's status-change entries carry a real
    accountId for whoever performed them, so scanning those too resolves
    someone who's only ever visible as a past owner. Without this, a developer
    filter (or a dashboard link) built from a name alone could never find
    them: dev_match_exact matches accountId first, and there'd be none."""
    import auth
    ids = {d["name"]: d["id"] for d in auth.all_developers()}
    for i in _issues(None):
        for _ts, author, author_id, _f, _t in i.status_events:
            if author_id and author not in ids:
                ids[author] = author_id
    return ids


def _developer_directory():
    """{accountId -> display name} for exactly the developers who show up in
    the normal Project/Developer dropdown everywhere else in the app —
    auth.visible_developers(), current assignees minus anyone hidden in
    Settings. This is deliberately the SAME set as the dropdown, not a
    broader one: someone who only ever appears as a past owner of a ticket
    they've since handed off (real, ownership-attributed time — see
    dev_time_totals) still doesn't get a dashboard entry here unless they're
    also a current assignee somewhere, exactly like the dropdown."""
    import auth
    return {d["id"]: d["name"] for d in auth.visible_developers()}


def _status_color(status):
    """Reuse the app's stage palette so a status keeps the same color it has on
    Flow and the Release pipeline."""
    return legacy.STAGE_COLORS.get(legacy.stage_of(status), "#8993a4")


def _landing_data():
    """Data for the Time-Spent Dashboards landing page: a dev-summary widget
    and a top-tickets widget, each on its own independent range."""
    import flow_quality as fq
    _psel, scope = current_project_selection()
    issues = _issues(scope)
    ids = _ids_by_name()
    directory = _developer_directory()
    visible_names = set(directory.values())

    dev_range = request.args.get("dev_range") or _DEFAULT_RANGE
    dev_start, dev_end, dev_range = _resolve_range(dev_range)
    totals = fq.dev_time_totals(issues, start=dev_start, end=dev_end, ids_by_name=ids)
    # Only developers who'd show up in the normal dropdown get a row here —
    # someone visible only as a past owner (real ownership time, but not the
    # dropdown's notion of "a developer") doesn't get one of their own.
    dev_rows = sorted((d for d in totals.values() if d["developer"] in visible_names),
                      key=lambda d: -d["dedup_seconds"])
    busiest = max((d["dedup_seconds"] for d in dev_rows), default=0) or 1
    for d in dev_rows:
        d["label"] = _dur(d["dedup_seconds"])
        d["pct"] = round(100 * d["dedup_seconds"] / busiest, 1)

    ticket_range = request.args.get("ticket_range") or _DEFAULT_RANGE
    tick_start, tick_end, ticket_range = _resolve_range(ticket_range)
    top = fq.top_tickets(issues, start=tick_start, end=tick_end, limit=10)
    for t in top:
        t["label"] = _dur(t["seconds"])

    return {
        "dev_rows": dev_rows, "dev_range": dev_range,
        "dev_range_label": _range_label(dev_start, dev_end, dev_range),
        "top_tickets": top, "ticket_range": ticket_range,
        "ticket_range_label": _range_label(tick_start, tick_end, ticket_range),
        "all_devs": sorted(({"id": did, "name": name} for did, name in directory.items()),
                           key=lambda d: d["name"].lower()),
    }


def _dev_dashboard_data(dev_id):
    """Data for one developer's Time Spent dashboard, or None if dev_id doesn't
    resolve to a visible developer — the same set the dropdown offers
    elsewhere in the app, so a stale or hand-edited link can't reach anyone
    who wouldn't otherwise be selectable."""
    import flow_quality as fq
    psel, scope = current_project_selection()
    issues = _issues(scope)
    ids = _ids_by_name()
    dev_name = _developer_directory().get(dev_id)
    if not dev_name:
        return None
    custom_start = (request.args.get("start") or "").strip()
    custom_end = (request.args.get("end") or "").strip()
    start, end, range_key = _resolve_range(
        request.args.get("range") or _DEFAULT_RANGE, custom_start, custom_end)
    if range_key == "custom":
        # The date boxes must always name the window actually in effect, not
        # sit blank while the cards below show a fallback range — same reason
        # the roster page's Start/End boxes were never left empty. `end` is
        # exclusive, so step back a day to name the last day included.
        custom_start = start.strftime("%Y-%m-%d")
        custom_end = (end - dt.timedelta(days=1)).strftime("%Y-%m-%d")

    all_totals = fq.dev_time_totals(issues, developer=dev_id, start=start, end=end,
                                    match=dr.dev_match_exact, ids_by_name=ids)
    totals = all_totals.get(dev_name,
                            {"raw_seconds": 0, "dedup_seconds": 0, "inflated_seconds": 0})
    rows = fq.active_time(issues, developer=dev_id, start=start, end=end,
                          match=dr.dev_match_exact, ids_by_name=ids)
    by_dev = _group_by_dev(rows, (end - start).total_seconds())
    group = by_dev[0] if by_dev else None

    def link(rk):
        cs = custom_start if rk == "custom" else ""
        ce = custom_end if rk == "custom" else ""
        return _dash_link(dev_id, psel, rk, cs, ce)

    return {
        "dev_name": dev_name, "dev_id": dev_id, "totals": totals, "group": group,
        "range_key": range_key, "custom_start": custom_start, "custom_end": custom_end,
        "window_label": _range_label(start, end, range_key), "link": link, "dur": _dur,
    }


@v3.route("/active-time")
def time_spent_screen():
    import auth
    user = auth.current_user()
    dev_id = (request.args.get("dev") or "").strip()
    if user and user.get("role") != "admin":
        # Employees get their own dashboard and nothing else: no roster (which
        # names the whole team and its busiest tickets), and no colleague's
        # dashboard by hand-editing ?dev=. The developer comes from the account,
        # not the URL, so the query string can't widen it.
        own = (user.get("developer_id") or user.get("developer") or "").strip()
        if not own:
            return page(DEV_UNLINKED_TMPL, active="/active-time", show_banner=False)
        if dev_id != own:
            from urllib.parse import quote
            return redirect("/active-time?dev=" + quote(own))
        dev_id = own
    if not dev_id:
        return page(TIME_SPENT_TMPL, active="/active-time", show_banner=False,
                    **_landing_data())
    own_only = bool(user and user.get("role") != "admin")
    ctx = _dev_dashboard_data(dev_id)
    if ctx is None:
        # For an employee this means their linked developer isn't selectable any
        # more (hidden in Settings, or no longer a current assignee). Sending
        # them "back to Time-Spent Dashboards" would just bounce them here
        # again, so give them the reason and a way out instead.
        return page(DEV_UNLINKED_TMPL if own_only else DEV_NOT_FOUND_TMPL,
                    active="/active-time", show_banner=False)
    return page(DEV_DASHBOARD_TMPL, active="/active-time", show_banner=False,
                own_only=own_only, **ctx)


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
<td><div style="background:#e7e8e7;border-radius:4px;height:10px;width:100%;max-width:200px"><div style="background:{{ '#d64545' if (w.rate_pct or 0) >= 50 else '#1fa963' }};border-radius:4px;height:10px;width:{{ w.rate_pct or 0 }}%"></div></div></td></tr>
{% else %}<tr><td colspan="5" class="muted">No handoffs recorded in the trend window.</td></tr>{% endfor %}
</table>
"""


def _quality_data():
    import flow_quality as fq
    project, developer, start, end = parse_filters()
    issues = _issues_in_range(project, start, end)  # edited-in-range ticket filter
    return (fq.bug_lens(issues, developer, start, end, dr.dev_match_exact),
            fq.reopen_loops(issues, developer, dr.dev_match_exact),
            fq.return_trend(issues))


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


# ---------------------------------------------------------------------------
# Team Trends + Meeting Mode (FR-X1/X2) and the snapshot/digest task endpoint
# ---------------------------------------------------------------------------

TRENDS_TMPL = """
{% if meeting %}<style>.wrap{max-width:1350px}.card .n{font-size:44px}.card .l{font-size:15px}h1{font-size:28px}td,th{font-size:16px}</style>{% endif %}
<h1>Team Trends</h1>
<div class="sub">Aggregates only — meetings discuss trends, not individuals ·
  <a class="btn-ghost" href="?{{ 'meeting=0' if meeting else 'meeting=1' }}">{{ 'Exit meeting mode' if meeting else 'Meeting Mode' }}</a>
  {% if not meeting %} · <a href="/exec/kpis">legacy KPI dashboard</a>{% endif %}
</div>
<div class="cards">
  {% for m in metrics %}
  <div class="card"><div class="n">{{ m.value }}</div><div class="l">{{ m.label }}
    {% if m.delta is not none %}<br><span class="pill {{ 'ok' if m.good else 'bad' }}">{{ '+' if m.delta > 0 }}{{ m.delta }} wk/wk</span>{% endif %}</div></div>
  {% endfor %}
</div>
{% if not history %}
<div class="sectionbox"><p class="muted"><b>No snapshots yet.</b> Trends need history: schedule a daily hit of
<code>POST /tasks/snapshot</code> (Azure WebJob, Logic App, or cron). Each run stores the day's team
aggregates in SQLite; week-over-week deltas appear after the first week.
{% if snapshot_url %}<br>Use this exact URL (the token is required):<br><code>{{ snapshot_url }}</code>{% endif %}</p></div>
{% else %}
<h2>History</h2>
<table>
<tr><th>Day</th><th>EOD signal %</th><th>Median cycle (h)</th><th>QA return rate</th><th>Blocked</th><th>Attention size</th></tr>
{% for s in history[:30] %}
<tr><td>{{ s.day }}</td><td>{{ s.eod_signal_pct }}%</td><td>{{ s.cycle_median_h or '—' }}</td>
<td>{{ s.return_rate_pct if s.return_rate_pct is not none else '—' }}{{ '%' if s.return_rate_pct is not none }} ({{ s.returns }}/{{ s.handoffs }})</td>
<td>{{ s.blocked_count }}</td><td>{{ s.attention_size }}</td></tr>
{% endfor %}
</table>
{% endif %}
{% if meeting %}
<h2>Distributions <span class="muted">(no names)</span></h2>
<div class="cards">
  <div class="card"><div class="n">{{ dist.aging }}</div><div class="l">tickets over their aging threshold</div></div>
  <div class="card"><div class="n">{{ dist.silent }}</div><div class="l">tickets silent beyond the limit</div></div>
  <div class="card"><div class="n">{{ dist.multi }}</div><div class="l">developers with >1 ticket in an active status (same lane)</div></div>
</div>
{% endif %}
"""


@v3.route("/exec")
def trends_screen():
    import flow_quality as fq
    import snapshots as sn
    meeting = request.args.get("meeting") == "1"
    _psel, scope = current_project_selection()   # respect the project dropdown
    issues = _issues(scope)
    agg = sn.compute_aggregates(issues)
    wow = sn.week_over_week()
    def metric(key, label, value, invert=False):
        w = wow.get(key, {})
        delta = w.get("delta")
        good = (delta or 0) <= 0 if invert else (delta or 0) >= 0
        return {"label": label, "value": value, "delta": delta, "good": good}
    metrics = [
        metric("eod_signal_pct", "Active/paused tickets with EOD signal",
               f"{agg['eod_signal_pct']}% of {agg['eod_total']}"),
        metric("cycle_median_h", "Median cycle time (30d)",
               f"{agg['cycle_median_h'] or '—'}h (n={agg['cycle_n']})", invert=True),
        metric("return_rate_pct", "QA return rate (14d)",
               f"{agg['return_rate_pct'] if agg['return_rate_pct'] is not None else '—'}"
               f"{'%' if agg['return_rate_pct'] is not None else ''} "
               f"({agg['returns']} of {agg['handoffs']})", invert=True),
        metric("blocked_count", "Blocked tickets",
               f"{agg['blocked_count']}"
               + (f" · med {agg['blocked_median_days']}d" if agg['blocked_median_days'] else ""),
               invert=True),
        metric("attention_size", "Attention board size", agg["attention_size"], invert=True),
    ]
    board = attention.board(issues)
    dist = {
        "aging": sum(1 for r in board["rows"] if any(x["kind"] == "aging" for x in r["reasons"])),
        "silent": sum(1 for r in board["rows"] if any(x["kind"] == "silent" for x in r["reasons"])),
        "multi": len(fq.multiple_active(issues)),
    }
    import auth
    snapshot_url = (f"{request.url_root.rstrip('/')}/tasks/snapshot?token={auth.snapshot_token()}"
                    if auth.is_admin() else None)  # only admins see the token
    return page(TRENDS_TMPL, active="/exec", metrics=metrics, history=sn.series(30),
                meeting=meeting, dist=dist, show_banner=not meeting,
                snapshot_url=snapshot_url)


@v3.route("/tasks/snapshot", methods=["GET", "POST"])
def snapshot_task():
    """Scheduled endpoint (cron/WebJob): stores today's aggregate snapshot; with
    ?digest=1 also posts the Teams morning digest (FR-U8).

    Reachable without a login (schedulers can't sign in), so it requires the
    shared token instead — otherwise anyone who guessed the URL could trigger
    Jira pulls, overwrite snapshots, spam the Teams digest, and read team
    aggregates. A logged-in admin may also call it (e.g. to test)."""
    import auth
    import digest as dg
    import snapshots as sn
    supplied = request.args.get("token") or request.headers.get("X-Snapshot-Token", "")
    if not (secrets.compare_digest(supplied, auth.snapshot_token()) or auth.is_admin()):
        return jsonify({"ok": False, "error": "missing or invalid token"}), 403
    issues = _issues(None)
    agg = sn.take(issues)
    sent = False
    if request.args.get("digest") == "1":
        board = attention.board(issues)
        try:
            sent = dg.send(board["rows"], agg)
        except Exception:
            sent = False
    return jsonify({"ok": True, "snapshot": agg, "digest_sent": sent})
