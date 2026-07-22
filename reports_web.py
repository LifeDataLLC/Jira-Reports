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
import re

from urllib.parse import quote

from flask import Blueprint, Response, jsonify, redirect, render_template_string, request

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

# Full-screen loading overlay. Shown via JS the instant the user clicks an
# internal link or submits a filter form, so the (server-rendered, Jira-backed)
# pages give immediate feedback while the data is fetched. It is part of every
# page's markup but stays hidden until triggered; the next page load replaces
# the document, which clears it. Imported by app.py so the v0 pages match.
LOADING_OVERLAY = """
<div id="loadingOverlay" aria-hidden="true" role="status">
  <div class="lo-box">
    <div class="lo-spinner"></div>
    <div class="lo-text">We are loading your report…</div>
    <div class="lo-sub">Pulling the latest data from Jira</div>
  </div>
</div>
<style>
 #loadingOverlay{position:fixed;inset:0;z-index:9999;display:none;align-items:center;
   justify-content:center;background:rgba(244,245,247,.85);-webkit-backdrop-filter:blur(2px);
   backdrop-filter:blur(2px)}
 #loadingOverlay.show{display:flex}
 #loadingOverlay .lo-box{text-align:center;background:#fff;padding:30px 42px;border-radius:12px;
   box-shadow:0 6px 24px rgba(9,30,66,.18)}
 #loadingOverlay .lo-spinner{width:44px;height:44px;margin:0 auto 16px;border:4px solid #dfe7f5;
   border-top-color:#1fa963;border-radius:50%;animation:lo-spin .8s linear infinite}
 #loadingOverlay .lo-text{font-size:16px;font-weight:600;color:#172b4d}
 #loadingOverlay .lo-sub{font-size:13px;color:#6b778c;margin-top:4px}
 @keyframes lo-spin{to{transform:rotate(360deg)}}
 @media (prefers-reduced-motion:reduce){ #loadingOverlay .lo-spinner{animation-duration:2s}}
</style>
<script>
(function(){
  function ov(){return document.getElementById('loadingOverlay');}
  function show(){var o=ov();if(o)o.classList.add('show');}
  function hide(){var o=ov();if(o)o.classList.remove('show');}
  // Back/forward cache restore should not leave the spinner up.
  window.addEventListener('pageshow',function(e){if(e.persisted)hide();});
  document.addEventListener('click',function(e){
    var a=e.target.closest?e.target.closest('a'):null;
    if(!a)return;
    var href=a.getAttribute('href')||'';
    if(a.target==='_blank'||a.hasAttribute('download'))return;
    if(href===''||href.charAt(0)==='#')return;
    if(/\\.(csv|xlsx?|json|pdf|zip|tsv)(\\?|#|$)/i.test(href))return;  // file download, no navigation
    if(a.host&&a.host!==location.host)return;            // external link
    if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0)return; // open-in-new-tab
    show();
  });
  document.addEventListener('submit',function(){show();});  // filter/timeframe forms
})();
</script>
"""


TOP = """
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#172b4d;background:#f4f5f7}
 nav{background:#212121;padding:10px 20px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
 nav a{color:#c9cbc9;text-decoration:none;font-size:13px;padding:5px 10px;border-radius:5px}
 nav a:hover,nav a.active{background:#fff;color:#212121}
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
 a{color:#1fa963;text-decoration:none}a:hover{text-decoration:underline}
 .pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#dfe1e6}
 .warn{background:#ffebe6;color:#bf2600}.ok{background:#e3fcef;color:#006644}
 .muted{color:#6b778c;font-size:12px}
 .sectionbox{background:#fff;border-radius:8px;padding:16px 18px;box-shadow:0 1px 3px rgba(9,30,66,.12);margin-bottom:16px}
</style>
<nav>
 <span class="brand">LifeData Eng Reports</span>
 <a href="/my-day">My Day</a>
 <a href="/attention">Attention</a>
 <a href="/qa">QA</a>
 <a href="/flow">Flow</a>
 <a href="/quality">Quality</a>
 <a href="/release">Release</a>
 <a href="/investigate">Investigate</a>
 <a href="/exec">Trends</a>
 <a href="/settings">Settings</a>
</nav>
""" + LOADING_OVERLAY + """
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


@bp.route("/exec/kpis")
def exec_dashboard():
    d = R.executive_dashboard(dataset(), days_back=cfg.STUCK_THRESHOLD_DAYS if False else 7)
    return page(EXEC, d=d, projects=", ".join(jc.PROJECT_KEYS),
                now=time.strftime("%Y-%m-%d %H:%M"))


# ---------------------------------------------------------------------------
# Report 1 — Daily movement
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Report 3 — Developer productivity
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Report 4 — QA productivity
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Report 6 — Status duration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Report 7 — Release readiness
# ---------------------------------------------------------------------------

def _burnup_svg(d):
    """Inline SVG: cumulative tickets reaching development-complete over the last
    8 weeks, with the scope line, a dashed projection to the projected date, and a
    target-date marker."""
    bu = d.get("burnup") or []
    total = d.get("total") or 0
    if not bu or total == 0:
        return '<div class="rr-muted">No development activity yet.</div>'
    L, Rt, T, B = 44, 540, 16, 176           # plot box
    dmin = -56.0
    dmax = max(14.0, float(d.get("days_to_target") or 0), float(d.get("proj_days") or 0)) or 14.0
    xp = lambda t: L + (t - dmin) / (dmax - dmin) * (Rt - L)
    yp = lambda c: B - (c / total) * (B - T)
    pts = [(xp(-b["days_ago"]), yp(b["count"])) for b in bu]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{L:.1f},{B:.1f} " + line + f" {pts[-1][0]:.1f},{B:.1f}"
    parts = [f'<svg viewBox="0 0 560 200" role="img" aria-label="Development burn-up">']
    # gridlines + y labels (0, half, full)
    for frac in (0, 0.5, 1):
        y = yp(total * frac)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{Rt}" y2="{y:.1f}" stroke="#eef1ef"/>')
        parts.append(f'<text x="{L-6}" y="{y+3:.1f}" font-size="9" fill="#98a099" text-anchor="end">{round(total*frac)}</text>')
    # scope line
    parts.append(f'<line x1="{L}" y1="{yp(total):.1f}" x2="{Rt}" y2="{yp(total):.1f}" stroke="#98a099" stroke-dasharray="2 3"/>')
    parts.append(f'<text x="{Rt}" y="{yp(total)-4:.1f}" font-size="9" fill="#98a099" text-anchor="end">scope {total}</text>')
    # area + line
    parts.append(f'<polygon points="{area}" fill="#1fa96322"/>')
    parts.append(f'<polyline points="{line}" fill="none" stroke="#17864e" stroke-width="2.5"/>')
    lx, ly = pts[-1]
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="#17864e"/>')
    # projection (dashed) to (proj_days, total)
    pd = d.get("proj_days")
    if pd is not None and pd > 0:
        parts.append(f'<polyline points="{lx:.1f},{ly:.1f} {xp(pd):.1f},{yp(total):.1f}" '
                     f'fill="none" stroke="#b7791f" stroke-width="2" stroke-dasharray="5 4"/>')
        parts.append(f'<circle cx="{xp(pd):.1f}" cy="{yp(total):.1f}" r="3" fill="#b7791f"/>')
    # target marker
    td = d.get("days_to_target")
    if td is not None and td >= 0:
        parts.append(f'<line x1="{xp(td):.1f}" y1="{T}" x2="{xp(td):.1f}" y2="{B}" stroke="#d64545" stroke-width="1.5" stroke-dasharray="3 3"/>')
        parts.append(f'<text x="{xp(td):.1f}" y="{B+14}" font-size="9" fill="#d64545" text-anchor="middle">target</text>')
    parts.append(f'<line x1="{xp(0):.1f}" y1="{T}" x2="{xp(0):.1f}" y2="{B}" stroke="#c9cdca"/>')
    parts.append(f'<text x="{xp(0):.1f}" y="{B+14}" font-size="9" fill="#98a099" text-anchor="middle">now</text>')
    parts.append('</svg>')
    return "".join(parts)


REL = """
<style>
.rrw{max-width:1120px;margin:0 auto}
.rrw .eyebrow{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b756e}
.rrw h1.rt{font-size:24px;font-weight:800;margin:2px 0 4px;letter-spacing:-.4px}
.rrw .rsub{color:#6b756e;font-size:13.5px;margin:0 0 16px}
.rrw .rsw{display:flex;align-items:center;gap:12px;margin:14px 0 20px;flex-wrap:wrap}
.rrw .rsw-lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#6b756e}
.rrw .rsw-box{position:relative}
.rrw .rsw-trigger{display:flex;align-items:center;gap:11px;background:#fff;border:1px solid #e4e7e5;border-radius:10px;padding:9px 12px;cursor:pointer;min-width:320px;box-shadow:0 1px 2px rgba(9,30,20,.05)}
.rrw .rsw-trigger:hover{border-color:#98a099}
.rrw .rsw-trigger.open{border-color:#1fa963;box-shadow:0 0 0 3px rgba(31,169,99,.2)}
.rrw .rsw-dot{width:9px;height:9px;border-radius:50%;flex:none;display:inline-block}
.rrw .rsw-dot.red{background:#d64545}.rrw .rsw-dot.amber{background:#b7791f}.rrw .rsw-dot.green{background:#1fa963}.rrw .rsw-dot.none{background:#c9cdca}
.rrw .rsw-name{font-weight:700;font-size:14px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rrw .rsw-meta{font-size:12px;color:#6b756e;white-space:nowrap}
.rrw .rsw-chev{color:#98a099;font-size:11px}
.rrw .rsw-trigger.open .rsw-chev{transform:rotate(180deg)}
.rrw .rsw-menu{position:absolute;top:calc(100% + 8px);left:0;width:430px;max-width:92vw;background:#fff;border:1px solid #e4e7e5;border-radius:12px;box-shadow:0 6px 24px rgba(9,30,20,.14);z-index:30;overflow:hidden;display:none}
.rrw .rsw-menu.open{display:block}
.rrw .rsw-search{padding:11px 12px;border-bottom:1px solid #eef1ef}
.rrw .rsw-search input{width:100%;border:1px solid #e4e7e5;border-radius:8px;padding:8px 11px;font-size:13px;font-family:inherit;color:#1c2620}
.rrw .rsw-search input:focus{outline:none;border-color:#1fa963;box-shadow:0 0 0 3px rgba(31,169,99,.18)}
.rrw .rsw-seg{display:flex;gap:4px;padding:10px 12px 4px;flex-wrap:wrap}
.rrw .rsw-seg button{border:1px solid #e4e7e5;background:#fff;color:#3a453e;font:inherit;font-size:12px;font-weight:600;padding:5px 12px;border-radius:999px;cursor:pointer}
.rrw .rsw-seg button.on{background:#1fa963;border-color:#1fa963;color:#fff}
.rrw .rsw-list{max-height:340px;overflow-y:auto;padding:6px}
.rrw .rsw-grp{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:#98a099;padding:12px 10px 5px}
.rrw .rsw-opt{display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center;padding:8px 10px;border-radius:8px;cursor:pointer}
.rrw .rsw-opt:hover{background:#eef1ef}
.rrw .rsw-opt.sel{background:#e9f6ef}
.rrw .rsw-opt .nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rrw .rsw-opt .tg{font-size:10px;font-weight:700;color:#6b756e;border:1px solid #e4e7e5;border-radius:5px;padding:1px 5px;margin-left:7px;text-transform:uppercase}
.rrw .rsw-opt .tg.bug{color:#d64545;border-color:rgba(214,69,69,.4)}
.rrw .rsw-opt .wh{font-size:11.5px;color:#6b756e;text-align:right;white-space:nowrap}
.rrw .rsw-opt .wh b{color:#1c2620}
.rrw .rsw-legend{display:flex;gap:14px;font-size:11px;color:#6b756e;padding:9px 14px;border-top:1px solid #eef1ef}
.rrw .rsw-legend span{display:inline-flex;align-items:center;gap:5px}
.rrw .rsw-empty{padding:16px;text-align:center;color:#6b756e;font-size:12.5px}
.rrw .verdict{display:grid;grid-template-columns:auto 1fr auto;gap:22px;align-items:center;background:#fff;border:1px solid #e4e7e5;border-left:5px solid #b7791f;border-radius:12px;padding:18px 22px;box-shadow:0 1px 2px rgba(9,30,20,.05);margin-bottom:18px}
.rrw .verdict.go{border-left-color:#1fa963}.rrw .verdict.risk{border-left-color:#b7791f}.rrw .verdict.no{border-left-color:#d64545}
.rrw .badge{font-size:19px;font-weight:800;padding:6px 14px;border-radius:8px;display:inline-block}
.rrw .badge.go{background:#e9f6ef;color:#17864e}.rrw .badge.risk{background:#fdf3e3;color:#8a5a14}.rrw .badge.no{background:#fbeaea;color:#a82f2f}
.rrw .reasons{display:flex;flex-direction:column;gap:6px}
.rrw .reasons .r{font-size:13px;color:#3a453e;display:flex;gap:8px;align-items:baseline}
.rrw .rdot{width:7px;height:7px;border-radius:50%;flex:none}
.rrw .rdot.bad{background:#d64545}.rrw .rdot.warn{background:#b7791f}.rrw .rdot.ok{background:#1fa963}
.rrw .countdown{text-align:right;padding-left:20px;border-left:1px solid #e4e7e5}
.rrw .countdown .big{font-size:30px;font-weight:800;line-height:1;letter-spacing:-1px}
.rrw .countdown .lbl{font-size:11px;color:#6b756e;margin-top:3px}
.rrw .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
.rrw .tile{background:#fff;border:1px solid #e4e7e5;border-radius:12px;padding:15px 17px;box-shadow:0 1px 2px rgba(9,30,20,.05);display:flex;flex-direction:column;gap:8px}
.rrw .tile .tl{font-size:12px;color:#6b756e;font-weight:600}
.rrw .tile .val{font-size:26px;font-weight:800;letter-spacing:-.6px;line-height:1}
.rrw .tile .val small{font-size:14px;font-weight:600;color:#6b756e}
.rrw .tile .meta{font-size:12px;color:#6b756e}
.rrw .chip{font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px}
.rrw .chip.ok{background:#e9f6ef;color:#17864e}.rrw .chip.warn{background:#fdf3e3;color:#8a5a14}.rrw .chip.bad{background:#fbeaea;color:#a82f2f}
.rrw .th{display:flex;justify-content:space-between;align-items:center;gap:8px}
.rrw .rbar{height:7px;background:#eef1ef;border-radius:999px;overflow:hidden}
.rrw .rbar>span{display:block;height:100%;background:#1fa963;border-radius:999px}
.rrw .grid2{display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-bottom:18px}
.rrw .panel{background:#fff;border:1px solid #e4e7e5;border-radius:12px;padding:17px 19px;box-shadow:0 1px 2px rgba(9,30,20,.05)}
.rrw .panel h2{font-size:14px;margin:0 0 2px;font-weight:700}
.rrw .panel .hint{font-size:12px;color:#6b756e;margin:0 0 14px}
.rrw svg{display:block;width:100%;height:auto}
.rrw .funnel{display:flex;flex-direction:column;gap:11px}
.rrw .frow{display:grid;grid-template-columns:150px 1fr 54px;gap:12px;align-items:center}
.rrw .frow .fl{font-size:12.5px;color:#1c2620;font-weight:600}
.rrw .frow .fl small{display:block;font-weight:400;color:#6b756e;font-size:11px}
.rrw .ftrack{height:10px;background:#eef1ef;border-radius:999px;overflow:hidden}
.rrw .ftrack>span{display:block;height:100%;border-radius:999px}
.rrw .fn{text-align:right;font-weight:800;font-size:14px}
.rrw .fn small{display:block;font-weight:600;color:#6b756e;font-size:11px}
.rrw .gaps{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px;font-size:12.5px;margin-top:4px}
.rrw .gaps .k{display:flex;justify-content:space-between;gap:10px;color:#3a453e}
.rrw .rubric{display:flex;flex-direction:column;gap:1px;background:#eef1ef;border:1px solid #e4e7e5;border-radius:8px;overflow:hidden}
.rrw .rrow{display:grid;grid-template-columns:1fr auto auto;gap:14px;align-items:center;background:#fff;padding:11px 14px}
.rrw .rrow .name{font-size:13px;font-weight:600;color:#1c2620}
.rrw .rrow .name small{display:block;font-weight:400;color:#6b756e;font-size:11.5px}
.rrw .rrow .measure{font-size:11px;color:#98a099;text-align:right;max-width:220px}
.rrw .rrow .st{justify-self:end;display:flex;align-items:center;gap:8px}
.rrw .rrow .st .v{font-size:15px;font-weight:800}
.rrw .own{display:flex;flex-direction:column;gap:9px}
.rrw .orow{display:grid;grid-template-columns:120px 1fr 34px;gap:10px;align-items:center;font-size:12.5px}
.rrw .orow .nm{color:#3a453e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rrw .otrack{height:9px;background:#eef1ef;border-radius:999px;overflow:hidden}
.rrw .otrack>span{display:block;height:100%;background:#1fa963;border-radius:999px}
.rrw .orow .on{text-align:right;color:#6b756e;font-weight:600}
.rrw table.mc{width:100%;border-collapse:collapse;font-size:12.5px;box-shadow:none;background:transparent}
.rrw table.mc th,.rrw table.mc td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef1ef;background:transparent}
.rrw table.mc th{font-size:11px;color:#6b756e;text-transform:uppercase;letter-spacing:.03em}
.rrw .tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:#eef1ef;color:#3a453e}
.rrw .tag.bad{background:#fbeaea;color:#a82f2f}.rrw .tag.warn{background:#fdf3e3;color:#8a5a14}
.rrw .age{color:#a82f2f;font-weight:600}
.rrw .rr-muted{color:#6b756e;font-size:12.5px;padding:20px 0}
@media (max-width:820px){.rrw .tiles{grid-template-columns:repeat(2,1fr)}.rrw .grid2{grid-template-columns:1fr}.rrw .verdict{grid-template-columns:1fr}.rrw .countdown{border-left:none;padding-left:0;text-align:left}}
</style>
<div class="rrw">
<div class="eyebrow">Release Readiness</div>
{% if not d %}<h1 class="rt">Pick a release</h1>
<div class="rsub">Choose a fix version to see how ready it is to ship.</div>{% endif %}
{% if not versions_data %}
<div class="rsw"><span class="rr-muted">No unreleased fix versions found.</span></div>
{% else %}
<div class="rsw">
  <span class="rsw-lbl">Release</span>
  <div class="rsw-box">
    <div class="rsw-trigger" id="rswTrigger" onclick="rswToggle()">
      <span class="rsw-dot {{ selected.cls if selected else 'none' }}"></span>
      <span class="rsw-name">{{ selected.name if selected else 'Choose a release' }}</span>
      <span class="rsw-meta">{% if selected and selected.date_label %}ships {{ selected.date_label }}{% if selected.days is not none %} · {% if selected.days < 0 %}{{ -selected.days }}d overdue{% else %}{{ selected.days }} days{% endif %}{% endif %}{% endif %}</span>
      <span class="rsw-chev">&#9660;</span>
    </div>
    <div class="rsw-menu" id="rswMenu">
      <div class="rsw-search"><input id="rswSearch" placeholder="Search releases&hellip;" autocomplete="off" oninput="rswRender()"></div>
      <div class="rsw-seg" id="rswSeg">
        <button class="on" data-p="all" onclick="rswSetPlat('all',this)">All</button>
        {% for p in platforms %}<button data-p="{{ p }}" onclick="rswSetPlat('{{ p }}',this)">{{ p }}</button>{% endfor %}
      </div>
      <div class="rsw-list" id="rswList"></div>
      <div class="rsw-legend"><span><span class="rsw-dot red"></span>overdue</span><span><span class="rsw-dot amber"></span>&le; 7 days</span><span><span class="rsw-dot green"></span>on track</span></div>
    </div>
  </div>
</div>
<script>
(function(){
  var DATA={{ versions_data|tojson }}, CHOSEN={{ chosen|tojson }}, plat="all";
  var order={Web:0,iOS:1,Android:2,Backend:3,Other:4};
  var trigger=document.getElementById('rswTrigger'), menu=document.getElementById('rswMenu'),
      list=document.getElementById('rswList'), search=document.getElementById('rswSearch');
  if(!trigger) return;
  function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
  function whenTxt(v){if(!v.date_label)return 'no date';if(v.days===null)return esc(v.date_label);
    return esc(v.date_label)+' &middot; <b>'+(v.days<0?(-v.days)+'d overdue':v.days+'d')+'</b>';}
  window.rswToggle=function(){var o=menu.classList.toggle('open');trigger.classList.toggle('open',o);if(o){search.focus();rswRender();}};
  window.rswSetPlat=function(p,btn){plat=p;var seg=document.getElementById('rswSeg');
    Array.prototype.forEach.call(seg.children,function(b){b.classList.toggle('on',b===btn);});rswRender();};
  function close(){menu.classList.remove('open');trigger.classList.remove('open');}
  window.rswRender=function(){
    var q=(search.value||'').trim().toLowerCase();
    var rows=DATA.filter(function(v){return (plat==='all'||v.platform===plat)&&(!q||v.name.toLowerCase().indexOf(q)>=0);});
    rows.sort(function(a,b){var pa=order[a.platform]==null?9:order[a.platform],pb=order[b.platform]==null?9:order[b.platform];
      if(pa!==pb)return pa-pb;var da=a.days==null?1e9:a.days,db=b.days==null?1e9:b.days;return da-db;});
    list.innerHTML='';
    if(!rows.length){list.innerHTML='<div class="rsw-empty">No releases match.</div>';return;}
    var cur=null;
    rows.forEach(function(v){
      if(v.platform!==cur){cur=v.platform;var h=document.createElement('div');h.className='rsw-grp';h.textContent=v.platform;list.appendChild(h);}
      var o=document.createElement('div');o.className='rsw-opt'+(v.name===CHOSEN?' sel':'');
      o.onclick=function(){window.location='/release?version='+encodeURIComponent(v.name);};
      o.innerHTML='<span class="rsw-dot '+v.cls+'"></span><span class="nm">'+esc(v.short||v.name)+
        '<span class="tg'+(v.type==='Bug'?' bug':'')+'">'+esc(v.type)+'</span></span>'+
        '<span class="wh">'+whenTxt(v)+'</span>';
      list.appendChild(o);
    });
  };
  document.addEventListener('keydown',function(e){if(e.key==='Escape')close();});
  document.addEventListener('click',function(e){if(!e.target.closest('.rsw-box'))close();});
  rswRender();
})();
</script>
{% endif %}

{% if d %}
{% set vcls = {'GO':'go','AT RISK':'risk','NO-GO':'no'}[d.verdict] %}
{% set fcolor = {'dev_completed':'#00b8d9','passed_qa':'#6554c0','passed_staging':'#57d9a3','in_production':'#00875a','done':'#36b37e'} %}
<h1 class="rt">{{ d.version }}</h1>
<div class="rsub">Is this fix version ready to ship, and if not, what's standing in the way?</div>

<div class="verdict {{ vcls }}">
  <div><span class="badge {{ vcls }}">{{ d.verdict }}</span></div>
  <div class="reasons">
    {% for lvl,txt in d.reasons %}<div class="r"><span class="rdot {{ lvl }}"></span>{{ txt }}</div>{% endfor %}
  </div>
  <div class="countdown">
    {% if d.days_to_target is not none %}<div class="big">{{ d.days_to_target }}</div><div class="lbl">days to target{% if d.release_date %} · {{ d.release_date.strftime('%b %-d') }}{% endif %}</div>
    {% else %}<div class="big">—</div><div class="lbl">no target date set</div>{% endif %}
  </div>
</div>

<div class="tiles">
  <div class="tile"><div class="th"><span class="tl">Development completed</span><span class="chip {{ 'ok' if d.dev_completed_pct>=50 else 'warn' }}">+{{ d.throughput }} / wk</span></div>
    <div class="val">{{ d.dev_completed_pct }}<small>%</small></div><div class="rbar"><span style="width:{{ d.dev_completed_pct }}%"></span></div>
    <div class="meta">{{ d.dev_completed }} of {{ d.total }} reached Ready-for-QA+</div></div>
  <div class="tile"><div class="th"><span class="tl">Projected dev-complete</span>{% if d.proj_delta is not none %}<span class="chip {{ 'warn' if d.proj_delta>0 else 'ok' }}">{{ '%+d'|format(d.proj_delta) }}d</span>{% endif %}</div>
    <div class="val">{% if d.proj_date %}{{ d.proj_date.strftime('%b %-d') }}{% else %}—{% endif %}</div>
    <div class="meta">{{ d.remaining_dev }} left ÷ ~{{ d.throughput }}/wk</div></div>
  <div class="tile"><div class="th"><span class="tl">Blockers to ship</span><span class="chip {{ 'bad' if (d.open_critical+d.blocked)>0 else 'ok' }}">{{ 'action' if (d.open_critical+d.blocked)>0 else 'clear' }}</span></div>
    <div class="val">{{ d.open_critical + d.blocked }}</div><div class="meta">{{ d.open_critical }} critical bug{{ 's' if d.open_critical!=1 else '' }} · {{ d.blocked }} blocked</div></div>
  <div class="tile"><div class="th"><span class="tl">Release-ready (passed staging)</span><span class="chip {{ 'ok' if d.passed_staging_pct>=80 else 'warn' }}">{{ d.passed_staging_pct }}%</span></div>
    <div class="val">{{ d.passed_staging }} <small>/ {{ d.total }}</small></div><div class="rbar"><span style="width:{{ d.passed_staging_pct }}%;background:#57d9a3"></span></div>
    <div class="meta">verified in staging, awaiting cutover</div></div>
</div>

<div class="grid2">
  <div class="panel">
    <h2>Development burn-up</h2>
    <div class="hint">Tickets reaching development-complete over time, projected against the target date.</div>
    {{ burnup_svg|safe }}
  </div>
  <div class="panel">
    <h2>Readiness pipeline</h2>
    <div class="hint">Each milestone counts tickets that reached it <b>or beyond</b>.</div>
    <div class="funnel">
    {% for f in d.funnel %}
      <div class="frow"><div class="fl">{{ f.label }}</div>
        <div class="ftrack"><span style="width:{{ f.pct }}%;background:{{ fcolor[f.id] }}"></span></div>
        <div class="fn">{{ f.count }}<small>{{ f.pct }}%</small></div></div>
    {% endfor %}
    </div>
    <div style="height:1px;background:#eef1ef;margin:15px 0"></div>
    <h2 style="font-size:12.5px;margin-bottom:8px">Coverage gaps</h2>
    <div class="gaps">
      <div class="k"><span>Missing due date</span><b style="color:#a82f2f">{{ d.missing_due }}</b></div>
      <div class="k"><span>Not started (To Do)</span><b style="color:#8a5a14">{{ d.not_started }}</b></div>
      <div class="k"><span>Missing release</span><b>{{ d.no_release }}</b></div>
      <div class="k"><span>Unassigned</span><b>{{ d.unassigned }}</b></div>
    </div>
  </div>
</div>

<div class="panel" style="margin-bottom:18px">
  <h2>Release risk — the checklist behind the verdict</h2>
  <div class="hint">Each gate has an explicit threshold. The verdict is the worst status across all gates.</div>
  <div class="rubric">
  {% for g in d.gates %}
    <div class="rrow"><div class="name">{{ g.name }}<small>{{ g.sub }}</small></div>
      <div class="measure">gate: {{ g.measure }}</div>
      <div class="st"><span class="v" style="color:{{ '#a82f2f' if g.status=='bad' else ('#8a5a14' if g.status=='warn' else '#17864e') }}">{{ g.value }}</span>
        <span class="chip {{ g.status }}">{{ {'ok':'OK','warn':'OVER','bad':'FAIL'}[g.status] }}</span></div></div>
  {% endfor %}
  </div>
</div>

<div class="grid2">
  <div class="panel">
    <h2>Open work by owner</h2>
    <div class="hint">Remaining open tickets per developer — where the release is bottlenecked.</div>
    <div class="own">
    {% set omax = d.ownership[0].count if d.ownership else 1 %}
    {% for o in d.ownership %}
      <div class="orow"><span class="nm">{{ o.name }}</span>
        <div class="otrack"><span style="width:{{ (100*o.count/omax)|round(0,'floor') }}%"></span></div>
        <span class="on">{{ o.count }}</span></div>
    {% else %}<div class="rr-muted">No open work. 🎉</div>{% endfor %}
    </div>
  </div>
  <div class="panel">
    <h2>Must-clear before ship</h2>
    <div class="hint">Open critical/high bugs and blocked tickets, oldest first.</div>
    <table class="mc"><tr><th>Key</th><th>Summary</th><th>Type</th><th>Age</th></tr>
    {% for r in d.must_clear %}
      <tr><td><a href="{{ r.url }}" target="_blank">{{ r.key }}</a></td><td>{{ r.summary|truncate(48) }}</td>
      <td><span class="tag {{ r.cls }}">{{ r.tag }}</span></td>
      <td class="{{ 'age' if r.age and r.age>=5 else '' }}">{{ fmt(r.age) }}</td></tr>
    {% else %}<tr><td colspan="4" class="rr-muted">Nothing blocking. 🎉</td></tr>{% endfor %}
    </table>
  </div>
</div>
{% endif %}
</div>
"""


# Order platforms appear in the release switcher.
_PLATFORM_ORDER = ["Web", "iOS", "Android", "Backend", "Other"]


def _platform_of(name):
    n = name.lower()
    if re.search(r"\bios\b", n):
        return "iOS"
    if re.search(r"\bandroid\b", n):
        return "Android"
    if re.search(r"\bweb\b", n):
        return "Web"
    if re.search(r"\b(be|backend|back[\s-]?end)\b", n):
        return "Backend"
    return "Other"


def _type_of(name):
    n = name.lower()
    if re.search(r"\b(bug|hotfix|patch)\b", n):
        return "Bug"
    if re.search(r"\bfeature\b", n):
        return "Feature"
    return "Release"


def _short_name(name):
    """Drop a leading platform word so the switcher rows aren't redundant with the
    group header (e.g. 'Web Feature Release 0.12.0' -> 'Feature Release 0.12.0')."""
    parts = name.split(" ", 1)
    if len(parts) == 2 and re.fullmatch(r"(?i)ios|android|web|be|backend", parts[0]):
        return parts[1]
    return name


def _version_meta(name, release_date, today):
    days = (release_date - today).days if release_date else None
    cls = "none" if days is None else ("red" if days < 0 else ("amber" if days <= 7 else "green"))
    return {
        "name": name,
        "short": _short_name(name),
        "platform": _platform_of(name),
        "type": _type_of(name),
        "date_label": release_date.strftime("%b %-d") if release_date else "",
        "days": days,
        "cls": cls,
    }


def release_context(chosen):
    """Build the Release Readiness template context. Produces the release-switcher
    metadata (platform / type / date / urgency per version) and, when nothing is
    chosen, defaults to the soonest upcoming release. Shared by the /release page."""
    today = dt.date.today()
    date_by_name, metas = {}, []
    for v in jc.fetch_project_versions():
        if v.get("released"):
            continue
        name = v.get("name")
        rd = R._parse_date(v.get("releaseDate"))
        date_by_name[name] = rd
        metas.append(_version_meta(name, rd, today))

    def sort_key(m):
        pidx = _PLATFORM_ORDER.index(m["platform"]) if m["platform"] in _PLATFORM_ORDER else 99
        return (pidx, m["days"] is None, m["days"] if m["days"] is not None else 0)
    metas.sort(key=sort_key)
    platforms = [p for p in _PLATFORM_ORDER if any(m["platform"] == p for m in metas)]

    # Default to the soonest upcoming release (else the soonest dated one).
    if not chosen and metas:
        dated = [m for m in metas if m["days"] is not None]
        upcoming = [m for m in dated if m["days"] >= 0]
        pool = upcoming or dated or metas
        chosen = min(pool, key=lambda m: m["days"] if m["days"] is not None else 10**9)["name"]

    selected = next((m for m in metas if m["name"] == chosen), None)
    d, burnup_svg = None, ""
    if chosen:
        d = R.release_readiness(jc.fetch_issues_for_version(chosen), chosen,
                                release_date=date_by_name.get(chosen))
        burnup_svg = _burnup_svg(d)
    return {"versions_data": metas, "platforms": platforms, "chosen": chosen,
            "selected": selected, "d": d, "burnup_svg": burnup_svg}


@bp.route("/reports/release")
def release():
    # Release Readiness now lives at /release (a top-level nav page). Keep the old
    # URL working by redirecting, preserving the selected version.
    chosen = request.args.get("version")
    return redirect("/release" + (("?version=" + quote(chosen)) if chosen else ""), code=302)


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
    import settings as _st
    s = _st.load()
    if not (s["gates"].get("sprints_enabled") and s.get("board_ids")):
        return page(SPRINT, configured=False, sprints=[])
    return page(SPRINT, configured=True, sprints=R.sprint_health(jc.fetch_active_sprints()))


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
  <a class="pill" style="float:right" href="/reports/time-in-status.csv?{{ query }}" download>Download CSV</a>
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
        "deprecation": "This combined feed is deprecated; use the per-screen "
                       "/api/v2/... endpoints (myday, attention, feed).",
        "executive": {k: ed[k] for k in ("delivery", "productivity", "quality", "risk")},
        "developers": R.developer_productivity(issues, jc.WINDOW_DAYS)["rows"],
        "qa": R.qa_productivity(issues, jc.WINDOW_DAYS)["rows"],
        "status_duration": R.status_duration(issues)["rows"],
    })
