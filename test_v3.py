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
import workflow as wf  # noqa: E402

now = A.now_utc()
PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, f"FAIL: {name}"
    PASSED += 1


def login_admin(c):
    """Log a test client in as an admin (registers the first account if needed)."""
    import auth
    if auth.user_count() == 0:
        c.post("/register", data={"email": "boss@lifedatacorp.com", "password": "secret123"})
    else:
        c.post("/login", data={"email": "boss@lifedatacorp.com", "password": "secret123"})
    return c


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
    check("active statuses seeded", len(fresh["active_statuses"]) == len(wf.ACTIVE)
          and all(s in fresh["active_statuses"] for s in
                  ("Development / In Design", "Investigation",
                   "Review and Testing", "In QA Testing (QA Env)"))
          and "Customer Feedback" not in fresh["active_statuses"])
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
    # Unmapped in-progress status, moved today (so it passes the My Day date filter)
    bad = mkraw("C-2", "Mystery Status", "In Progress",
                events=[(0, "Jane Doe", "status", "To Do", "Mystery Status")])
    issues = dr.load_dev_issues([good, bad])

    d = checklist.my_day(issues, "jane", today, today, dr._dev_match, now=now)
    rows = {r["issue"].key: r for r in d["rows"]}
    g = dict((c[0], c[2]) for c in rows["C-1"]["checks"])
    check("comment today pass", g["comment_today"] == "pass")
    check("status mapped pass", g["status_mapped"] == "pass")
    check("removed checks gone", "worklog_today" not in g and "handoff_comment" not in g
          and "eod_pause" not in g and "start_date" not in g and "blocked_reason" not in g
          and "not_over_threshold" not in g)
    check("kept 5 checks", set(g) == {"status_mapped", "comment_today", "due_date",
                                      "past_due", "has_release"})
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
    c = login_admin(app.app.test_client())
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


def test_active_time():
    """Active Time page's core metric: active-status seconds per (developer,
    ticket) inside a window. 'Active' = st.is_active_status(), the literal
    "someone is on it right now" set — narrower than the cycle-time stage
    bucket (fq.bottleneck's world) — and that distinction is the whole point
    of the feature, so the queued-status case below is the important check."""
    import dev_reports as dr
    import flow_quality as fq
    start, end = now - dt.timedelta(days=7), now

    # Entered Development 10 days ago, never left -> clipped to the window
    # length (7 days), not the full 10 it's actually been there.
    open_ended = mkraw("AT-1", "Development / In Design", "In Progress", created_d=20, events=[
        (10, "Jane Doe", "status", "To Do", "Development / In Design")])

    # Two different active statuses back to back inside the window -> summed
    # (3 days dev + 2 days QA = 5 days), not just the most recent one.
    two_active = mkraw("AT-2", "In QA Testing (QA Env)", "In Progress", assignee="Sam Lee",
                       created_d=20, events=[
        (5, "Sam Lee", "status", "To Do", "Development / In Design"),
        (2, "Sam Lee", "status", "Development / In Design", "In QA Testing (QA Env)")])

    # Paused for the middle 3 days of the window -> only the 2+1 dev days
    # on either side count.
    paused = mkraw("AT-3", "Development / In Design", "In Progress", assignee="Sam Lee",
                   created_d=20, events=[
        (6, "Sam Lee", "status", "To Do", "Development / In Design"),
        (4, "Sam Lee", "status", "Development / In Design", "Pause Development / Design"),
        (1, "Sam Lee", "status", "Pause Development / Design", "Development / In Design")])

    # Sitting in a hand-off/queue status the whole window: it's an
    # ACTIVE_STAGES stage (cycle-time still counts it), but NOT in
    # is_active_status -> must show zero here, not just "less than AT-1".
    queued = mkraw("AT-4", "Ready for QA (QA Env)", "To Do", assignee="Jane Doe",
                   created_d=20, events=[
        (12, "Jane Doe", "status", "Development / In Design", "Ready for QA (QA Env)")])

    # Same shape as AT-1, but the developer is hidden -> excluded entirely.
    ghost = mkraw("AT-5", "Development / In Design", "In Progress", assignee="Ghost Dev",
                  created_d=20, events=[
        (10, "Ghost Dev", "status", "To Do", "Development / In Design")])

    s = st.load()
    prior_hidden = s.get("hidden_developers", [])
    s["hidden_developers"] = prior_hidden + ["ghostdev"]
    st.save(s)
    try:
        issues = dr.load_dev_issues([open_ended, two_active, paused, queued, ghost])
        rows = fq.active_time(issues, start=start, end=end, match=dr.dev_match_exact)
    finally:
        s = st.load()
        s["hidden_developers"] = prior_hidden
        st.save(s)

    by_key = {r["issue"].key: r for r in rows}
    check("open-ended clipped to window length", abs(by_key["AT-1"]["active_seconds"] / 3600 - 168) < 1)
    check("two active statuses summed", abs(by_key["AT-2"]["active_seconds"] / 3600 - 120) < 1)
    check("paused segment excluded", abs(by_key["AT-3"]["active_seconds"] / 3600 - 72) < 1)
    check("queue status (not is_active_status) shows zero", "AT-4" not in by_key)
    check("hidden developer excluded", "AT-5" not in by_key)
    check("rows sorted by active time desc", all(
        rows[i]["active_seconds"] >= rows[i + 1]["active_seconds"] for i in range(len(rows) - 1)))


def test_concurrency_dedup_and_top_tickets():
    """The Time Spent dashboard's core new math: a developer's raw time (sum
    across tickets) vs. their de-duplicated time (max one ticket credited per
    instant) vs. the inflation between them. Verified against the exact case
    from the spec: 3 tickets active at once for 6h each is 18h raw, 6h real,
    12h inflated."""
    import dev_reports as dr
    import flow_quality as fq

    check("no overlap", fq._merge_intervals([]) == [])
    check("disjoint intervals stay separate",
          fq._merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)])
    check("overlapping intervals merge",
          fq._merge_intervals([(1, 5), (3, 7)]) == [(1, 7)])
    check("touching intervals merge (no gap between them)",
          fq._merge_intervals([(1, 3), (3, 5)]) == [(1, 5)])
    check("merges regardless of input order",
          fq._merge_intervals([(5, 7), (1, 3), (2, 6)]) == [(1, 7)])
    check("one interval nested inside another collapses to the outer one",
          fq._merge_intervals([(1, 10), (3, 4)]) == [(1, 10)])

    # 3 tickets, all opened at once, all still active -> fully overlapping.
    concurrent = dr.load_dev_issues([mkraw(
        f"CC-{n}", "Development / In Design", "In Progress", assignee="Sam Lee",
        created_d=10, events=[(0.25, "Sam Lee", "status", "To Do",
                               "Development / In Design")]) for n in range(3)])
    totals = fq.dev_time_totals(concurrent, start=now - dt.timedelta(days=7), end=now)
    sam = totals["Sam Lee"]
    check("raw sums all three tickets (~18h)", abs(sam["raw_seconds"] / 3600 - 18) < 0.2)
    check("dedup caps it at one ticket's worth (~6h)",
          abs(sam["dedup_seconds"] / 3600 - 6) < 0.2)
    check("inflated is exactly the overlap (~12h)",
          abs(sam["inflated_seconds"] / 3600 - 12) < 0.2)
    check("raw = dedup + inflated, always",
          abs(sam["raw_seconds"] - (sam["dedup_seconds"] + sam["inflated_seconds"])) < 1)

    # Two tickets worked back to back, no overlap -> no inflation at all.
    sequential = dr.load_dev_issues([
        mkraw("SQ-1", "Done", "Done", assignee="Jane Doe", created_d=10, events=[
            (6, "Jane Doe", "status", "To Do", "Development / In Design"),
            (4, "Jane Doe", "status", "Development / In Design", "Done")]),
        mkraw("SQ-2", "Done", "Done", assignee="Jane Doe", created_d=10, events=[
            (3, "Jane Doe", "status", "To Do", "Development / In Design"),
            (1, "Jane Doe", "status", "Development / In Design", "Done")])])
    jane = fq.dev_time_totals(sequential, start=now - dt.timedelta(days=7), end=now)["Jane Doe"]
    check("sequential work has zero inflation", jane["inflated_seconds"] < 1)
    check("raw equals dedup when nothing overlapped",
          abs(jane["raw_seconds"] - jane["dedup_seconds"]) < 1)

    # Scoping to one developer must not pull in someone else's overlap.
    mixed = concurrent + sequential
    scoped = fq.dev_time_totals(mixed, developer="Jane Doe",
                                start=now - dt.timedelta(days=7), end=now,
                                match=dr.dev_match_exact)
    check("scoping to one developer excludes the rest", set(scoped) == {"Jane Doe"})

    # Top tickets: ranked by total elapsed time regardless of who worked it,
    # and a handoff ticket sums correctly rather than splitting into two rows.
    handoff = mkraw("TT-1", "In QA Testing (QA Env)", "In Progress", assignee="Bob",
                    created_d=10, events=[
                        (6, "Alice", "status", "To Do", "Development / In Design"),
                        (3, "Alice", "assignee", "Alice", "Bob")])
    solo = mkraw("TT-2", "Development / In Design", "In Progress", assignee="Alice",
                created_d=10, events=[(1, "Alice", "status", "To Do", "Development / In Design")])
    top = fq.top_tickets(dr.load_dev_issues([handoff, solo]),
                         start=now - dt.timedelta(days=7), end=now)
    check("ranked with the busiest ticket first", top[0]["issue"].key == "TT-1")
    check("handoff ticket is one row, summed across both owners",
          abs(top[0]["seconds"] / 3600 - 144) < 1 and top[0]["people"] == 2)
    check("solo ticket totals correctly", abs(top[1]["seconds"] / 3600 - 24) < 1
          and top[1]["people"] == 1)
    check("limit is respected",
          len(fq.top_tickets(dr.load_dev_issues([handoff, solo]), limit=1)) == 1)


def test_developer_directory_matches_the_normal_dropdown():
    """Whoever shows up as a "developer" in Time-Spent Dashboards — the
    dev-summary widget's rows and the "Select a Developer" buttons — must be
    exactly auth.visible_developers(): the same set the Project/Developer
    dropdown offers on every other screen, minus anyone hidden in Settings.

    A developer whose only visible work is a ticket they've since handed off
    is never a CURRENT assignee of anything, so the dropdown doesn't know
    them either — they get no row and no button, even though their
    ownership-attributed time is real (dev_time_totals still computes it
    correctly; this is specifically about who gets LISTED)."""
    import auth
    import dev_reports as dr
    import jira_client as jc
    import screens_web as sw

    raw = mkraw("PD-1", "In QA Testing (QA Env)", "In Progress", assignee="Bob Second",
               created_d=20, events=[
                   (15, "Alice First", "status", "To Do", "Development / In Design"),
                   (10, "Alice First", "assignee", "Alice First", "Bob Second")])
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: [raw]
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}

    # ids_by_name still resolves her id from status-change authorship — that
    # remains useful for correctly consolidating a VISIBLE developer's own
    # past-owner appearances under one identity — it's just no longer what
    # decides who gets listed as a developer.
    ids = sw._ids_by_name()
    check("a past-only owner's id is still resolvable via status-change authorship",
          ids.get("Alice First") == "alicefirst")

    directory = sw._developer_directory()
    check("the past-only owner does NOT appear — she's not in the dropdown either",
          "Alice First" not in directory.values())
    check("the current assignee does appear", "Bob Second" in directory.values())
    check("directory keys are exactly auth.visible_developers()'s ids",
          set(directory) == {d["id"] for d in auth.visible_developers()})

    # Hiding a current assignee in Settings removes them from here too, the
    # same way it removes them from the dropdown.
    s = st.load()
    prior_hidden = s.get("hidden_developers", [])
    s["hidden_developers"] = prior_hidden + ["bobsecond"]
    st.save(s)
    try:
        check("a hidden current assignee is excluded",
              "Bob Second" not in sw._developer_directory().values())
    finally:
        s = st.load()
        s["hidden_developers"] = prior_hidden
        st.save(s)

    # End to end: the landing page's widget and button grid must agree with
    # the directory, not with the broader ownership-time computation.
    import app
    c = login_admin(app.app.test_client())
    h = c.get("/active-time").get_data(as_text=True)
    check("landing page never lists the past-only owner", "Alice First" not in h)
    check("landing page still lists the current assignee", "Bob Second" in h)

    # And her dashboard link is refused, not silently served — a stale or
    # hand-typed URL shouldn't reach someone the dropdown wouldn't offer.
    alice_id = ids["Alice First"]
    dh = c.get(f"/active-time?dev={alice_id}").get_data(as_text=True)
    check("her own dashboard link is refused",
          "No developer matches" in dh)


def test_elapsed_and_effort_stay_separate():
    """Elapsed time in a working status and booked worklog effort are different
    numbers answering different questions, and the screens must never let one
    stand in for the other: a ticket can sit in Development for a week while
    someone books four hours against it."""
    import dev_reports as dr
    import flow_quality as fq
    start, end = now - dt.timedelta(days=7), now

    raw = mkraw("EF-1", "Development / In Design", "In Progress", assignee="Jane Doe",
                created_d=20, events=[
                    (10, "Jane Doe", "status", "To Do", "Development / In Design")],
                worklogs=[(5, "Jane Doe", 4 * 3600, "actual work"),
                          (2, "Sam Lee", 2 * 3600, "helped out"),
                          (30, "Jane Doe", 9 * 3600, "long before the window")])
    issue = dr.load_dev_issues([raw])[0]

    row = fq.active_time([issue], start=start, end=end, match=dr.dev_match_exact)[0]
    check("elapsed is the full window, not the effort",
          abs(row["active_seconds"] / 3600 - 168) < 1)
    check("effort counts only this person's worklogs in the window",
          abs(row["logged_seconds"] / 3600 - 4) < 0.01)
    check("elapsed and effort are different numbers",
          row["active_seconds"] != row["logged_seconds"])

    check("ticket-wide effort spans everyone, whole life",
          abs(fq.logged_seconds(issue) / 3600 - 15) < 0.01)
    check("windowed ticket-wide effort excludes the old worklog",
          abs(fq.logged_seconds(issue, start=start, end=end) / 3600 - 6) < 0.01)
    check("effort filtered by person",
          abs(fq.logged_seconds(issue, "Sam Lee", "samlee") / 3600 - 2) < 0.01)

    # No worklogs at all must read as "none booked", never as zero effort
    # dressed up as a real measurement.
    bare = dr.load_dev_issues([mkraw(
        "EF-2", "Development / In Design", "In Progress", created_d=20, events=[
            (10, "Jane Doe", "status", "To Do", "Development / In Design")])])[0]
    check("no worklogs yields zero, not a crash", fq.logged_seconds(bare) == 0)

    import screens_web as sw
    # A dashboard card can legitimately show zero (e.g. a developer with no
    # overlapping tickets has zero inflation) — that has to read as a real
    # zero, not a dash implying "no data" nor an em dash at all (none of these
    # pages use one).
    check("zero renders as a real zero, not a dash", sw._dur(0) == "0h")
    check("durations read as days once past a day", sw._dur(168 * 3600) == "7d")
    check("short durations stay in hours", sw._dur(5 * 3600) == "5h")
    check("sub-hour durations stay in minutes", sw._dur(20 * 60) == "20m")
    check("day and hour remainder", sw._dur((48 + 3) * 3600) == "2d 3h")
    # Clock drift makes summed windows land a hair under a whole number of
    # days; the remainder must carry instead of rendering "6d 24h".
    check("rounding carries into days", sw._dur(168 * 3600 - 4) == "7d")


def test_dev_dashboard_range_control():
    """The dev dashboard's 7/14/30/Custom control: presets need no date boxes
    (the pill label already names the window), but Custom's date inputs must
    always show exactly the range on screen, and re-submitting them unchanged
    must land on the identical window — the same principle the roster page's
    date boxes follow, applied to this page's own control."""
    import re

    import app
    import jira_client as jc
    import screens_web as sw

    raws = [mkraw("WN-1", "Development / In Design", "In Progress", assignee="Jane Doe",
                  created_d=40, events=[(20, "Jane Doe", "status", "To Do",
                                         "Development / In Design")])]
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: raws
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.report_projects = lambda: [{"key": "LIFEDATAV2", "name": "LIFEDATAV2"}]
    jc.report_project_keys = lambda: ["LIFEDATAV2"]
    jc.configured_projects = lambda: ["LIFEDATAV2"]
    c = login_admin(app.app.test_client())
    import auth
    dev_id = next(d["id"] for d in auth.all_developers() if d["name"] == "Jane Doe")

    def get(qs=""):
        return c.get(f"/active-time?dev={dev_id}{qs}").get_data(as_text=True)

    h = get()
    check("default range is 7 days", "past 7 days" in h)
    check("no developer selector on the dev dashboard", "name=\"developer\"" not in h)
    check("project selector is present", "name=\"project\"" in h)
    check("presets show no date inputs", 'name="start"' not in h.split("Tickets worked")[0]
          or 'type="date"' not in h.split("Tickets worked")[0])

    h14 = get("&range=14d")
    check("switching preset updates the label", "past 14 days" in h14)
    check("switching preset marks the right pill active",
          re.search(r'pill ok" href="\?[^"]*range=14d', h14))

    # The bug this test exists for: clicking "Custom" the first time carries
    # no dates yet. That must still land in custom mode with the picker
    # visible and pre-filled — not silently fall back to the 7-day preset
    # with the picker never appearing.
    hbare = get("&range=custom")
    check("clicking Custom with no dates yet still selects the Custom pill",
          re.search(r'pill ok" href="\?[^"]*range=custom', hbare))
    check("the date picker form appears on the first click",
          "<label>From" in hbare and 'type="date"' in hbare)
    check("the picker is pre-filled with a real window, not left blank",
          re.search(r'name="start" value="\d{4}-\d{2}-\d{2}"', hbare)
          and re.search(r'name="end" value="\d{4}-\d{2}-\d{2}"', hbare))

    hc = get("&range=custom&start=2026-08-01&end=2026-08-10")
    check("custom start box echoes exactly", 'name="start" value="2026-08-01"' in hc)
    check("custom end box echoes exactly", 'name="end" value="2026-08-10"' in hc)
    # end is exclusive internally; the label must name the last day the page
    # actually covers, not the day after it.
    check("label names the last day included, not the day after",
          "Aug 1 → Aug 10" in hc and "Aug 11" not in hc)

    hc2 = get("&range=custom&start=2026-08-05&end=2026-08-05")
    check("a single-day custom range reads as one day", "on Aug 5" in hc2)

    start, end, key = sw._resolve_range("7d")
    check("resolved default window is midnight aligned", start.hour == 0 and end.hour == 0)
    check("7d really spans 7 days", (end - start).days == 7)
    check("unknown range key falls back to the default", key == "7d")
    cstart, cend, ckey = sw._resolve_range("custom", "2026-08-01", "2026-08-10")
    check("custom end is exclusive (spans the 10th fully)",
          cend == dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc) and ckey == "custom")

    # "custom" is a mode, not just a pair of dates — the exact bug fixed here.
    bstart, bend, bkey = sw._resolve_range("custom")
    check("custom with no dates yet still resolves to the custom mode",
          bkey == "custom")
    check("and still returns a sane, midnight-aligned fallback window",
          bstart.hour == 0 and bend.hour == 0 and bend > bstart)

    check("_dash_link preserves dev/project across a range switch",
          sw._dash_link(dev_id, "LIFEDATAV2", "30d") ==
          f"dev={dev_id}&project=LIFEDATAV2&range=30d")
    check("_dash_link carries custom dates only when given",
          sw._dash_link(dev_id, "LIFEDATAV2", "custom", "2026-08-01", "2026-08-10") ==
          f"dev={dev_id}&project=LIFEDATAV2&range=custom&start=2026-08-01&end=2026-08-10")


def test_ticket_row_shows_stage_composition():
    """Each ticket row's bar shows what stage(s) it was in while this developer
    worked it, as real segments of the row's own bar — not a separate caption
    line below it. A single-status ticket is one full segment; a ticket the
    dev carried across a handoff (dev work, then picked back up after QA sent
    it back) shows its real composition, and the segments must reconcile with
    the row's own total."""
    import dev_reports as dr
    import flow_quality as fq
    import screens_web as sw

    single = mkraw("SG-1", "Development / In Design", "In Progress", assignee="Jane Doe",
                   created_d=10, events=[(3, "Jane Doe", "status", "To Do",
                                          "Development / In Design")])
    mixed = mkraw("MX-1", "In QA Testing (QA Env)", "In Progress", assignee="Jane Doe",
                  created_d=10, events=[
                      (5, "Jane Doe", "status", "To Do", "Development / In Design"),
                      (2, "Jane Doe", "status", "Development / In Design",
                       "In QA Testing (QA Env)")])
    issues = dr.load_dev_issues([single, mixed])
    rows = fq.active_time(issues, match=dr.dev_match_exact)
    groups = sw._group_by_dev(rows)
    g = groups[0]
    tickets = {t["issue"].key: t for t in g["tickets"]}

    check("a single-status ticket is one full segment",
          len(tickets["SG-1"]["segments"]) == 1
          and tickets["SG-1"]["segments"][0]["pct"] == 100)
    check("a ticket worked across a handoff shows both stages",
          {s["status"] for s in tickets["MX-1"]["segments"]}
          == {"Development / In Design", "In QA Testing (QA Env)"})
    check("segment shares sum to 100% of the ticket's own total",
          abs(sum(s["pct"] for s in tickets["MX-1"]["segments"]) - 100) < 0.1)
    check("the legend lists every distinct status across the developer's tickets",
          {s for s, _c in g["statuses_seen"]}
          == {"Development / In Design", "In QA Testing (QA Env)"})


def test_no_em_dashes_on_time_spent_pages():
    """A plain style rule for this feature: no em dashes anywhere on the
    landing dashboard, a developer's dashboard, or the not-found state -
    including a dashboard card showing a real, legitimate zero (most
    developers have zero inflation), which must read as "0h", never as a dash
    implying "no data"."""
    import app
    import auth
    import jira_client as jc

    raw = mkraw("ED-1", "Development / In Design", "In Progress", assignee="Jane Doe",
               created_d=10, events=[(2, "Jane Doe", "status", "To Do",
                                      "Development / In Design")])
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: [raw]
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.report_projects = lambda: [{"key": "LIFEDATAV2", "name": "LIFEDATAV2"}]
    jc.report_project_keys = lambda: ["LIFEDATAV2"]
    jc.configured_projects = lambda: ["LIFEDATAV2"]
    c = login_admin(app.app.test_client())

    landing = c.get("/active-time").get_data(as_text=True)
    check("no em dash on the landing dashboard", "—" not in landing)

    jane_id = next(d["id"] for d in auth.all_developers() if d["name"] == "Jane Doe")
    dev = c.get(f"/active-time?dev={jane_id}").get_data(as_text=True)
    check("no em dash on a developer's dashboard", "—" not in dev)
    check("zero inflation reads as a real zero on the card", "0h" in dev)

    missing = c.get("/active-time?dev=NOPE-999").get_data(as_text=True)
    check("no em dash on the not-found state", "—" not in missing)


def test_long_ticket_list_stays_readable():
    """A developer can easily touch 14 tickets in a week. The per-person list
    has to stay scannable, and the shared bar scale only works if the visible
    rows are of comparable size — so the cut follows the data, not a fixed row
    count."""
    import screens_web as sw

    def split(*seconds):
        ts = [{"active_seconds": s} for s in seconds]
        return sw._split_head_tail(ts, sum(seconds))

    h, t = split(*([70 * 3600, 30 * 3600] + [3600] * 12))
    check("a concentrated week collapses to the few that mattered",
          len(h) == 3 and len(t) == 11)

    h, t = split(*([4 * 3600] * 14))
    check("an evenly fragmented week shows more, capped", len(h) == 8 and len(t) == 6)

    h, t = split(5 * 3600, 3 * 3600, 1 * 3600)
    check("a short list is never collapsed", len(h) == 3 and not t)

    h, t = split(10 * 3600, 8 * 3600, 6 * 3600, 3600)
    check("a tail of one is absorbed rather than hidden behind a click",
          len(h) == 4 and not t)

    h, t = split(8 * 3600)
    check("a single ticket needs no tail", len(h) == 1 and not t)

    # Head and tail must together account for everything — a ticket silently
    # falling out of both would be worse than a long page.
    for case in ([70 * 3600, 30 * 3600] + [3600] * 12, [4 * 3600] * 14):
        h, t = split(*case)
        check("every ticket lands in head or tail", len(h) + len(t) == len(case))

    # End to end, on the dev dashboard (where the per-ticket list now lives):
    # 14 tickets render a folded tail, and the hidden ones are still reachable
    # in the markup rather than dropped.
    import app
    import auth
    import jira_client as jc
    raws = [mkraw(f"LT-{n:02d}", "Development / In Design", "In Progress",
                  assignee="Marcus Chen", created_d=30,
                  events=[((70 if n == 0 else 0.2), "Marcus Chen", "status",
                           "To Do", "Development / In Design")])
            for n in range(14)]
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: raws
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.report_projects = lambda: [{"key": "LIFEDATAV2", "name": "LIFEDATAV2"}]
    jc.report_project_keys = lambda: ["LIFEDATAV2"]
    jc.configured_projects = lambda: ["LIFEDATAV2"]
    c = login_admin(app.app.test_client())
    dev_id = next(d["id"] for d in auth.all_developers() if d["name"] == "Marcus Chen")
    html = c.get(f"/active-time?dev={dev_id}").get_data(as_text=True)
    check("the tail is folded behind a disclosure", "more tickets" in html)
    check("folded tickets are still in the page, not dropped",
          all(f"LT-{n:02d}" in html for n in range(14)))
    check("every ticket still appears", html.count("LT-13") >= 1)


def test_roles_on_time_spent_dashboards():
    """Time-Spent Dashboards is admin-only while it is still being evaluated:
    an admin reaches the landing dashboard and any developer's own dashboard;
    an employee cannot reach either, or a nav link to them.

    Opening this up later needs more than the usual two-line change, because
    unlike the other screens this page isn't scoped by parse_filters — the dev
    dashboard is selected by a raw ?dev= id, so an employee's own ?dev= would
    need to be checked against their linked developer explicitly (see the note
    in app.py)."""
    import app
    import auth
    import jira_client as jc

    raws = [
        mkraw("RL-1", "Development / In Design", "In Progress", assignee="Jane Doe",
              created_d=20, events=[(5, "Jane Doe", "status", "To Do", "Development / In Design")]),
        mkraw("RL-2", "Development / In Design", "In Progress", assignee="Sam Lee",
              created_d=20, events=[(4, "Sam Lee", "status", "To Do", "Development / In Design")]),
    ]
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: raws
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.report_projects = lambda: [{"key": "LIFEDATAV2", "name": "LIFEDATAV2"}]
    jc.report_project_keys = lambda: ["LIFEDATAV2"]
    jc.configured_projects = lambda: ["LIFEDATAV2"]

    admin = login_admin(app.app.test_client())
    h = admin.get("/active-time").get_data(as_text=True)
    check("admin lands on the new dashboard title", "Time-Spent Dashboards" in h)
    check("admin sees both developers in the summary widget",
          "Jane Doe" in h and "Sam Lee" in h)
    check("admin nav offers the renamed page", "Time-Spent Dashboards" in h
          and "/active-time" in h)

    jane_id = next(d["id"] for d in auth.all_developers() if d["name"] == "Jane Doe")
    dh = admin.get(f"/active-time?dev={jane_id}").get_data(as_text=True)
    check("admin reaches Jane's own dashboard", "Jane Doe" in dh.split("<h1>")[1][:40])
    check("Jane's dashboard doesn't show Sam's ticket", "RL-2" not in dh)

    # Time-Spent Dashboards is admin-only for now: employees must not reach
    # the landing page, a dev dashboard, or a nav link to either.
    emp = app.app.test_client()
    emp.post("/register", data={"email": "jane@lifedatacorp.com", "password": "secret123",
                                "developer_id": jane_id, "developer_name": "Jane Doe"})
    u = auth.get_user("jane@lifedatacorp.com")
    check("employee registered and linked",
          u and u["role"] != "admin" and u.get("developer_id") == jane_id)

    r = emp.get("/active-time")
    check("employee is redirected away from the landing dashboard",
          r.status_code in (301, 302) and "/my-day" in r.headers.get("Location", ""))
    r2 = emp.get(f"/active-time?dev={jane_id}")
    check("employee is redirected away from even their own dev dashboard",
          r2.status_code in (301, 302) and "/my-day" in r2.headers.get("Location", ""))
    check("employee nav does not offer it",
          "/active-time" not in emp.get("/my-day").get_data(as_text=True))
    check("employee still blocked from Flow",
          emp.get("/flow").status_code in (301, 302))
    check("employee keeps the screens they already had",
          emp.get("/my-day").status_code == 200)


def test_ticket_active_time_by_person():
    """The ticket view's metric: a ticket that changed hands splits its active
    time between the people who actually held it, instead of dumping all of it
    on whoever happens to be assigned now."""
    import dev_reports as dr
    import flow_quality as fq

    # In Development for 15 days straight. Alice held it for the first 5,
    # Bob for the last 10. Bob is the current assignee.
    handoff = dr.load_dev_issues([mkraw(
        "HO-1", "Development / In Design", "In Progress", assignee="Bob Second",
        created_d=20, events=[
            (15, "Alice First", "status", "To Do", "Development / In Design"),
            (10, "Alice First", "assignee", "Alice First", "Bob Second")])])[0]

    people = {p["person"]: p for p in fq.ticket_active_time(handoff)}
    check("both owners credited", set(people) == {"Alice First", "Bob Second"})
    check("first owner gets their stretch", abs(people["Alice First"]["hours"] - 120) < 1)
    check("current assignee gets only theirs", abs(people["Bob Second"]["hours"] - 240) < 1)
    check("shares sum to 100", abs(sum(p["pct"] for p in people.values()) - 100) < 0.2)
    check("per-status split kept",
          "Development / In Design" in people["Alice First"]["statuses"])
    check("only current assignee carries an id",
          people["Bob Second"]["person_id"] == "bobsecond"
          and people["Alice First"]["person_id"] == "")

    # The roster view must agree with the ticket view for the same ticket —
    # two views of one number disagreeing is worse than either being absent.
    roster = {r["developer"]: r for r in
              fq.active_time([handoff], match=dr.dev_match_exact)}
    check("roster splits the same way", set(roster) == {"Alice First", "Bob Second"}
          and abs(roster["Alice First"]["active_seconds"]
                  - people["Alice First"]["seconds"]) < 1)

    # Ownership spans must tile the whole life even when the ticket spent time
    # unassigned, or the intersection silently loses that time.
    orphan = dr.load_dev_issues([mkraw(
        "HO-2", "Development / In Design", "In Progress", assignee="Bob Second",
        created_d=20, events=[
            (15, "Alice First", "status", "To Do", "Development / In Design"),
            (12, "Alice First", "assignee", "Alice First", ""),
            (6, "Bob Second", "assignee", "", "Bob Second")])])[0]
    spans = fq.ownership_spans(orphan)
    check("spans tile without gaps", all(spans[i][2] == spans[i + 1][1]
                                         for i in range(len(spans) - 1)))
    names = {p["person"] for p in fq.ticket_active_time(orphan)}
    check("unassigned stretch kept, not dropped", "Unassigned" in names)
    total = sum(p["seconds"] for p in fq.ticket_active_time(orphan))
    check("split conserves the ticket's total active time",
          abs(total / 3600 - 360) < 1)

    # Blocks are the shared primitive: the per-person totals must be exactly
    # the sum of the blocks drawn on the timeline, or the picture and the
    # numbers on the same page would disagree.
    blocks = fq.ticket_active_blocks(handoff)
    check("blocks in time order", all(blocks[i][2] <= blocks[i + 1][2]
                                      for i in range(len(blocks) - 1)))
    per_block = {}
    for owner, _s, lo, hi in blocks:
        per_block[owner] = per_block.get(owner, 0) + (hi - lo).total_seconds()
    check("blocks reconcile with totals", all(
        abs(per_block[p["person"]] - p["seconds"]) < 1 for p in people.values()))


# ---------------------------------------------------------------------------
# Phase 4 — gated attention date rules
# ---------------------------------------------------------------------------

def test_attention_date_gates():
    import attention
    import dev_reports as dr
    # Past-due is raised, and stays on even when the due-date gate is off (it
    # mirrors My Day's ungated "Past due date" check); the gated "missing dates"
    # reason follows the gate.
    s = st.load()
    s["gates"]["due_dates_required"] = True
    s["gates"]["start_dates_required"] = True
    st.save(s)
    over = mkraw("P-2", "Development / In Design", "In Progress",
                 duedate=(now - dt.timedelta(days=3)).date().isoformat(), events=[
        (2, "Jane Doe", "status", "To Do", "Development / In Design")])
    kinds = {r["kind"] for row in attention.board(dr.load_dev_issues([over]), now=now)["rows"]
             for r in row["reasons"]}
    check("past-due reason raised", "past_due" in kinds)
    s["gates"]["due_dates_required"] = False
    s["gates"]["start_dates_required"] = False
    st.save(s)
    kinds2 = {r["kind"] for row in attention.board(dr.load_dev_issues([over]), now=now)["rows"]
              for r in row["reasons"]}
    check("past due stays on when the due-date gate is off", "past_due" in kinds2)
    check("missing-dates dark when gate off", "dates" not in kinds2)


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
    d = attention.board(dr.load_dev_issues([overnight]), now=now)
    kinds = {r["kind"] for row in d["rows"] for r in row["reasons"]}
    # Rule 3 (pause at end of day) is retired: it flagged every legitimate
    # multi-day task, and its My Day counterpart was already removed.
    check("Rule 3: not-paused reason retired", "not_paused" not in kinds)

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


def test_auth():
    import app
    import auth
    import jira_client as jc
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: [
        {"fields": {"assignee": {"displayName": "Dev One", "accountId": "d1"},
                    "status": {"name": "To Do", "statusCategory": {"name": "To Do"}}}}]
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    c = app.app.test_client()
    # unauthenticated -> redirect to login
    check("guard redirects to login", c.get("/attention").status_code == 302)
    # Snapshot endpoint needs no login (schedulers can't sign in) but DOES need
    # the shared token — without it anyone could trigger pulls / spam the digest.
    check("snapshot endpoint rejects missing token", c.post("/tasks/snapshot").status_code == 403)
    check("snapshot endpoint rejects wrong token",
          c.post("/tasks/snapshot?token=nope").status_code == 403)
    check("snapshot endpoint accepts the token",
          c.post(f"/tasks/snapshot?token={auth.snapshot_token()}").status_code in (200, 500))
    # first account is admin
    c.post("/register", data={"email": "boss@lifedatacorp.com", "password": "secret123"})
    check("first user is admin", auth.get_user("boss@lifedatacorp.com")["role"] == "admin")
    check("admin reaches settings", c.get("/settings").status_code == 200)
    # employee self-register + permanent link
    ce = app.app.test_client()
    check("register warns permanent", "permanently linked" in ce.get("/register").get_data(as_text=True))
    ce.post("/register", data={"email": "d1@lifedatacorp.com", "password": "secret123",
                               "developer_id": "d1", "developer_name": "Dev One"})
    check("employee linked to dev", auth.get_user("d1@lifedatacorp.com")["developer_id"] == "d1")
    check("employee blocked from settings", ce.get("/settings").status_code == 403)
    check("employee blocked from rollup", ce.get("/my-day/rollup").status_code == 403)
    # duplicate dev + short password rejected
    check("dup dev rejected", "already linked" in app.app.test_client().post(
        "/register", data={"email": "x@lifedatacorp.com", "password": "secret123",
                           "developer_id": "d1", "developer_name": "Dev One"}).get_data(as_text=True))
    check("short password rejected", "at least 8" in app.app.test_client().post(
        "/register", data={"email": "y@lifedatacorp.com", "password": "abc",
                           "developer_id": "d1"}).get_data(as_text=True))
    # login / bad login
    cx = app.app.test_client()
    check("bad login fails", "Incorrect" in cx.post(
        "/login", data={"email": "boss@lifedatacorp.com", "password": "nope"}).get_data(as_text=True))
    check("good login redirects", cx.post(
        "/login", data={"email": "boss@lifedatacorp.com", "password": "secret123"}).status_code == 302)


def test_routes():
    import app
    import auth
    import jira_client as jc
    jc.fetch_dev_dataset = lambda project=None, lookback_days=None: []
    jc.detect_custom_fields = lambda: {"story_points": None, "sprint": None, "start_date": None}
    jc.fetch_issues_by_time = lambda clause: []
    jc.fetch_working_set = lambda days=None: []
    jc.fetch_project_versions = lambda: []
    # log in as admin so the guard lets route smoke-tests through
    c = app.app.test_client()
    if auth.user_count() == 0:
        c.post("/register", data={"email": "smoke@lifedatacorp.com", "password": "secret123"})
    else:
        c.post("/login", data={"email": "boss@lifedatacorp.com", "password": "secret123"})
    # redirects
    for old, new in [("/reports/daily", "/my-day/feed"), ("/reports/developers", "/qa"),
                     ("/dev-reports/timeline", "/investigate"), ("/dev-reports/bug-quality", "/quality")]:
        r = c.get(old)
        check(f"301 {old}", r.status_code == 301 and r.headers["Location"].endswith(new))
    r = c.get("/")
    check("landing redirects", r.status_code == 302)
    # new screens render
    for route in ["/my-day", "/my-day/rollup", "/my-day/feed", "/attention",
                  "/qa", "/flow", "/quality", "/release", "/investigate", "/settings"]:
        r = c.get(route)
        check(f"200 {route}", r.status_code == 200)
    # kept routes still live
    for route in ["/reports/time-in-status", "/exec/kpis"]:
        check(f"kept {route}", c.get(route).status_code == 200)
    # old release URL now redirects to the top-level /release page
    rr = c.get("/reports/release")
    check("/reports/release -> /release", rr.status_code in (301, 302)
          and rr.headers["Location"].endswith("/release"))


# ---------------------------------------------------------------------------
# Phase 5 — snapshots, trends, meeting mode, digest, sprint gating
# ---------------------------------------------------------------------------

def test_snapshots_and_trends():
    import app
    import dev_reports as dr
    import digest as dg
    import jira_client as jc
    import snapshots as sn
    try:  # isolate: other tests may have written a snapshot via /tasks/snapshot
        os.remove(os.environ["SNAPSHOT_DB_PATH"])
    except OSError:
        pass
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
    c = login_admin(app.app.test_client())
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
