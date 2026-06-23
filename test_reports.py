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


if __name__ == "__main__":
    test_analytics_cycle_and_pause()
    test_reports_end_to_end()
    print("All tests passed.")
