"""
test_reports.py — run with:  python3 test_reports.py
Validates the changelog analytics and report builders on fixtures modeled from real
LIFEDATAV2 tickets. No network required.
"""
import datetime as dt
import analytics as A
import config as cfg
import reports as R

NOW = dt.datetime(2026, 6, 10, 18, 0, tzinfo=dt.timezone.utc)


def _issue(key, typ, prio, assignee, status, cat, created, resolved, fixv, hist):
    return {"key": key, "changelog": {"histories": hist}, "fields": {
        "summary": key, "issuetype": {"name": typ}, "priority": {"name": prio},
        "assignee": {"displayName": assignee},
        "status": {"name": status, "statusCategory": {"name": cat}},
        "created": created, "resolutiondate": resolved,
        "fixVersions": [{"name": fixv}] if fixv else []}}


def test_analytics_cycle_and_pause():
    cl = [
        {"created": "2026-06-09T07:53:47-0700", "items": [{"field": "status", "fromString": "Development / In Design", "toString": "Development Completed"}]},
        {"created": "2026-06-08T05:48:58-0700", "items": [{"field": "status", "fromString": "Pause Development / Design", "toString": "Development / In Design"}]},
        {"created": "2026-05-11T03:01:43-0700", "items": [{"field": "status", "fromString": "Development / In Design", "toString": "Pause Development / Design"}]},
        {"created": "2026-05-11T03:01:37-0700", "items": [{"field": "status", "fromString": "In Progress / Start Investigation", "toString": "Development / In Design"}]},
        {"created": "2026-05-11T01:37:40-0700", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress / Start Investigation"}]},
    ]
    tl = A.analyze(cl, "2026-05-11T01:36:59-0700", "2026-06-09T07:53:47-0700", "Development Completed", "Done")
    assert 29.0 <= tl.cycle_days <= 29.6
    assert tl.days_in_stage(cfg.STAGE_PAUSED) > 27
    assert tl.reopened_count == 0


def test_reports_end_to_end():
    h = [
        {"created": "2026-06-09T10:00:00-0700", "author": {"displayName": "Tanvir Hossain"}, "items": [{"field": "status", "fromString": "In QA Testing (QA Env)", "toString": "Close"}]},
        {"created": "2026-06-08T10:00:00-0700", "author": {"displayName": "Tanvir Hossain"}, "items": [{"field": "status", "fromString": "Ready for QA (QA Env)", "toString": "In QA Testing (QA Env)"}]},
        {"created": "2026-06-07T10:00:00-0700", "author": {"displayName": "Md Hasan"}, "items": [{"field": "status", "fromString": "Development / In Design", "toString": "Ready for QA (QA Env)"}]},
        {"created": "2026-06-06T10:00:00-0700", "author": {"displayName": "Md Hasan"}, "items": [{"field": "status", "fromString": "To Do", "toString": "Development / In Design"}]},
    ]
    data = [
        _issue("LD-1", "Task", "Medium", "Md Hasan", "Close", "Done", "2026-06-05T09:00:00-0700", "2026-06-09T10:00:00-0700", "R1", h),
        _issue("LD-3", "Bug", "Highest", "Sashoto Seeam", "In Progress / Start Investigation", "In Progress", "2026-03-01T09:00:00-0700", None, "R1",
               [{"created": "2026-04-01T09:00:00-0700", "author": {"displayName": "Sashoto Seeam"}, "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress / Start Investigation"}]}]),
    ]
    issues = R.load_issues(data)
    dp = R.developer_productivity(issues, 14, NOW)
    assert any(r["name"] == "Md Hasan" and r["output"] >= 1 for r in dp["rows"])
    qa = R.qa_productivity(issues, 14, NOW)
    assert any(r["name"] == "Tanvir Hossain" and r["verified"] >= 1 for r in qa["rows"])
    rr = R.release_readiness(issues, "R1")
    assert rr["open_critical"] == 1
    ed = R.executive_dashboard(issues, 14, NOW)
    assert ed["risk"]["Critical bugs"] == 1


def test_pipeline_position():
    """The Release page's pipeline view: one row per Jira status, labelled with
    the team's own status names, every ticket counted exactly once, and the two
    statuses the stage map collapses ("Development Completed" vs "Ready for QA
    (QA Env)") kept apart."""
    rows = [
        ("PP-01", "To Do", "To Do"),
        ("PP-02", "Development / In Design", "In Progress"),
        ("PP-03", "Development Completed", "In Progress"),
        ("PP-04", "Ready for QA (QA Env)", "To Do"),
        ("PP-05", "Ready for QA (QA Env)", "To Do"),
        ("PP-06", "In QA Testing (QA Env)", "In Progress"),
        ("PP-07", "Passed QA (Staging Ready)", "To Do"),
        ("PP-08", "Ready for Staging Verification", "To Do"),
        ("PP-09", "Passed Staging (Prod Ready)", "To Do"),
        ("PP-10", "In Production Testing", "In Progress"),
        ("PP-11", "Pause QA Testing", "To Do"),
        ("PP-12", "Blocked", "In Progress"),
        ("PP-13", "Done", "Done"),
    ]
    issues = R.load_issues([
        _issue(k, "Task", "Medium", "Md Hasan", s, c,
               "2026-06-01T09:00:00-0700", None, "R1", []) for k, s, c in rows])
    d = R.release_readiness(issues, "R1", now=NOW)
    by = {p["status"]: p for p in d["pipeline"]}

    # Every ticket lands in exactly one row — the property the cumulative funnel
    # cannot offer, and the reason this view can be trusted.
    assert sum(p["count"] for p in d["pipeline"]) == d["total"] == len(rows)
    # Rows are labelled with the real Jira status, not invented wording.
    assert all(p["status"] in R._PIPELINE_RANK for p in d["pipeline"])
    # The distinction the whole change exists for: these two collapse into one
    # stage in config.py, and must stay separate rows here.
    assert by["Development Completed"]["count"] == 1
    assert by["Ready for QA (QA Env)"]["count"] == 2
    # Workflow order, not alphabetical or count order.
    order = [p["status"] for p in d["pipeline"]]
    assert order.index("Development Completed") < order.index("Ready for QA (QA Env)")
    assert order.index("Ready for QA (QA Env)") < order.index("In QA Testing (QA Env)")
    assert order.index("To Do") == 0

    # The core flow always renders so the pipeline reads end to end, while the
    # exceptions (Blocked / Reopen / Pause…) only show when they hold tickets.
    assert all(s in by for s in R._PIPELINE_CORE), "core stages always present"
    assert by["Passed Staging (Prod Ready)"]["count"] == 1
    assert by["Blocked"]["count"] == 1 and by["Pause QA Testing"]["count"] == 1

    d3 = R.release_readiness(R.load_issues([
        _issue("PP-S1", "Task", "Medium", "Md Hasan", "Development / In Design",
               "In Progress", "2026-06-01T09:00:00-0700", None, "R1", [])]),
        "R1", now=NOW)
    only = [p["status"] for p in d3["pipeline"]]
    assert only == R._PIPELINE_CORE, "core only, in order, when nothing else is occupied"
    assert "Blocked" not in only and "Reopen" not in only
    assert [p["count"] for p in d3["pipeline"] if p["status"] == "To Do"] == [0]

    # Each "Pause …" status renders immediately before the stage it interrupts,
    # not collected at the bottom.
    paused = R.load_issues([
        _issue(f"PP-P{n}", "Task", "Medium", "Md Hasan", s, "To Do",
               "2026-06-01T09:00:00-0700", None, "R1", [])
        for n, s in enumerate(["Pause Development / Design", "Pause QA Testing",
                               "Pause Staging Testing", "Pause Production Testing"])])
    dp = [p["status"] for p in R.release_readiness(paused, "R1", now=NOW)["pipeline"]]
    for pause, stage in [("Pause Development / Design", "Development / In Design"),
                         ("Pause QA Testing", "In QA Testing (QA Env)"),
                         ("Pause Staging Testing", "In Staging Testing"),
                         ("Pause Production Testing", "In Production Testing")]:
        assert dp.index(pause) == dp.index(stage) - 1, f"{pause} sits before {stage}"

    # A status the workflow gained since _PIPELINE_ORDER was written still gets a
    # row rather than vanishing from the counts.
    d2 = R.release_readiness(R.load_issues([
        _issue("PP-X", "Task", "Medium", "Md Hasan", "Brand New Status",
               "In Progress", "2026-06-01T09:00:00-0700", None, "R1", [])]),
        "R1", now=NOW)
    new_row = next(p for p in d2["pipeline"] if p["status"] == "Brand New Status")
    assert new_row["known"] is False and new_row["count"] == 1
    assert sum(p["count"] for p in d2["pipeline"]) == d2["total"] == 1


if __name__ == "__main__":
    test_analytics_cycle_and_pause()
    test_reports_end_to_end()
    test_pipeline_position()
    print("All tests passed.")
