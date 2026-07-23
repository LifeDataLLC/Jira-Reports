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
STAGE_PROD_READY = "Prod Ready"        # passed staging, awaiting the production release
STAGE_PRODUCTION = "Production"         # deployed; production-testing/verification phase
STAGE_DONE = "Done"
STAGE_REOPENED = "Reopened"

STAGE_ORDER = [
    STAGE_TODO, STAGE_IN_PROGRESS, STAGE_DEVELOPMENT, STAGE_PAUSED,
    STAGE_READY_FOR_QA, STAGE_QA_TESTING, STAGE_STAGING, STAGE_PROD_READY,
    STAGE_PRODUCTION, STAGE_DONE, STAGE_REOPENED,
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
    STAGE_PROD_READY: "#57d9a3",    # mint (passed staging, prod-ready)
    STAGE_PRODUCTION: "#00875a",    # deep green (in production / prod-testing)
    STAGE_DONE: "#36b37e",          # green
    STAGE_REOPENED: "#de350b",      # red
}

# ---------------------------------------------------------------------------
# Real LifeData status name -> logical stage.
# Covers all LIFEDATAV2 workflow statuses (pulled from the project's status list).
# NOTE: Jira's own statusCategory is unreliable here — e.g. "Development Completed"
# and "In Production" report misleading categories — so every status is mapped
# explicitly rather than trusting the category. Add new statuses here.
# ---------------------------------------------------------------------------
DEFAULT_STATUS_STAGE = {
    # --- Not started ---
    "To Do": STAGE_TODO,
    "Backlog": STAGE_TODO,                       # legacy / safety
    "Selected for Development": STAGE_TODO,      # legacy / safety

    # --- Investigation ---
    "In Progress / Start Investigation": STAGE_IN_PROGRESS,
    "In Progress": STAGE_IN_PROGRESS,            # legacy / safety
    "Pause Investigation": STAGE_PAUSED,

    # --- Development (incl. product's design review of dev output) ---
    "Development / In Design": STAGE_DEVELOPMENT,
    "Ready for Design Review": STAGE_DEVELOPMENT,
    "Review/Testing": STAGE_DEVELOPMENT,
    "Resume Development": STAGE_DEVELOPMENT,
    "Pause Development / Design": STAGE_PAUSED,

    # --- Development completed / ready for QA ---
    "Development Completed": STAGE_READY_FOR_QA,  # NOT done: dev finished, headed to QA
    "Ready for QA (QA Env)": STAGE_READY_FOR_QA,

    # --- QA ---
    "In QA Testing (QA Env)": STAGE_QA_TESTING,
    "Pause QA Testing": STAGE_PAUSED,

    # --- Passed QA / staging ---
    "Passed QA (Staging Ready)": STAGE_STAGING,
    "Ready for Staging Verification": STAGE_STAGING,
    "In Staging Testing": STAGE_STAGING,
    "Pause Staging Testing": STAGE_PAUSED,

    # --- Passed staging, ready for the production release ---
    "Passed Staging (Prod Ready)": STAGE_PROD_READY,

    # --- Live in production (production-testing / verification phase) ---
    "In Production": STAGE_PRODUCTION,
    "In Production Testing": STAGE_PRODUCTION,
    "Verification in Production": STAGE_PRODUCTION,
    "Pause Production Testing": STAGE_PAUSED,

    # --- Done (verified & closed) ---
    "Resolved": STAGE_DONE,
    "Resolved in Production": STAGE_DONE,
    "Close": STAGE_DONE,
    "Done": STAGE_DONE,

    # --- Overlays (not part of the linear flow) ---
    "Reopen": STAGE_REOPENED,
    "Blocked": STAGE_PAUSED,
    "Customer Feedback": STAGE_PAUSED,           # waiting on customer -> treat as blocked
    "Cannot Reproduce": STAGE_PAUSED,            # treat as blocked for now
}

# Stages that count as "actively being worked" (for cycle time = first active -> done)
ACTIVE_STAGES = {
    STAGE_IN_PROGRESS, STAGE_DEVELOPMENT, STAGE_READY_FOR_QA,
    STAGE_QA_TESTING, STAGE_STAGING,
}
# Stages we treat as a paused/blocked clock (optionally excluded from active time)
BLOCKED_STAGES = {STAGE_PAUSED}
DONE_STAGES = {STAGE_DONE}

# Genuinely-blocked statuses, as opposed to the "Pause …" statuses which just mean a
# developer paused work (e.g. at end of day). Both map to the paused stage, but the
# Release page distinguishes "blocked" from "paused".
BLOCKED_STATUSES = {"Blocked", "Customer Feedback", "Cannot Reproduce"}


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

# ---- Developer-discipline report settings (Jira Developer Reports spec) ----
# Stage groupings used by the QA-handoff/return reports.
DEV_STAGES = {STAGE_IN_PROGRESS, STAGE_DEVELOPMENT}
QA_STAGES = {STAGE_READY_FOR_QA, STAGE_QA_TESTING}
RETURN_TARGET_STAGES = DEV_STAGES | {STAGE_REOPENED}

# Status Change Without Comment: a comment by the same author within this many
# minutes of the transition counts as "explained".
COMMENT_WINDOW_MIN = int(os.environ.get("COMMENT_WINDOW_MIN", "10"))

# Handoff Quality keyword checks (comma-separated, case-insensitive substrings).
HANDOFF_TEST_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "HANDOFF_TEST_KEYWORDS", "test,steps,verify,qa,reproduce,scenario").split(",") if k.strip()]
HANDOFF_PR_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "HANDOFF_PR_KEYWORDS", "pr,pull request,merge,commit,branch,build,github,bitbucket").split(",") if k.strip()]

# Blocked Tickets: labels that mark a ticket as blocked.
BLOCKED_LABELS = {k.strip().lower() for k in os.environ.get(
    "BLOCKED_LABELS", "blocked,dependency,waiting").split(",") if k.strip()}

# Optional: Agile board id(s) for sprint reports (comma-separated). Blank disables
# the sprint report gracefully.
BOARD_IDS = [b for b in os.environ.get("JIRA_BOARD_IDS", "").split(",") if b.strip()]
