# Contributing & Handoff Guide

Internal Jira reporting web app for LifeData. This guide is for the engineering team
picking the project up in a real repo / Claude Code.

## What this is

A self-hosted Flask app that reads Jira via the REST API (read-only token) and reports
on engineering flow: cycle time, status aging, throughput, current workload, per-status
time, and the eight executive reports from the Reporting Framework. No paid add-on; all
metrics derive from the issue changelog. See `README.md` for features/usage and `PRD.md`
+ `IMPLEMENTATION_PLAN.md` for scope and roadmap.

## Architecture (keep these layers separate)

```
jira_client.py   REST transport: search, changelog, comments, worklogs, versions,
                 sprints, custom-field detection. Auth via env.
analytics.py     Pure changelog math: timeline reconstruction, durations,
                 transitions, percentiles.
settings.py      Persistent admin config: status->bucket map, thresholds, gates
                 (APP_CONFIG_PATH json). EDIT VIA /settings, not code.
activity.py      Unified event feed (transitions/comments/worklogs/field changes).
checklist.py     My Day checklist engine. attention.py: Attention Board reasons.
qa_handoff.py    Handoff/return edges + checks. flow_quality.py: cycle/bottleneck/
                 bug-lens engines. planning.py: date-discipline metrics (gated).
snapshots.py     SQLite daily aggregates. digest.py: Teams webhook card.
screens_web.py   v3 screens (My Day/Attention/QA/Flow/Quality/Planning/
                 Investigator/Trends/Settings) + nav + filter bar.
metrics_glossary.py  Single source of metric definitions (UI tooltips).
config.py        Status-name -> logical-stage mapping + tunable settings. EDIT HERE
                 when the workflow changes; no other module should hard-code statuses.
reports.py       The 8 report builders + time-in-status. Operates on in-memory issues.
reports_web.py   Flask Blueprint: the executive report pages + CSV/JSON.
app.py           v0 workload pages (overview, per-developer, Excel) + app wiring.
test_reports.py  Unit tests for analytics + report builders (no network).
```

Rule of thumb: data shape changes go in `jira_client`, metric definitions in
`analytics`/`reports`, workflow specifics in `config`, presentation in `reports_web`/`app`.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in JIRA_* values (read-only API token)
# export the vars (or use a dotenv loader), then:
python app.py                 # dev server
# or pick a port: PORT-style via app.run(port=...)
```

Run tests (no Jira needed — fixtures are modeled on real tickets):

```bash
python3 test_reports.py
```

## Configuration

All via environment (`.env.example` lists them). Never commit `.env`.

- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` — required, read-only token.
- `JIRA_PROJECTS` (default `LIFEDATAV2`), `JIRA_WINDOW_DAYS` (default 14).
- `JIRA_BOARD_IDS` — enables the Sprint Health report.
- `DEV_OUTPUT_STAGE`, `STUCK_THRESHOLD_DAYS`, `EXCLUDE_BLOCKED`, `RISK_W_*`, `STORY_POINT_FIELD`.
- `JIRA_STATUS_MAP` — path to a JSON override of the status→stage map in `config.py`.

## Production run

Use a WSGI server, not the dev server:

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

Put it behind nginx/Apache if you want TLS termination, and add authentication before
exposing beyond localhost (currently there is none — it is local-only by design).
A `Dockerfile` is included for container deploys.

Network: the host needs HTTPS egress to `lifedata.atlassian.net` and `api.atlassian.com`.

## Conventions

- Read-only: never call Jira write endpoints. The token should not have write scopes.
- Secrets only via env; nothing sensitive in code or logs.
- Add/adjust a test in `test_reports.py` for any metric logic change.
- Metrics are flow indicators, not individual performance scores — keep that framing.

## Good first tasks (from IMPLEMENTATION_PLAN.md)

- Phase 1 hardening: configurable port, `.env` auto-load, retry/backoff, `/health`.
- Wire `JIRA_BOARD_IDS` and verify Sprint Health against a real board.
- Confirm/extend the `config.py` status map against the live workflow.
- Phase 4: SQLite snapshots for trend history.
