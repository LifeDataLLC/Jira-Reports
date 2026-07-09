"""
Unit tests for the v3 platform: settings store, checklist engine, attention
reasons, handoff edges, timeline gaps, redirects. Fixtures follow the existing
pattern (LIFEDATAV2-shaped changelogs). Run: python3 test_v3.py
"""

import datetime as dt
import json
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="jira_v3_test_")
os.environ.setdefault("APP_CONFIG_PATH", os.path.join(_tmp, "settings.json"))
os.environ.setdefault("SNAPSHOT_DB_PATH", os.path.join(_tmp, "snapshots.db"))

import analytics as A  # noqa: E402
import settings as st  # noqa: E402

now = A.now_utc()
PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, f"FAIL: {name}"
    PASSED += 1


# ---------------------------------------------------------------------------
# Phase 0 — settings store
# ---------------------------------------------------------------------------

def test_settings():
    s = st.load()
    check("seeds workflow active_dev", s["status_buckets"].get("In Progress / Start Investigation") == "active_dev")
    check("seed rework", s["status_buckets"].get("Reopen") == "rework")
    check("seed qa", s["status_buckets"].get("Ready for QA (QA Env)") == "qa_stage")
    check("seed staging->qa", s["status_buckets"].get("In Staging Testing") == "qa_stage")
    # apply_workflow is deterministic regardless of shared-file state
    fresh = json.loads(json.dumps(st.DEFAULTS))
    st.apply_workflow(fresh)
    check("workflow enables worklog+due gates",
          fresh["gates"]["worklogs_required"] and fresh["gates"]["due_dates_required"])
    check("other gates off", not fresh["gates"]["sprints_enabled"] and not fresh["gates"]["estimates_used"])
    check("active statuses seeded", len(fresh["active_statuses"]) == 5)
    check("active lane + pause", fresh["active_statuses"]["In QA Testing (QA Env)"]["lane"] == "qa"
          and fresh["active_statuses"]["In QA Testing (QA Env)"]["pause"] == "Pause QA Testing")

    check("bucket_of mapped", st.bucket_of("Reopen") == "rework")
    check("bucket_of unmapped is None", st.bucket_of("Weird New Status") is None)
    check("bucket_of done category fallback", st.bucket_of("Weird Done", "Done") == "done")

    check("threshold per-status from workflow", st.threshold_for("Ready for QA (QA Env)") == 2)
    check("threshold bucket default", st.threshold_for("Development Completed") == 3)  # qa_stage default
    s["status_thresholds"]["Ready for QA (QA Env)"] = 1.5
    st.save(s)
    check("threshold per-status override", st.threshold_for("Ready for QA (QA Env)") == 1.5)
    check("threshold none for done", st.threshold_for("Done") is None)

    check("unmapped detection", st.unmapped_statuses({"Reopen", "Mystery"}) == ["Mystery"])

    s2 = st.load()
    s2["gates"]["worklogs_required"] = True
    st.save(s2)
    check("gate persists", st.gate("worklogs_required") is True)
    s2["gates"]["worklogs_required"] = False
    st.save(s2)


# ---------------------------------------------------------------------------
# Phase 1 — fixtures + checklist engine + attention reasons + redirects
# ---------------------------------------------------------------------------

def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def adf(text):
    return {"type": "doc", "content": [{"type": "paragraph",
                                        "content": [{"type": "text", "text": text}]}]}


def mkraw(key, status, cat, assignee="Jane Doe", typ="Story", created_d=10,
          events=None, comments=None, worklogs=None, duedate=None, labels=None,
          fix_versions=None):
    hist = []
    for e in (events or []):
        d_ago, author, fieldname, frm, to = e
        hist.append({"created": iso(now - dt.timedelta(days=d_ago)),
                     "author": {"displayName": author, "accountId": author.lower().replace(" ", "")},
                     "items": [{"field": fieldname, "fromString": frm, "toString": to}]})
    return {"key": key, "fields": {
        "summary": "Fix " + key, "issuetype": {"name": typ},
        "status": {"name": status, "statusCategory": {"name": cat}},
        "assignee": {"displayName": assignee, "accountId": assignee.lower().replace(" ", "")},
        "reporter": {"displayName": "PM"},
        "created": iso(now - dt.timedelta(days=created_d)),
        "updated": iso(now - dt.timedelta(days=1)),
        "resolutiondate": None, "duedate": duedate, "labels": labels or [],
        "timeoriginalestimate": None,
        "fixVersions": [{"name": v} for v in (fix_versions or [])],
        "comment": {"comments": [{"created": iso(now - dt.timedelta(days=d, minutes=m)),
                                  "author": {"displayName": a, "accountId": a.lower().replace(" ", "")},
                                  "body": adf(t)} for d, m, a, t in (comments or [])]},
        "worklog": {"worklogs": [{"started": iso(now - dt.timedelta(days=d)),
                                  "author": {"displayName": a, "accountId": a.lower().replace(" ", "")},
                                  "timeSpentSeconds": s, "comment": adf(n)}
                                 for d, a, s, n in (worklogs or [])]},
    }, "changelog": {"histories": hist}}


def test_field_events():
    import dev_reports as dr
    raw = mkraw("F-1", "In Progress / Start Investigation", "In Progress", events=[
        (5, "Jane Doe", "status", "To Do", "In Progress / Start Investigation"),
        (4, "Jane Doe", "duedate", "", "2026-07-10"),
        (3, "Jane Doe", "Start date", "", "2026-07-01"),
        (2, "Jane Doe", "Flagged", "", "Impediment"),
        (1, "Jane Doe", "Sprint", "", "Sprint 11"),
    ])
    i = dr.load_dev_issues([raw])[0]
    kinds = [k for _t, _a, k, _f, _to in i.field_events]
    check("field events extracted", kinds == ["duedate", "startdate", "flag", "sprint"])
    import activity
    ev = activity.events_for(i)
    check("activity feed merges all kinds",
          {e.kind for e in ev} == {"status", "duedate", "startdate", "flag", "sprint"})


def test_checklist():
    import checklist
    import dev_reports as dr
    sset = st.load()
    sset["gates"]["worklogs_required"] = False
    sset["gates"]["due_dates_required"] = False
    st.save(sset)
    today = now.date()
    # Active ticket, commented today, moved to QA today WITH handoff comment
    good = mkraw("C-1", "Ready for QA (QA Env)", "In Progress", events=[
        (0, "Jane Doe", "status", "Development / In Design", "Ready for QA (QA Env)")],
        comments=[(0, 30, "Jane Doe", "handoff: steps to test")])
    # Active ticket, silent today, unmapped status
    bad = mkraw("C-2", "Mystery Status", "In Progress")
    issues = dr.load_dev_issues([good, bad])

    d = checklist.my_day(issues, "jane", today, dr._dev_match, now=now)
    rows = {r["issue"].key: r for r in d["rows"]}
    g = dict((c[0], c[2]) for c in rows["C-1"]["checks"])
    check("handoff comment pass", g["handoff_comment"] == "pass")
    check("comment today pass", g["comment_today"] == "pass")
    check("worklog gated -> na", g["worklog_today"] == "na")
    check("status mapped pass", g["status_mapped"] == "pass")
    b = dict((c[0], c[2]) for c in rows["C-2"]["checks"])
    check("unmapped status fails", b["status_mapped"] == "fail")
    check("no comment fails", b["comment_today"] == "fail")

    # Roll-up counts only active_dev/rework buckets: C-1 is qa_stage, C-2 unmapped.
    r = checklist.rollup(issues, today, now=now)
    check("rollup counts active buckets only", r["total"] == 0)
    active = mkraw("C-3", "Development / In Design", "In Progress",
                   comments=[(0, 0, "Jane Doe", "eod update")])
    r2 = checklist.rollup(dr.load_dev_issues([active]), today, now=now)
    check("rollup signal", r2["total"] == 1 and r2["signaled"] == 1 and r2["pct"] == 100)


def test_attention():
    import attention
    import dev_reports as dr
    s = st.load()
    s["silent_days"] = 2
    st.save(s)
    # Silent 12d in active_dev + aging + not-paused (all have a release so no_release stays quiet)
    silent = mkraw("A-1", "In Progress / Start Investigation", "In Progress", fix_versions=["R1"],
                   events=[(12, "Jane Doe", "status", "To Do", "In Progress / Start Investigation")])
    # Fresh ticket: entered its active status TODAY, commented today, has a release + due date
    fresh = mkraw("A-2", "In Progress / Start Investigation", "In Progress", fix_versions=["R1"],
                  duedate="2026-08-01",
                  events=[(0, "Jane Doe", "status", "To Do", "In Progress / Start Investigation")],
                  comments=[(0, 0, "Jane Doe", "on it")])
    # QA-parked (Tanvir case): threshold qa_stage=2, sitting 9d — but not "silent" (not active)
    parked = mkraw("A-3", "Ready for QA (QA Env)", "In Progress", fix_versions=["R1"], events=[
        (9, "QA Bob", "status", "Development / In Design", "Ready for QA (QA Env)")])
    issues = dr.load_dev_issues([silent, fresh, parked])
    d = attention.board(issues, now=now)
    by_key = {r["issue"].key: r for r in d["rows"]}
    check("fresh ticket not on board", "A-2" not in by_key)
    check("silent+aging stack", len(by_key["A-1"]["reasons"]) >= 2)
    kinds1 = {r["kind"] for r in by_key["A-1"]["reasons"]}
    check("silent reason", "silent" in kinds1)
    check("aging reason", "aging" in kinds1)
    kinds3 = {r["kind"] for r in by_key["A-3"]["reasons"]}
    check("QA-parked aging (Tanvir case)", "aging" in kinds3)
    check("QA-parked not silent", "silent" not in kinds3)
    check("severity sort worst first",
          d["rows"][0]["severity"] >= d["rows"][-1]["severity"])
    # boundary: exactly at threshold (Ready for QA = 2d) is NOT aging (> not >=)
    edge = mkraw("A-4", "Ready for QA (QA Env)", "In Progress", events=[
        (2, "QA Bob", "status", "Development / In Design", "Ready for QA (QA Env)")])
    d2 = attention.board(dr.load_dev_issues([edge]),
                         now=A.parse_ts(edge["changelog"]["histories"][0]["created"])
                         + dt.timedelta(days=2))
    check("boundary day not aging", all("aging" != r["kind"]
          for row in d2["rows"] for r in row["reasons"]))


# ---------------------------------------------------------------------------
# Phase 2 — QA handoff edges + investigator gaps
# ---------------------------------------------------------------------------

def test_qa_handoff():
    import dev_reports as dr
    import qa_handoff as qh
    # Jane hands off with comment+PR link; QA Bob returns it; Jane hands off again.
    raw = mkraw("Q-1", "Ready for QA (QA Env)", "In Progress", events=[
        (10, "Jane Doe", "status", "Development / In Design", "Ready for QA (QA Env)"),
        (8, "QA Bob", "status", "Ready for QA (QA Env)", "Reopen"),
        (5, "Jane Doe", "status", "Reopen", "Ready for QA (QA Env)"),
    ], comments=[
        (10, 60, "Jane Doe", "Handoff: see https://github.com/lifedata/x/pull/42 test steps inside"),
        (8, 2, "QA Bob", "fails on login step"),
    ])
    # A skip-RFQA edge: straight from active_dev into QA Testing (still a handoff).
    raw2 = mkraw("Q-2", "In QA Testing (QA Env)", "In Progress", events=[
        (3, "Sam Lee", "status", "Development / In Design", "In QA Testing (QA Env)")])
    issues = dr.load_dev_issues([raw, raw2])

    h = qh.handoff_feed(issues, match=dr._dev_match)
    check("three handoffs (incl. skip-RFQA edge)", len(h) == 3)
    jane_first = [x for x in h if x["issue"].key == "Q-1"][-1]
    check("handoff comment within window", jane_first["has_comment"] is True)
    check("PR url detected", jane_first["has_pr"] is True)
    check("pass result", jane_first["result"] == "Pass")
    sam = [x for x in h if x["issue"].key == "Q-2"][0]
    check("needs info when no comment", sam["result"] == "Needs info")

    r = qh.returned_feed(issues, match=dr._dev_match)
    check("one return", len(r) == 1 and r[0]["returned_by"] == "QA Bob")
    check("return reason captured", "fails on login" in r[0]["reason"])

    rates = qh.return_rates(issues)
    jane = [x for x in rates if x["developer"] == "Jane Doe"][0]
    check("return attributed to handoff author", jane["handoffs"] == 2 and jane["returns"] == 1)
    check("raw counts in rate label", "(1 of 2)" in jane["rate_label"])


def test_investigator_gaps():
    import app
    import dev_reports as dr
    import jira_client as jc
    raw = mkraw("G-1", "Development / In Design", "In Progress", created_d=40, events=[
        (30, "Jane Doe", "status", "To Do", "Development / In Design"),
        (2, "Jane Doe", "status", "Development / In Design", "Development / In Design")])
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: [raw]
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    c = app.app.test_client()
    h = c.get("/investigate?key=g-1").get_data(as_text=True)
    check("investigator resolves key case-insensitively", "G-1" in h)
    check("gap spacer rendered", "days — no activity" in h)
    check("stage ribbon rendered", "Active Dev" in h)
    check("deep link", "browse/G-1" in h)
    h2 = c.get("/investigate").get_data(as_text=True)
    check("investigator teaches without key", "Enter an issue key" in h2)


# ---------------------------------------------------------------------------
# Phase 3 — percentiles + flow/quality engines
# ---------------------------------------------------------------------------

def test_percentile():
    check("percentile empty", A.percentile([], 50) is None)
    check("median odd", A.percentile([1, 2, 100], 50) == 2)
    check("median robust to outlier", A.percentile([1, 2, 3, 4, 100], 50) == 3)
    check("p85 interpolates", abs(A.percentile([0, 10], 85) - 8.5) < 1e-9)


def test_flow_quality():
    import dev_reports as dr
    import flow_quality as fq
    done = mkraw("FL-1", "Done", "Done", typ="Bug", events=[
        (10, "Jane Doe", "status", "To Do", "Development / In Design"),
        (7, "Jane Doe", "status", "Development / In Design", "Ready for QA (QA Env)"),
        (6, "QA Bob", "status", "Ready for QA (QA Env)", "Reopen"),
        (4, "Jane Doe", "status", "Reopen", "Ready for QA (QA Env)"),
        (2, "QA Bob", "status", "Ready for QA (QA Env)", "Done")])
    done["fields"]["resolutiondate"] = iso(now - dt.timedelta(days=2))
    wip1 = mkraw("FL-2", "Development / In Design", "In Progress", assignee="Sam Lee", events=[
        (3, "Sam Lee", "status", "To Do", "Development / In Design")])
    wip2 = mkraw("FL-3", "Development / In Design", "In Progress", assignee="Sam Lee", events=[
        (1, "Sam Lee", "status", "To Do", "Development / In Design")])
    issues = dr.load_dev_issues([done, wip1, wip2])

    rows = fq.cycle_rows(issues, match=dr._dev_match)
    fl1 = [r for r in rows if r["issue"].key == "FL-1"][0]
    check("dev->qa hours", abs(fl1["dev_to_qa_h"] - 72.0) < 1)
    check("cycle hours", abs(fl1["cycle_h"] - 192.0) < 1)
    check("rework loop counted", fl1["rework_loops"] >= 1)
    check("stage segments computed", len(fl1["segments"]) >= 2)
    stats = fq.cycle_stats(rows)
    check("stats counts", stats["cycle"]["n"] == 1 and stats["dev_to_qa"]["n"] == 1)

    v = fq.multiple_active(issues)
    check("multiple-active violation", len(v) == 1 and v[0]["developer"] == "Sam Lee"
          and v[0]["count"] == 2)

    bugs = fq.bug_lens(issues, match=dr._dev_match)
    jane = [b for b in bugs if b["developer"] == "Jane Doe"][0]
    check("bug lens median hours", jane["median_hours"] is not None and jane["done"] == 1)
    check("bug lens raw counts", "(1 of 1)" in jane["rate_label"])

    tr = fq.return_trend(issues)
    check("return trend has data", sum(w["handoffs"] for w in tr) == 2
          and sum(w["returns"] for w in tr) == 1)

    b = fq.bottleneck(issues)
    check("bottleneck sorted desc", all(b[i]["median_days"] >= b[i+1]["median_days"]
                                        for i in range(len(b)-1)))


# ---------------------------------------------------------------------------
# Phase 4 — gated planning features
# ---------------------------------------------------------------------------

def test_planning_gated():
    import dev_reports as dr
    import planning as pl
    # Ticket with a pushed due date (2026-07-01 -> 2026-07-08 -> 2026-07-15) and
    # two start-date moves.
    raw = mkraw("P-1", "Development / In Design", "In Progress",
                duedate="2026-07-15", events=[
        (9, "Jane Doe", "status", "To Do", "Development / In Design"),
        (8, "Jane Doe", "duedate", "2026-07-01", "2026-07-08"),
        (4, "Jane Doe", "duedate", "2026-07-08", "2026-07-15"),
        (7, "Jane Doe", "Start date", "2026-06-20", "2026-06-25"),
        (3, "Jane Doe", "Start date", "2026-06-25", "2026-07-02"),
    ])
    issues = dr.load_dev_issues([raw])
    i = issues[0]
    sm = pl.slip_metrics(i)
    check("original due = first from-value", sm["original"] == dt.date(2026, 7, 1))
    check("push count", sm["pushes"] == 2)
    check("slip days", sm["slip_days"] == 14)
    rm = pl.reschedule_metrics(i)
    check("reschedule count", rm["count"] == 2)
    check("days pushed", rm["days_pushed"] == 12)

    # Gates OFF -> hygiene returns empty (ships dark)
    s = st.load()
    for g in s["gates"]:
        s["gates"][g] = False
    st.save(s)
    h = pl.hygiene(issues)
    check("hygiene dark when gates off",
          not h["missing"] and not h["slips"] and not h["reschedules"])
    # Flip gates -> features light up with zero deploy
    s["gates"]["due_dates_required"] = True
    s["gates"]["start_dates_required"] = True
    st.save(s)
    h = pl.hygiene(issues)
    check("slip table lights up", len(h["slips"]) == 1 and h["slips"][0]["slip_days"] == 14)
    check("reschedules light up", len(h["reschedules"]) == 1)
    check("missing start date flagged", len(h["missing"]) == 1
          and "start date" in h["missing"][0]["missing"])
    # attention gains Overdue + Missing dates reasons when gated on
    import attention
    over = mkraw("P-2", "Development / In Design", "In Progress",
                 duedate=(now - dt.timedelta(days=3)).date().isoformat(), events=[
        (2, "Jane Doe", "status", "To Do", "Development / In Design")])
    d = attention.board(dr.load_dev_issues([over]), now=now)
    kinds = {r["kind"] for row in d["rows"] for r in row["reasons"]}
    check("overdue reason when gate on", "overdue" in kinds)
    s["gates"]["due_dates_required"] = False
    s["gates"]["start_dates_required"] = False
    st.save(s)
    d2 = attention.board(dr.load_dev_issues([over]), now=now)
    kinds2 = {r["kind"] for row in d2["rows"] for r in row["reasons"]}
    check("overdue dark when gate off", "overdue" not in kinds2)


def test_disposition():
    import attention
    import dev_reports as dr
    # Over threshold (active_dev default 5d): entered status 10d ago; moved to
    # Backlog 3d ago -> dispositioned, but NOT within 48h of crossing (crossed 5d ago).
    raw = mkraw("D-1", "To Do", "To Do", events=[
        (10, "Jane Doe", "status", "To Do", "Development / In Design"),
        (3, "Jane Doe", "status", "Development / In Design", "Backlog"),
    ])
    i = dr.load_dev_issues([raw])[0]
    # simulate: currently in Backlog; disposition_state checks the CURRENT status
    # threshold — Backlog (todo) has none, so evaluate the pre-move ticket instead.
    raw2 = mkraw("D-2", "Development / In Design", "In Progress", events=[
        (10, "Jane Doe", "status", "To Do", "Development / In Design")])
    i2 = dr.load_dev_issues([raw2])[0]
    d = attention.disposition_state(i2, now)
    check("needs disposition after threshold", d and d["state"] == "needs_disposition")
    check("48h breach detected", d["overdue_48h"] is True)
    comp = attention.disposition_compliance(dr.load_dev_issues([raw2]), now)
    check("compliance counts flagged", comp["flagged"] == 1 and comp["within_48h"] == 0)


def test_dev_team_rules():
    """The seven Jira Ticket Rules mapped to checks."""
    import attention
    import checklist
    import dev_reports as dr
    import flow_quality as fq
    # reset gates to workflow state (worklogs + due dates required)
    s = st.load()
    s["gates"]["worklogs_required"] = True
    s["gates"]["due_dates_required"] = True
    st.save(s)

    # Rule 1: one active per lane. Sam has 2 in DEV lane + 1 in QA lane.
    dev1 = mkraw("R1-1", "In Progress / Start Investigation", "In Progress", assignee="Sam Lee",
                 events=[(0, "Sam Lee", "status", "To Do", "In Progress / Start Investigation")])
    dev2 = mkraw("R1-2", "Development / In Design", "In Progress", assignee="Sam Lee",
                 events=[(0, "Sam Lee", "status", "To Do", "Development / In Design")])
    qa1 = mkraw("R1-3", "In QA Testing (QA Env)", "In Progress", assignee="Sam Lee",
                events=[(0, "Sam Lee", "status", "Ready for QA (QA Env)", "In QA Testing (QA Env)")])
    v = fq.multiple_active(dr.load_dev_issues([dev1, dev2, qa1]))
    lanes = {r["lane"]: r["count"] for r in v}
    check("Rule 1: two in dev lane flagged", lanes.get("dev") == 2)
    check("Rule 1: single QA ticket not a violation", "qa" not in lanes)
    # two in the QA lane -> violation
    qa2 = mkraw("R1-4", "In Staging Testing", "In Progress", assignee="Sam Lee",
                events=[(0, "Sam Lee", "status", "Passed QA (Staging Ready)", "In Staging Testing")])
    qa3 = mkraw("R1-5", "In Staging Testing", "In Progress", assignee="Sam Lee",
                events=[(0, "Sam Lee", "status", "Passed QA (Staging Ready)", "In Staging Testing")])
    v2 = fq.multiple_active(dr.load_dev_issues([qa2, qa3]))
    check("Rule 1: two in staging lane flagged", v2 and v2[0]["lane"] == "staging" and v2[0]["count"] == 2)

    # Rule 3: pause active ticket at EOD. Carried overnight -> fail + attention.
    overnight = mkraw("R3-1", "Development / In Design", "In Progress", fix_versions=["R1"],
                      duedate="2026-08-01", worklogs=[(0, "Jane Doe", 3600, "x")],
                      comments=[(0, 0, "Jane Doe", "wip")],
                      events=[(2, "Jane Doe", "status", "To Do", "Development / In Design")])
    i = dr.load_dev_issues([overnight])[0]
    row = checklist.evaluate_ticket(i, now.date(), now=now)
    eod = dict((c[0], c[2]) for c in row["checks"])
    check("Rule 3: left active overnight fails", eod["eod_pause"] == "fail")
    d = attention.board(dr.load_dev_issues([overnight]), now=now)
    kinds = {r["kind"] for row in d["rows"] for r in row["reasons"]}
    check("Rule 3: not-paused attention reason", "not_paused" in kinds)
    # entered its active status today -> reminder (na), no attention reason
    today_active = mkraw("R3-2", "Development / In Design", "In Progress", fix_versions=["R1"],
                         duedate="2026-08-01", worklogs=[(0, "Jane Doe", 3600, "x")],
                         comments=[(0, 0, "Jane Doe", "wip")],
                         events=[(0, "Jane Doe", "status", "Pause Development / Design", "Development / In Design")])
    row2 = checklist.evaluate_ticket(dr.load_dev_issues([today_active])[0], now.date(), now=now)
    check("Rule 3: active-today is a reminder not a fail",
          dict((c[0], c[2]) for c in row2["checks"])["eod_pause"] == "na")

    # Rule 5: belongs to a release.
    no_rel = mkraw("R5-1", "Development / In Design", "In Progress", duedate="2026-08-01",
                   worklogs=[(0, "Jane Doe", 3600, "x")], comments=[(0, 0, "Jane Doe", "wip")],
                   events=[(0, "Jane Doe", "status", "To Do", "Development / In Design")])
    with_rel = mkraw("R5-2", "Development / In Design", "In Progress", fix_versions=["Web 0.12.0"],
                     duedate="2026-08-01", worklogs=[(0, "Jane Doe", 3600, "x")],
                     comments=[(0, 0, "Jane Doe", "wip")],
                     events=[(0, "Jane Doe", "status", "To Do", "Development / In Design")])
    r_no = checklist.evaluate_ticket(dr.load_dev_issues([no_rel])[0], now.date(), now=now)
    r_yes = checklist.evaluate_ticket(dr.load_dev_issues([with_rel])[0], now.date(), now=now)
    check("Rule 5: no release fails", dict((c[0], c[2]) for c in r_no["checks"])["has_release"] == "fail")
    check("Rule 5: has release passes", dict((c[0], c[2]) for c in r_yes["checks"])["has_release"] == "pass")
    dboard = attention.board(dr.load_dev_issues([no_rel]), now=now)
    check("Rule 5: no-release attention reason",
          any(r["kind"] == "no_release" for row in dboard["rows"] for r in row["reasons"]))

    # Rules 4 & 6: gates on -> worklog/due checks are live (not n-a)
    r4 = dict((c[0], c[2]) for c in r_yes["checks"])
    check("Rule 4: worklog check active when gated on", r4["worklog_today"] in ("pass", "fail"))
    check("Rule 6: due-date check active when gated on", r4["due_date"] in ("pass", "fail"))

    # Rule 7: apply_workflow re-applies mapping to an existing store
    s2 = st.load()
    s2["status_buckets"] = {}
    st.apply_workflow(s2)
    check("Rule 7: load-workflow remaps statuses",
          s2["status_buckets"].get("In Production Testing") == "qa_stage"
          and st.lane_of("In Production Testing") == "production")


def test_rollup_terminology():
    """Roll-up counts tickets in an ACTIVE or PAUSED status — active includes the
    testing lanes; queue states (Ready for QA) are excluded."""
    import checklist
    import dev_reports as dr
    s = st.load(); st.apply_workflow(s); st.save(s)  # ensure workflow mapping
    qa_active = mkraw("T-1", "In QA Testing (QA Env)", "In Progress", assignee="QA Bob",
                      comments=[(0, 0, "QA Bob", "testing")],
                      events=[(0, "QA Bob", "status", "Ready for QA (QA Env)", "In QA Testing (QA Env)")])
    queue = mkraw("T-2", "Ready for QA (QA Env)", "In Progress", assignee="QA Bob",
                  events=[(1, "Jane Doe", "status", "Development / In Design", "Ready for QA (QA Env)")])
    paused = mkraw("T-3", "Pause Development / Design", "In Progress", assignee="Jane Doe",
                   events=[(0, "Jane Doe", "status", "Development / In Design", "Pause Development / Design")])
    r = checklist.rollup(dr.load_dev_issues([qa_active, queue, paused]), now.date(), now=now)
    check("active testing lane counted in roll-up", r["total"] == 2)  # T-1 active + T-3 paused
    check("queue (Ready for QA) excluded from roll-up", r["total"] == 2)
    check("active QA ticket signaled", r["signaled"] >= 1)


def test_routes():
    import app
    import jira_client as jc
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: []
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.fetch_issues_by_time = lambda clause: []
    jc.fetch_working_set = lambda days=None: []
    jc.fetch_project_versions = lambda: []
    c = app.app.test_client()
    # redirects
    for old, new in [("/reports/daily", "/my-day/feed"), ("/reports/developers", "/qa"),
                     ("/dev-reports/timeline", "/investigate"), ("/dev-reports/bug-quality", "/quality")]:
        r = c.get(old)
        check(f"301 {old}", r.status_code == 301 and r.headers["Location"].endswith(new))
    r = c.get("/")
    check("landing redirects", r.status_code == 302)
    # new screens render
    for route in ["/my-day", "/my-day/rollup", "/my-day/feed", "/attention",
                  "/qa", "/flow", "/quality", "/planning", "/investigate", "/settings"]:
        r = c.get(route)
        check(f"200 {route}", r.status_code == 200)
    # kept routes still live
    for route in ["/reports/time-in-status", "/reports/release", "/exec/kpis"]:
        check(f"kept {route}", c.get(route).status_code == 200)


# ---------------------------------------------------------------------------
# Phase 5 — snapshots, trends, meeting mode, digest, sprint gating
# ---------------------------------------------------------------------------

def test_snapshots_and_trends():
    import app
    import dev_reports as dr
    import digest as dg
    import jira_client as jc
    import snapshots as sn
    raw = mkraw("S-1", "Development / In Design", "In Progress", events=[
        (3, "Jane Doe", "status", "To Do", "Development / In Design")],
        comments=[(0, 0, "Jane Doe", "daily update")])
    issues = dr.load_dev_issues([raw])
    agg = sn.compute_aggregates(issues, now=now)
    check("aggregate has no names", "Jane" not in str(agg))
    check("eod pct computed", agg["eod_signal_pct"] == 100)
    sn.take(issues, day=dt.date(2026, 6, 29), now=now)
    sn.take(issues, day=dt.date(2026, 7, 6), now=now)
    s = sn.series()
    check("two snapshots stored", len(s) == 2 and s[0]["day"] == "2026-07-06")
    wow = sn.week_over_week()
    check("wow delta computed", wow["eod_signal_pct"]["delta"] == 0)

    card = dg.build_card([], agg)
    check("digest card shape", card["attachments"][0]["content"]["type"] == "AdaptiveCard")
    check("digest without webhook returns False", dg.send([], agg) is False)

    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: [raw]
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    c = app.app.test_client()
    h = c.get("/exec").get_data(as_text=True)
    check("trends renders aggregates", "EOD signal" in h and "Meeting Mode" in h)
    hm = c.get("/exec?meeting=1").get_data(as_text=True)
    check("meeting mode hides names / shows distributions", "Distributions" in hm
          and "Jane Doe" not in hm)
    r = c.get("/tasks/snapshot")
    check("snapshot endpoint ok", r.status_code == 200 and r.get_json()["ok"] is True)
    # sprint gating from settings: gate off -> teaching empty state
    h = c.get("/reports/sprints").get_data(as_text=True)
    check("sprint teaching state when gated off", "board id" in h.lower())


if __name__ == "__main__":
    for fn in sorted(list(globals().items())):
        if fn[0].startswith("test_"):
            fn[1]()
    print(f"All v3 tests passed ({PASSED} checks).")
