"""
Unit tests for the v3 platform: settings store, checklist engine, attention
reasons, handoff edges, timeline gaps, redirects. Fixtures follow the existing
pattern (LIFEDATAV2-shaped changelogs). Run: python3 test_v3.py
"""

import datetime as dt
import os
import tempfile

os.environ.setdefault("APP_CONFIG_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="jira_v3_test_"), "settings.json"))

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
    check("seeds from config.py", s["status_buckets"].get("In Progress / Start Investigation") == "active_dev")
    check("seed rework", s["status_buckets"].get("Reopen") == "rework")
    check("seed qa", s["status_buckets"].get("Ready for QA (QA Env)") == "qa_stage")
    check("seed staging->qa", s["status_buckets"].get("In Staging Testing") == "qa_stage")
    check("gates default off", not any(s["gates"].values()))

    check("bucket_of mapped", st.bucket_of("Reopen") == "rework")
    check("bucket_of unmapped is None", st.bucket_of("Weird New Status") is None)
    check("bucket_of done category fallback", st.bucket_of("Weird Done", "Done") == "done")

    check("threshold bucket default", st.threshold_for("Ready for QA (QA Env)") == 3)
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
          events=None, comments=None, worklogs=None, duedate=None, labels=None):
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
    # Silent 5d in active_dev + aging (threshold 5d default, in status 12d)
    silent = mkraw("A-1", "In Progress / Start Investigation", "In Progress", events=[
        (12, "Jane Doe", "status", "To Do", "In Progress / Start Investigation")])
    silent["fields"]["updated"] = iso(now - dt.timedelta(days=5))
    # Fresh ticket: commented today, within threshold
    fresh = mkraw("A-2", "In Progress / Start Investigation", "In Progress", events=[
        (1, "Jane Doe", "status", "To Do", "In Progress / Start Investigation")],
        comments=[(0, 0, "Jane Doe", "on it")])
    # QA-parked (Tanvir case): threshold qa_stage=3, sitting 9d — but not "silent" (not active_dev)
    parked = mkraw("A-3", "Ready for QA (QA Env)", "In Progress", events=[
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
    # boundary: exactly at threshold is NOT aging (> not >=)
    edge = mkraw("A-4", "Ready for QA (QA Env)", "In Progress", events=[
        (3, "QA Bob", "status", "Development / In Design", "Ready for QA (QA Env)")])
    d2 = attention.board(dr.load_dev_issues([edge]),
                         now=edge and (A.parse_ts(edge["changelog"]["histories"][0]["created"])
                                       + dt.timedelta(days=3)))
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
    for route in ["/reports/time-in-status", "/reports/release", "/exec"]:
        check(f"kept {route}", c.get(route).status_code == 200)


if __name__ == "__main__":
    for fn in sorted(list(globals().items())):
        if fn[0].startswith("test_"):
            fn[1]()
    print(f"All v3 tests passed ({PASSED} checks).")
