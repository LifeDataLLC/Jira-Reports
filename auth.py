"""
auth.py
-------
Account + session core for the app's login system. Two roles: admin and
employee. Accounts are stored in users.json next to the settings store
(data_dir), passwords hashed with Werkzeug. The first account created becomes
the admin (bootstrap); afterward only an admin can create more admin accounts.

An employee account is permanently linked to exactly one developer (a Jira
assignee) — that is the only developer they can view on My Day. Admins may view
any (non-hidden) developer and default to their own if they linked one.

Read-only Jira is unaffected; this only governs who can open the app.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

import settings as st


def _users_path() -> str:
    return os.path.join(st.data_dir(), "users.json")


def _load() -> dict:
    try:
        with open(_users_path()) as fh:
            data = json.load(fh)
            data.setdefault("users", {})
            return data
    except (OSError, ValueError):
        return {"users": {}}


def _save(data: dict) -> None:
    path = _users_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Secret key (stable across workers/restarts so sessions survive)
# ---------------------------------------------------------------------------

def secret_key() -> str:
    k = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if k:
        return k
    path = os.path.join(st.data_dir(), "secret_key")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        k = secrets.token_hex(32)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            fh.write(k)
        return k


# ---------------------------------------------------------------------------
# User records
# ---------------------------------------------------------------------------

def user_count() -> int:
    return len(_load()["users"])


def list_users() -> list[dict]:
    return sorted(_load()["users"].values(), key=lambda u: u["email"])


def get_user(email: str) -> dict | None:
    return _load()["users"].get((email or "").strip().lower())


def developer_claimed_by(dev_id: str, dev_name: str, exclude_email=None):
    """Return the email of the employee already linked to this developer, if any."""
    for u in list_users():
        if u.get("role") == "employee" and u["email"] != (exclude_email or ""):
            if (dev_id and u.get("developer_id") == dev_id) or \
               (dev_name and u.get("developer") == dev_name):
                return u["email"]
    return None


class AuthError(Exception):
    pass


def create_user(email, password, role, developer=None, developer_id=None):
    """Create an account. Raises AuthError on any validation problem."""
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise AuthError("Enter a valid work email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if role not in ("admin", "employee"):
        raise AuthError("Invalid role.")
    data = _load()
    if email in data["users"]:
        raise AuthError("An account with that email already exists.")
    if role == "employee":
        if not (developer_id or developer):
            raise AuthError("Select which developer you are.")
        claimed = developer_claimed_by(developer_id, developer)
        if claimed:
            raise AuthError(f"That developer is already linked to another account ({claimed}).")
    data["users"][email] = {
        "email": email,
        "password_hash": generate_password_hash(password, method="pbkdf2:sha256"),
        "role": role,
        "developer": developer or None,
        "developer_id": developer_id or None,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _save(data)
    return data["users"][email]


def delete_user(email: str) -> None:
    data = _load()
    data["users"].pop((email or "").strip().lower(), None)
    _save(data)


def verify(email, password) -> dict | None:
    u = get_user(email)
    if u and check_password_hash(u["password_hash"], password or ""):
        return u
    return None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def login_user(user: dict) -> None:
    session["user_email"] = user["email"]
    session.permanent = True


def logout_user() -> None:
    session.pop("user_email", None)


def current_user() -> dict | None:
    email = session.get("user_email")
    return get_user(email) if email else None


def is_admin() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "admin")


# ---------------------------------------------------------------------------
# Developer directory (for the dropdowns) — derived from Jira assignees
# ---------------------------------------------------------------------------

def all_developers() -> list[dict]:
    """Distinct Jira assignees seen in the dataset: [{id, name}] sorted by name.
    id is the accountId when present, else the display name."""
    import jira_client as jc
    seen = {}
    try:
        for raw in jc.fetch_dev_dataset(None):
            a = (raw.get("fields", {}) or {}).get("assignee") or {}
            name = a.get("displayName")
            if name and name != "Unassigned":
                seen[a.get("accountId") or name] = name
    except Exception:
        pass
    return [{"id": k, "name": v} for k, v in sorted(seen.items(), key=lambda kv: kv[1].lower())]


def visible_developers() -> list[dict]:
    """Developers minus those hidden in Settings (e.g. past employees)."""
    hidden = set(st.load().get("hidden_developers", []))
    return [d for d in all_developers() if d["id"] not in hidden and d["name"] not in hidden]
