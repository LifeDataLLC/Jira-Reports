"""
workflow.py
-----------
The LIFEDATAV2 Jira workflow, encoded as the single source of truth for status
classification. Derived from the team's status diagram + the "Jira Ticket Rules
for Developers & QA" reference table.

Buckets feed all analytics. `ACTIVE` encodes the blue "active work" statuses
with their lane (dev / qa / staging / production) and the pause status each moves
to at end of day — this drives the one-active-per-lane rule and the EOD-pause
check. Everything here seeds the admin settings store and can be re-applied from
the Settings screen; the admin can still override any status afterward.
"""

# status name -> bucket
BUCKETS = {
    # queue / pre-work
    "To Do": "todo",
    "Cannot Reproduce": "rework",
    "Customer Feedback": "rework",
    "Reopen": "rework",
    # active development (blue)
    "In Progress / Start Investigation": "active_dev",
    "Investigation": "active_dev",
    "Development / In Design": "active_dev",
    "Development": "active_dev",
    # dev handoff / QA + verification pipeline
    "Development Completed": "qa_stage",
    "Ready for Design Review": "qa_stage",
    "Ready for QA (QA Env)": "qa_stage",
    "In QA Testing (QA Env)": "qa_stage",
    "Review and Testing": "qa_stage",
    "Review/ Testing": "qa_stage",
    "Passed QA (Staging Ready)": "qa_stage",
    "Ready for Staging Verification": "qa_stage",
    "In Staging Testing": "qa_stage",
    "Passed Staging (Prod Ready)": "qa_stage",
    "In Production": "qa_stage",
    "In Production Testing": "qa_stage",
    "Verification in Production": "qa_stage",
    # paused / blocked
    "Pause Investigation": "paused",
    "Pause Development / Design": "paused",
    "Pause QA Testing": "paused",
    "Pause Staging Testing": "paused",
    "Pause Production Testing": "paused",
    "Blocked": "paused",
    # terminal
    "Close": "done",
    "Resolved": "done",
    "Done": "done",
}

# The blue "active work" statuses (someone is actively working the ticket right
# now): lane + the status each pauses into at end of day (None if the workflow has
# no matching pause state). Lane drives the one-active-ticket-per-lane rule.
ACTIVE = {
    # dev lane — investigation + development work
    "In Progress / Start Investigation": {"lane": "dev", "pause": "Pause Investigation"},
    "Investigation": {"lane": "dev", "pause": "Pause Investigation"},
    "Development / In Design": {"lane": "dev", "pause": "Pause Development / Design"},
    "Development": {"lane": "dev", "pause": "Pause Development / Design"},
    # qa lane — QA + review/testing work
    "In QA Testing (QA Env)": {"lane": "qa", "pause": "Pause QA Testing"},
    "Review and Testing": {"lane": "qa", "pause": "Pause QA Testing"},
    "Review/ Testing": {"lane": "qa", "pause": "Pause QA Testing"},
    "In Staging Testing": {"lane": "staging", "pause": "Pause Staging Testing"},
    "In Production Testing": {"lane": "production", "pause": "Pause Production Testing"},
}

LANE_LABELS = {"dev": "Development", "qa": "QA Testing",
               "staging": "Staging Testing", "production": "Production Testing"}

# Tight per-status thresholds so parked/queue states surface on the Attention
# Board (the "parked in QA Review forever" case) without polluting active counts.
THRESHOLDS = {
    "In Progress / Start Investigation": 5,
    "Investigation": 5,
    "Development / In Design": 5,
    "Development": 5,
    "Ready for QA (QA Env)": 2,
    "In QA Testing (QA Env)": 3,
    "Review and Testing": 3,
    "Review/ Testing": 3,
    "Passed QA (Staging Ready)": 2,
    "Ready for Staging Verification": 2,
    "In Staging Testing": 3,
    "Passed Staging (Prod Ready)": 2,
    "In Production": 2,
    "In Production Testing": 3,
    "Verification in Production": 2,
    "Ready for Design Review": 2,
    "Reopen": 2,
    "Customer Feedback": 3,
    "Blocked": 5,
}

# This workflow's rules require worklogs and due dates (rules 4 & 6), so those
# gates are enabled when the workflow is applied.
GATES_ON = ["worklogs_required", "due_dates_required"]
