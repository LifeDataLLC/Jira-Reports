"""
config.py
---------
Single place to map LifeData's real Jira workflow onto the logical stages the
Executive Reporting Framework expects, plus tunable settings. Edit this file (or
override via environment) when your workflow or preferences change — no other code
should need touching.

Why a mapping exists: the framework talks in idealized stages ("Development ->
Ready for QA"). Your actual statuses are richer ("Development / In Design",
"Ready for QA (QA Env)", "In QA Testing (QA Env)", "Development Completed", ...).
Every transition metric is computed against the STAGE, not the raw status name,
so renaming a status only means editing the map here.
"""

from __future__ import annotations
import json
import os

# ---------------------------------------------------------------------------
# Logical stages (ordered, earliest -> latest in a normal flow)
# ---------------------------------------------------------------------------
STAGE_TODO = "To Do"
STAGE_IN_PROGRESS = "In Progress"      # investigation / analysis
STAGE_DEVELOPMENT = "Development"
STAGE_PAUSED = "Paused/Blocked"
STAGE_READY_FOR_QA = "Ready for QA"
STAGE_QA_TESTING = "QA Testing"
STAGE_STAGING = "Staging/Verification"
STAGE_DONE = "Done"
STAGE_REOPENED = "Reopened"

STAGE_ORDER = [
    STAGE_TODO, STAGE_IN_PROGRESS, STAGE_DEVELOPMENT, STAGE_PAUSED,
    STAGE_READY_FOR_QA, STAGE_QA_TESTING, STAGE_STAGING, STAGE_DONE, STAGE_REOPENED,
]

# Stable color per stage, used by the stage-journey bar and legends.
STAGE_COLORS = {
    STAGE_TODO: "#8993a4",          # gray
    STAGE_IN_PROGRESS: "#0065ff",   # blue
    STAGE_DEVELOPMENT: "#0747a6",   # dark blue
    STAGE_PAUSED: "#ff7452",        # red-orange (blocked)
    STAGE_READY_FOR_QA: "#00b8d9",  # teal
    STAGE_QA_TESTING: "#ffab00",    # amber
    STAGE_STAGING: "#6554c0",       # purple
    STAGE_DONE: "#36b37e",          # green
    STAGE_REOPENED: "#de350b",      # red
}

# ---------------------------------------------------------------------------
# Real LifeData status name -> logical stage.
# Derived from the LIFEDATAV2 workflow seen in the changelog. Add new statuses here.
# ---------------------------------------------------------------------------
DEFAULT_STATUS_STAGE = {
    "To Do": STAGE_TODO,
    "Backlog": STAGE_TODO,
    "Selected for Development": STAGE_TODO,

    "In Progress / Start Investigation": STAGE_IN_PROGRESS,
    "In Progress": STAGE_IN_PROGRESS,
    "Pause Investigation": STAGE_PAUSED,

    "Development / In Design": STAGE_DEVELOPMENT,
    "Pause Development / Design": STAGE_PAUSED,

    "Ready for QA (QA Env)": STAGE_READY_FOR_QA,

    "In QA Testing (QA Env)": STAGE_QA_TESTING,

    "Passed QA (Staging Ready)": STAGE_STAGING,
    "Ready for Staging Verification": STAGE_STAGING,
    "In Staging Testing": STAGE_STAGING,

    "Development Completed": STAGE_DONE,
    "Close": STAGE_DONE,
    "Done": STAGE_DONE,

    "Reopen": STAGE_REOPENED,
}

# Stages that count as "actively being worked" (for cycle time = first active -> done)
ACTIVE_STAGES = {
    STAGE_IN_PROGRESS, STAGE_DEVELOPMENT, STAGE_READY_FOR_QA,
    STAGE_QA_TESTING, STAGE_STAGING,
}
# Stages we treat as a paused/blocked clock (optionally excluded from active time)
BLOCKED_STAGES = {STAGE_PAUSED}
DONE_STAGES = {STAGE_DONE}


def load_status_stage() -> dict:
    """Allow an external JSON override file (JIRA_STATUS_MAP=path) to win."""
    path = os.environ.get("JIRA_STATUS_MAP")
    if path and os.path.exists(path):
        with open(path) as fh:
            override = json.load(fh)
        merged = dict(DEFAULT_STATUS_STAGE)
        merged.update(override)
        return merged
    return DEFAULT_STATUS_STAGE


STATUS_STAGE = load_status_stage()


def stage_of(status_name: str, status_category: str | None = None) -> str:
    """
    Map a raw status name to a logical stage. Falls back to the Jira status
    category so unmapped statuses still classify sensibly.
    """
    if status_name in STATUS_STAGE:
        return STATUS_STAGE[status_name]
    if status_category == "Done":
        return STAGE_DONE
    if status_category == "In Progress":
        return STAGE_IN_PROGRESS
    return STAGE_TODO


# ---------------------------------------------------------------------------
# Tunable settings (env-overridable)
# ---------------------------------------------------------------------------

# Developer "output" KPI: which stage entry counts as delivered work.
# Framework uses "Ready for QA"; set to "Done" to count completion instead.
DEV_OUTPUT_STAGE = os.environ.get("DEV_OUTPUT_STAGE", STAGE_READY_FOR_QA)

# Tickets aging beyond this many days in their current status are "stuck".
STUCK_THRESHOLD_DAYS = int(os.environ.get("STUCK_THRESHOLD_DAYS", "7"))

# Exclude paused/blocked time from cycle/active duration?
EXCLUDE_BLOCKED_FROM_ACTIVE = os.environ.get("EXCLUDE_BLOCKED", "false").lower() == "true"

# Release Risk Score weights (open bugs etc.). Tune to taste.
RISK_WEIGHTS = {
    "critical_bug": int(os.environ.get("RISK_W_CRITICAL", "10")),
    "high_bug": int(os.environ.get("RISK_W_HIGH", "5")),
    "pending_story": int(os.environ.get("RISK_W_PENDING", "2")),
    "blocked": int(os.environ.get("RISK_W_BLOCKED", "3")),
}

# Optional: story-point custom field id (auto-detected at runtime if blank).
STORY_POINT_FIELD = os.environ.get("STORY_POINT_FIELD", "")

# Optional: Agile board id(s) for sprint reports (comma-separated). Blank disables
# the sprint report gracefully.
BOARD_IDS = [b for b in os.environ.get("JIRA_BOARD_IDS", "").split(",") if b.strip()]
