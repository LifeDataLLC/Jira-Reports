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


if __name__ == "__main__":
    for fn in sorted(list(globals().items())):
        if fn[0].startswith("test_"):
            fn[1]()
    print(f"All v3 tests passed ({PASSED} checks).")
