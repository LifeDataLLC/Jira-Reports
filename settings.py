"""
settings.py
-----------
Persistent, admin-editable configuration store (PRD v3 FR-C1–C6).

Everything workflow-specific lives here so status changes never require a code
deploy: status→bucket classification, per-status aging thresholds, feature
gates, checklist item toggles, handoff window, keyword/label lists, board IDs.

Storage: a JSON file at APP_CONFIG_PATH (default ./data/settings.json). On
first run it seeds itself from the legacy stage map in config.py — after that,
config.py is a fallback/seed only. Credentials never live here (env-only); the
sole allowed secret is the Teams webhook URL (documented in DEPLOYMENT.md).
"""

from __future__ import annotations

import json
import os
import threading
import time

import config as legacy

BUCKETS = ["todo", "active_dev", "qa_stage", "paused", "rework", "done"]
BUCKET_LABELS = {
    "todo": "To Do", "active_dev": "Active Dev", "qa_stage": "QA Stage",
    "paused": "Paused/Blocked", "rework": "Rework", "done": "Done",
}

# Legacy logical stage -> v3 bucket (used only to seed a fresh settings file).
_STAGE_TO_BUCKET = {
    legacy.STAGE_TODO: "todo",
    legacy.STAGE_IN_PROGRESS: "active_dev",
    legacy.STAGE_DEVELOPMENT: "active_dev",
    legacy.STAGE_PAUSED: "paused",
    legacy.STAGE_READY_FOR_QA: "qa_stage",
    legacy.STAGE_QA_TESTING: "qa_stage",
    legacy.STAGE_STAGING: "qa_stage",
    legacy.STAGE_DONE: "done",
    legacy.STAGE_REOPENED: "rework",
}

DEFAULTS = {
    "version": 2,
    # status name -> bucket
    "status_buckets": {},
    # status name -> max days in status before it lands on the Attention Board
    "status_thresholds": {},
    # the "active work" statuses: {status: {"lane": .., "pause": ..}} — drives the
    # one-active-per-lane rule (Rule 1) and the pause-at-EOD check (Rule 3).
    "active_statuses": {},
    # bucket-level threshold defaults (PRD §9 proposal); null = no threshold
    "bucket_thresholds": {"todo": None, "active_dev": 5, "qa_stage": 3,
                          "paused": 10, "rework": 2, "done": None},
    # feature gates — all default OFF (FR-C4); gated UI shows teaching empty states
    "gates": {"worklogs_required": False, "estimates_used": False,
              "due_dates_required": False, "start_dates_required": False,
              "sprints_enabled": False},
    # My Day checklist item toggles (FR-C3)
    "checklist_items": {"status_mapped": True, "comment_today": True,
                        "due_date": True, "past_due": True, "has_release": True},
    # QA handoff: comment by transition author within this many hours before/at handoff
    "handoff_window_hours": 4,
    "test_keywords": ["test", "steps", "verify", "qa", "reproduce", "scenario"],
    "pr_keywords": ["pr", "pull request", "merge", "commit", "branch", "build",
                    "github", "bitbucket", "dev.azure.com"],
    "blocked_labels": ["blocked", "dependency", "waiting"],
    # Attention Board: "Silent" = no activity event in this many days while active
    "silent_days": 2,
    # Investigator: inactivity gaps >= this many days rendered as spacers
    "gap_days": 7,
    # My Day: mark a ticket "stale" when its status hasn't changed in this many days
    "stale_days": 10,
    # Sprint boards (moved here from env per Phase 5)
    "board_ids": [],
    # Start-date custom field id (runtime-detected; admin-overridable)
    "start_date_field": None,
    # Teams incoming-webhook URL for the morning digest (FR-U8)
    "teams_webhook_url": "",
    # Role-based landing default: developer | lead | exec (FR-X4)
    "default_role": "lead",
    # Developers (Jira assignees) to hide from the My Day dropdown — e.g. past
    # employees. Stored by accountId or display name.
    "hidden_developers": [],
    # Jira project keys to include in all views. Empty = the JIRA_PROJECTS env
    # default. Lets admins choose the Support space, the V2 space, or both.
    "projects": [],
}

def data_dir() -> str:
    """Directory for the config store and snapshot DB.

    On Azure App Service, `/home` is the persistent mount that survives deploys
    and restarts — unlike `/home/site/wwwroot`, which the app runs from and Azure
    replaces on every deployment. We detect Azure via WEBSITE_SITE_NAME (always
    set there) and default to /home/data so settings saving works out of the box
    with no manual configuration. Locally it's ./data. Override with APP_DATA_DIR.
    """
    override = os.environ.get("APP_DATA_DIR")
    if override:
        return override
    # Keep users/snapshots/secret next to the settings file when it's overridden
    # (tests and custom deployments set APP_CONFIG_PATH).
    cfg = os.environ.get("APP_CONFIG_PATH")
    if cfg:
        return os.path.dirname(cfg) or "."
    if os.environ.get("WEBSITE_SITE_NAME"):  # running on Azure App Service
        return "/home/data"
    return os.path.join(".", "data")


CONFIG_PATH = os.environ.get("APP_CONFIG_PATH") or os.path.join(data_dir(), "settings.json")

_lock = threading.Lock()
_cache: dict = {"data": None, "mtime": 0.0, "path": None}


def apply_workflow(data: dict) -> dict:
    """Load the LIFEDATAV2 workflow (workflow.py) into a settings dict: bucket
    map, active-status lanes/pauses, parked-state thresholds, and the gates the
    workflow's rules require. Admin overrides afterward still win per status."""
    import workflow as wf
    data["status_buckets"] = dict(wf.BUCKETS)
    data["active_statuses"] = json.loads(json.dumps(wf.ACTIVE))
    data["status_thresholds"] = dict(wf.THRESHOLDS)
    for g in wf.GATES_ON:
        data["gates"][g] = True
    return data


def _seed() -> dict:
    """Fresh settings seeded from the LIFEDATAV2 workflow."""
    data = json.loads(json.dumps(DEFAULTS))  # deep copy
    apply_workflow(data)
    data["board_ids"] = [b for b in legacy.BOARD_IDS]
    return data


def load(path: str | None = None) -> dict:
    """Load settings, seeding the file on first run. Reloads if file changed."""
    path = path or CONFIG_PATH
    with _lock:
        try:
            mtime = os.path.getmtime(path)
            if _cache["data"] is not None and _cache["path"] == path and _cache["mtime"] == mtime:
                return _cache["data"]
            with open(path) as fh:
                raw = json.load(fh)
            data = json.loads(json.dumps(DEFAULTS))
            for k, v in raw.items():
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    data[k].update(v)
                else:
                    data[k] = v
            # active_statuses is defined in code (workflow.py) and is NOT admin-
            # editable, so always take the current code definition — otherwise a
            # copy frozen into settings.json at first-run can drift from the code
            # and a status like "Development / In Design" stops reading as active.
            import workflow as wf
            data["active_statuses"] = json.loads(json.dumps(wf.ACTIVE))
            _cache.update(data=data, mtime=mtime, path=path)
            return data
        except (OSError, ValueError):
            data = _seed()
    save(data, path)
    return data


def save(data: dict, path: str | None = None) -> None:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Unique temp name per writer: a shared "<path>.tmp" makes two concurrent
    # savers (e.g. a request and the startup warm thread) clobber each other's
    # temp file, so one os.replace fails with FileNotFoundError.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    with _lock:
        _cache.update(data=data, path=path)
        try:
            _cache["mtime"] = os.path.getmtime(path)
        except OSError:
            _cache["mtime"] = time.time()


# ---------------------------------------------------------------------------
# Lookup helpers used by every metric engine
# ---------------------------------------------------------------------------

def bucket_of(status: str, status_category: str | None = None) -> str | None:
    """Bucket for a status, or None when unmapped. Never guesses (PRD §3.1) —
    except Jira's own Done category, which is unambiguous. Admin overrides win;
    the code workflow (workflow.py) is the fallback base so a status the code
    already knows never reads as unmapped just because settings.json is stale."""
    b = load()["status_buckets"].get(status)
    if b:
        return b
    import workflow as wf
    if status in wf.BUCKETS:
        return wf.BUCKETS[status]
    if status_category == "Done":
        return "done"
    return None


def threshold_for(status: str) -> float | None:
    """Aging threshold (days) for a status: per-status override, else its
    bucket's default, else None (no threshold)."""
    s = load()
    if status in s["status_thresholds"] and s["status_thresholds"][status] is not None:
        return float(s["status_thresholds"][status])
    b = s["status_buckets"].get(status)
    v = s["bucket_thresholds"].get(b) if b else None
    return float(v) if v is not None else None


def gate(name: str) -> bool:
    return bool(load()["gates"].get(name, False))


def unmapped_statuses(seen: set[str]) -> list[str]:
    """Statuses present in synced data but not classified into a bucket (by an
    admin override or the code workflow)."""
    import workflow as wf
    mapped = set(load()["status_buckets"]) | set(wf.BUCKETS)
    return sorted(s for s in seen if s and s not in mapped)


# ---- active-status layer (Rules 1 & 3) ----

def is_active_status(status: str) -> bool:
    return status in load().get("active_statuses", {})


def lane_of(status: str) -> str | None:
    a = load().get("active_statuses", {}).get(status)
    return a.get("lane") if a else None


def lane_label(status: str) -> str | None:
    """Friendly lane name for an active status (e.g. 'Development'), or None."""
    lane = lane_of(status)
    if not lane:
        return None
    import workflow as wf
    return wf.LANE_LABELS.get(lane, lane)


def pause_for(status: str) -> str | None:
    a = load().get("active_statuses", {}).get(status)
    return a.get("pause") if a else None
