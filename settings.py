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
    "version": 1,
    # status name -> bucket
    "status_buckets": {},
    # status name -> max days in status before it lands on the Attention Board
    "status_thresholds": {},
    # bucket-level threshold defaults (PRD §9 proposal); null = no threshold
    "bucket_thresholds": {"todo": None, "active_dev": 5, "qa_stage": 3,
                          "paused": 10, "rework": 2, "done": None},
    # feature gates — all default OFF (FR-C4); gated UI shows teaching empty states
    "gates": {"worklogs_required": False, "estimates_used": False,
              "due_dates_required": False, "start_dates_required": False,
              "sprints_enabled": False},
    # My Day checklist item toggles (FR-C3)
    "checklist_items": {"status_mapped": True, "comment_today": True,
                        "worklog_today": True, "start_date": True,
                        "due_date": True, "not_over_threshold": True,
                        "handoff_comment": True, "blocked_reason": True},
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
    # Sprint boards (moved here from env per Phase 5)
    "board_ids": [],
    # Start-date custom field id (runtime-detected; admin-overridable)
    "start_date_field": None,
    # Teams incoming-webhook URL for the morning digest (FR-U8)
    "teams_webhook_url": "",
    # Role-based landing default: developer | lead | exec (FR-X4)
    "default_role": "lead",
}

CONFIG_PATH = os.environ.get("APP_CONFIG_PATH", os.path.join(".", "data", "settings.json"))

_lock = threading.Lock()
_cache: dict = {"data": None, "mtime": 0.0, "path": None}


def _seed() -> dict:
    """Fresh settings seeded from the legacy config.py stage map."""
    data = json.loads(json.dumps(DEFAULTS))  # deep copy
    for status, stage in legacy.STATUS_STAGE.items():
        data["status_buckets"][status] = _STAGE_TO_BUCKET.get(stage, "todo")
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
            _cache.update(data=data, mtime=mtime, path=path)
            return data
        except (OSError, ValueError):
            data = _seed()
    save(data, path)
    return data


def save(data: dict, path: str | None = None) -> None:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
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
    except Jira's own Done category, which is unambiguous."""
    b = load()["status_buckets"].get(status)
    if b:
        return b
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
    """Statuses present in synced data but not classified into a bucket."""
    mapped = set(load()["status_buckets"])
    return sorted(s for s in seen if s and s not in mapped)
