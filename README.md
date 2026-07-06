# Developer Activity Report (Jira)

A small, self-hosted web app that reports on what your developers are doing in
Jira — how long in-progress tickets are taking, what's been completed, and a
per-developer breakdown. It replaces what the expensive Jira reporting add-ons do,
using nothing but the standard Jira REST API and a read-only API token.

## What it shows

- **Throughput** — tickets each developer completed in the window (default 14 days).
- **Cycle time** — time from when a ticket first entered an *In Progress* status
  to *Done*, computed from the issue changelog (not just created→resolved).
- **Status aging** — how long each in-progress ticket has sat in its current status,
  so stuck work surfaces immediately (highlighted when > 14 days).
- **Completed / in-progress lists** — per developer, with links back to Jira.

Everything is grouped by the ticket's current assignee.

## How it works

Every status change on a Jira ticket is recorded in that ticket's **changelog**
with a timestamp. The paid add-ons are essentially readers of this same data.
`jira_client.py` pulls issues with `expand=changelog`, walks the status history,
and derives the metrics. Statuses are classified by Jira's built-in
`statusCategory` (To Do / In Progress / Done), so custom workflow status names
like *"In Progress / Start Investigation"* are handled automatically.

## Setup

1. Create a Jira API token: https://id.atlassian.com/manage-profile/security/api-tokens
   (read access to issues + search is all that's needed — no write permissions).

2. Install and configure:

   ```bash
   pip install -r requirements.txt
   cp .env.example .env      # then edit .env  (or just export the vars)
   export JIRA_BASE_URL=https://lifedata.atlassian.net
   export JIRA_EMAIL=you@lifedatacorp.com
   export JIRA_API_TOKEN=*****
   ```

3. Run:

   ```bash
   python app.py
   # open http://localhost:5000
   ```

## Screens (v3)

Navigation is grouped by purpose (PRD v3). The global filter bar (project /
developer / date range) follows you across screens.

| Screen | URL | What |
|--------|-----|------|
| My Day | `/my-day` | Per-ticket end-of-day checklist (traffic-light checks, gate-aware); roll-up at `/my-day/rollup`; raw activity feed at `/my-day/feed` |
| Attention Board | `/attention` | Every ticket needing intervention with stacked reason chips (Silent / Aging / Overdue / Blocked / Needs disposition / Missing dates), severity-sorted |
| QA Handoff | `/qa` | Handoff feed (transition-author credited), binary Pass/Needs-info checks, returned-from-QA with reasons, return rates with raw counts |
| Flow Analytics | `/flow` | Median/p85 cycle stats, per-ticket stage bars, bottleneck medians, multiple-active rule, focus view; Time in Status at `/reports/time-in-status` |
| Quality | `/quality` | Bug lens (median resolution), reopen loops, weekly return-rate trend |
| Sprint & Planning | `/planning` | Release Readiness interim view, sprint teaching state, gated hygiene/slip/reschedule tables, disposition compliance |
| Ticket Investigator | `/investigate?key=…` | Full forensic timeline with inactivity gaps and stage ribbon |
| Team Trends | `/exec` | Aggregates + week-over-week deltas, **Meeting Mode** (`?meeting=1`); legacy KPI dashboard at `/exec/kpis` |
| Settings | `/settings` | Status→bucket mapping, thresholds, feature gates, checklist config, keywords, board IDs, webhook |

Per-developer drill-downs from the old app remain at `/developer/<name>` and
`/developer/<name>/history`. Exports: CSV on every table, JSON under `/api/v2/`
(the combined `/api/reports.json` is deprecated but still responds).
Scheduled tasks: `POST /tasks/snapshot` stores the daily trend snapshot;
`?digest=1` also posts the Teams morning digest.

Admin-editable configuration lives in `settings.json` (`APP_CONFIG_PATH`,
default `./data/settings.json`) — status changes never require a deploy.
Snapshots in SQLite (`SNAPSHOT_DB_PATH`, default `./data/snapshots.db`).
See `docs/PRD_Jira_Developer_Reports_v3.md` and `docs/jira_process_setup.md`.

### Configuration for the executive reports

`config.py` maps your real workflow statuses to logical stages (To Do / In Progress /
Development / Ready for QA / QA Testing / Staging / Done / Reopened). The default map is
built from the LIFEDATAV2 workflow — edit it there, or point `JIRA_STATUS_MAP` at a JSON
override file. Other tunables (env-overridable): `DEV_OUTPUT_STAGE`,
`STUCK_THRESHOLD_DAYS`, `EXCLUDE_BLOCKED`, `RISK_W_*` risk weights, `JIRA_BOARD_IDS`
(enables Sprint Health), `STORY_POINT_FIELD`.

**Attribution:** developer and QA output are credited to the person who *performed the
transition* (changelog author), which is more accurate than current assignee. Workload
views still group by assignee.

**Module map:** `jira_client.py` (REST transport) → `analytics.py` (changelog math) →
`reports.py` (8 report builders) → `reports_web.py` (web pages) + `app.py` (v0 pages
and wiring). `config.py` holds the status mapping and settings. Run the test files in
the project to validate the metric logic.

## Making it a daily report

The app is the interactive version. For a pushed daily report, run the same logic
on a schedule and email/post the Excel file. A minimal cron entry:

```bash
# 6am daily: regenerate and email the workbook
0 6 * * *  cd /path/to/jira_report_app && python -c "import generate_and_send" 
```

(`generate_and_send.py` is left as a small exercise — it calls
`jira_client.build_report()`, writes the xlsx exactly like the `/report.xlsx`
route, and hands it to your mail/Slack sender.)

## Notes / things to tune

- **Cycle-time policy.** Tickets that go straight To Do → Done (e.g. admin tasks)
  never enter an In Progress status, so their cycle time is blank by design.
  Re-opened tickets use the *first* In Progress entry — adjust in
  `first_active_entry()` if you prefer a different rule.
- **Assignee vs. who did the work.** Reports group by current assignee, which is
  the standard basis but isn't always who moved the ticket. The changelog author
  is available if you ever want stricter attribution.
- **Scale.** Search is paged at 100/issue. For very large instances, consider the
  `/rest/api/3/changelog/bulkfetch` endpoint to batch changelog reads.
- Use these as a factual record of shipped work and to spot bottlenecks — not as
  raw head-to-head productivity scores, since ticket size and complexity vary.
