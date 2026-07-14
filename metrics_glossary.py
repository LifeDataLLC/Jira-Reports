"""
metrics_glossary.py
-------------------
Single source of metric definitions (PRD v3 FR-U5, "explain this number").
The UI renders these as hover tooltips; keeping them in one dict means the
docs and the UI cannot drift apart.
"""

GLOSSARY = {
    "cycle_time": "First transition into an active development status → first Done. "
                  "Reopened tickets keep the first-entry policy; rework loops are shown separately.",
    "dev_to_qa": "First transition into active development → first transition into the QA stage.",
    "median": "Middle value — half the tickets are faster, half slower. Robust to one 28-day outlier.",
    "p85": "85th percentile — 85% of tickets finish within this time. The 'bad week' bound.",
    "active_status": "A ticket in one of the five 'active work' statuses — In Progress / "
                     "Start Investigation, Development / In Design, In QA Testing, In Staging "
                     "Testing, In Production Testing. It means someone is CURRENTLY working on "
                     "it. One per lane at a time; move it to its pause status at end of day.",
    "open_work": "A developer's open, assigned tickets: anything in an active, paused, QA-pipeline, "
                 "or reopened status. NOT To Do (not started) and NOT Done. Broader than "
                 "'active' — an open ticket may be paused or waiting, not being worked right now.",
    "eod_signal": "Evidence a ticket was touched that day — a comment, worklog, status change, "
                  "or any tracked update. Measured across tickets in an active or paused status.",
    "silent": "No activity-feed event (transition, comment, worklog, field change) in N days "
              "while the ticket sits in an active development or rework status.",
    "aging": "Time in the current status exceeds that status's configured threshold.",
    "handoff": "Any transition entering the QA stage, credited to whoever performed it "
               "(the changelog author), not the current assignee.",
    "handoff_check": "Binary Pass / Needs info: a comment by the handoff author within the "
                     "configured window, plus a PR/build reference. Never a decimal score.",
    "return": "A transition from the QA stage back to development or rework.",
    "return_rate": "Returns ÷ handoffs, attributed to the most recent handoff author. "
                   "Raw counts always shown — 50% of 2 is noise, not a trend.",
    "reopen_loop": "A ticket with 2+ rework cycles (returned or reopened at least twice).",
    "multiple_active": "Developers holding more than one ticket in active development at once. "
                       "QA-stage tickets are excluded — those are aging's job.",
    "focus": "Distinct tickets a developer touched per day. High counts suggest context switching.",
    "bottleneck": "Median days tickets spend in each status — the biggest number is the constraint.",
    "slip": "Current due date minus the original (first-ever) due date, with the push count.",
    "reschedule_count": "How many times the planned start date was moved. A prioritization signal, "
                        "not a developer problem.",
    "disposition": "An over-threshold ticket must move to Backlog or get a future start date "
                   "within 48 hours.",
    "eod_pause": "Active tickets must be moved to their paused status at end of day "
                 "(and back to active when resumed). Flags tickets left in an active status overnight.",
    "has_release": "Every ticket must belong to a release (fixVersion) — an upcoming feature, "
                   "bug, or the next backlog release.",
    "one_active": "One ticket active at a time per lane: dev (In Progress/Development), QA, "
                  "staging, and production testing are each enforced separately.",
    "blocked": "Jira's Flagged field (changelog-tracked). Label matches are low-confidence hints.",
}


def gloss(term: str, label: str) -> str:
    """Wrap a label in an explain-this-number tooltip span."""
    d = GLOSSARY.get(term, "")
    return f'<span class="glossary" title="{d}">{label}</span>' if d else label
