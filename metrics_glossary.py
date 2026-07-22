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
    "silent": "Nobody has touched the ticket at all — no transition, comment, worklog or field "
              "change — for the configured number of days. Compare with 'stale', which looks "
              "only at status changes.",
    "stale": "The ticket has not CHANGED STATUS for the configured number of days. It may still "
             "have comments or worklogs — it just isn't moving through the workflow. Compare "
             "with 'silent', which means no activity of any kind.",
    "aging": "Time in the current status exceeds that status's configured threshold — this step "
             "is taking longer than it should, regardless of any due date.",
    "past_due": "The ticket's due date has passed. About the committed date, not how long it has "
                "sat in a status (that's 'aging').",
    "no_release": "The ticket isn't assigned to a release (fixVersion). Required for every open "
                  "ticket until it's resolved/done.",
    "missing_dates": "An open ticket missing a date the team requires — a due date (any open "
                     "ticket) or a start date (in development or rework) — so it can't be planned against.",
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
