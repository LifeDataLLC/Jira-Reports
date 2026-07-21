# Jira-Reports — project state (handoff)

Reload this file to continue in a fresh session. Concise on purpose.

## What it is
Read-only Flask app over the Jira REST API for LifeData engineering discipline.
Repo `LifeDataLLC/Jira-Reports` → auto-deploys to Azure App Service (gunicorn
`app:app`) on every push to `main`. gh CLI at `~/.local/bin/gh`.

## Architecture (module map)
- `jira_client.py` — Jira transport (search/changelog/comments/worklogs/versions/
  projects), retry+backoff, TTL cache, enriched dev dataset, `configured_projects()`.
- `settings.py` — persistent admin config (JSON at `data_dir()`; Azure `/home/data`,
  else `./data`, else `dirname(APP_CONFIG_PATH)`). Buckets, thresholds, gates,
  active-status lanes/pauses, hidden_developers, projects. Seeds from `workflow.py`.
- `workflow.py` — LIFEDATAV2 status→bucket map, 5 active statuses {lane,pause}, thresholds.
- `analytics.py` — changelog timeline, durations, percentile(median/p85).
- `activity.py` — unified event feed. `checklist.py` — My Day. `attention.py` — Attention.
- `qa_handoff.py`, `flow_quality.py`, `planning.py`, `snapshots.py`, `digest.py`.
- `auth.py`/`auth_web.py` — login. `screens_web.py` — v3 screens + nav + filter bar.
- `reports_web.py`/`app.py` — retained engines + guard + redirects. `metrics_glossary.py`.
- Tests: `test_v3.py` (129 checks), `test_reports.py`. Run with `python3`.

## Screens (all require login)
`/my-day` (checklist), `/attention`, `/qa`, `/flow`, `/quality`, `/planning`,
`/investigate`, `/exec` (Trends+Meeting Mode), `/settings` (admin), `/admin/users`.
CSV + `/api/v2/*` JSON everywhere. `/tasks/snapshot` public (scheduler).

## Key decisions in force
- **Auth:** email+password, roles admin/employee. First account = admin; then only
  admins create admins; employees self-register and are PERMANENTLY linked to one
  developer (their only My Day option). Whole app behind login; Settings/admin/
  rollup/feed admin-only. pbkdf2 hashing; persisted SECRET_KEY in data_dir.
- **Terminology:** "active/in-progress" = the 5 blue statuses only (`is_active_status`).
  Broad open work = "open tickets" (NOT "in-flight" — removed). Roll-up denominator =
  active-or-paused.
- **My Day checklist = 5 items:** status_mapped, comment_today, due_date, has_release,
  not_over_threshold. (worklog/start_date/eod_pause/handoff/blocked removed from My Day;
  pause/blocked still on Attention.) Filter chips = "show tickets failing <check>".
- **7 dev-team rules** enforced (see `docs/dev_team_rules_mapping.md`).
- **Projects:** Settings picks Jira spaces (Support / V2 / both) → `configured_projects()`.

## Operational must-dos (Azure)
- First person to open the deployed app registers = becomes ADMIN. Do this before
  sharing the URL.
- `/settings` → "Load LIFEDATAV2 workflow" to map statuses; fix any "needs classification".
- Persistence auto-works (`/home/data`). Optionally set `SECRET_KEY` app setting.
- Schedule daily `POST /tasks/snapshot?digest=1` for trends+digest; set Teams webhook in Settings.

## Deferred / open
- Per-project checklist scoping (global today); Jira dev-panel PR links (URL/keyword now).
- Consider Azure Easy Auth / IP restriction (self-register is open); optionally lock
  lead screens (Attention/QA/Flow/Quality/Trends) to admins only.
- Node20-deprecation warning in the Azure workflow is cosmetic.

## Working style
Deploy after each change (push → watch `gh run watch`). Tests must pass. Read-only
Jira; no decimal scores on individuals; medians with raw counts.
