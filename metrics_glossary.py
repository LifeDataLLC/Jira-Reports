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
    "eod_signal": "A comment, worklog, or any tracked update on the ticket that day.",
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
    "blocked": "Jira's Flagged field (changelog-tracked). Label matches are low-confidence hints.",
}


def gloss(term: str, label: str) -> str:
    """Wrap a label in an explain-this-number tooltip span."""
    d = GLOSSARY.get(term, "")
    return f'<span class="glossary" title="{d}">{label}</span>' if d else label
