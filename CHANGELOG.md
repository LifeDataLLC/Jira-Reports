# Changelog

## v3 migration (PRD_Jira_Developer_Reports_v3)

### Phase 0 — Config foundation
- Persistent admin-editable settings store (`settings.py` + JSON at `APP_CONFIG_PATH`,
  default `./data/settings.json`), seeded from the legacy `config.py` stage map.
- Status→bucket classification (todo / active_dev / qa_stage / paused / rework / done),
  per-status aging thresholds with bucket defaults, feature gates (all default off),
  checklist toggles, handoff window, keyword/label lists, board IDs, webhook URL.
- `/settings` admin screen; unmapped statuses excluded from metrics and flagged
  with a banner on every v3 screen.

### Phase 1 — Activity feed, My Day, Attention Board, nav, deprecations
- FR-D3: changelog extraction of due-date, start-date, Flagged, and sprint changes
  (start-date field auto-detected and stored in settings); `fetch_single_issue`
  for the Investigator.
- `activity.py`: unified per-ticket/per-developer event feed (PRD §3.3).
- `checklist.py`: My Day engine — per-ticket pass/fail/n-a checks (gated checks
  return n-a); `/my-day` screen with traffic-light chips, `/my-day/rollup`
  (% EOD signal), `/my-day/feed` (audit lens) + `/api/v2/` JSON/CSV.
- `attention.py`: Attention Board — Silent + Aging reasons (stacked chips,
  severity sort); gated Overdue/Blocked/Missing-dates rules ship dark;
  stateless disposition rule. `/attention` + exports.
- Purpose-grouped nav (My Day / Attention / QA / Flow / Quality / Planning /
  Investigate / Trends / Settings) + global filter bar with localStorage.
- Deprecations per migration map: v0 overview, Daily Movement, Developer/QA
  Productivity, Status Duration pages and the /dev-reports catalog now 301 to
  their v3 screens with hit logging; engines retained. /api/reports.json gains
  a deprecation notice; old planning docs archived under docs/.

### Phase 2 — QA Handoff + Ticket Investigator
- `qa_handoff.py`: bucket-edge-driven handoff feed (any entry into qa_stage
  counts, incl. skip-RFQA edges), binary Pass/Needs-info checks (handoff comment
  within the configured window + PR/build URL or keyword), returned-from-QA feed
  with return-reason comments, and return-rate summary attributed to the most
  recent handoff author — raw counts always shown.
- `/qa` screen + CSV/JSON exports (`/api/v2/handoffs.csv`, `/api/v2/returns.csv`,
  `/api/v2/qa.json`).
- `/investigate`: full chronological timeline (transitions, assignee changes,
  comments, worklogs, field changes) with inactivity-gap spacers (configurable),
  bucket stage-duration ribbon, Jira deep link, optional date range; falls back
  to a live uncached single-issue fetch when the key is outside the sync window.

### Phase 3 — Flow Analytics + Quality + explain-this-number
- `analytics.percentile`; all new duration displays are median/p85 with raw
  counts (PRD §3.5) — averages never shown alone.
- `flow_quality.py` + `/flow`: cycle stats (median/p85 dev→QA and cycle),
  per-ticket stage-share stacked bars (bucket-colored, CSS only), team
  bottleneck medians per status, multiple-active rule (active_dev only,
  qa_stage excluded), Focus view. CSV + `/api/v2/flow.*`.
- `/quality`: bug lens per developer (median resolution hours, return rate with
  raw counts), reopen-loop (≥2 rework cycles) highlighting, weekly team
  return-rate trend. CSV + `/api/v2/quality.*`.
- `metrics_glossary.py`: single definitions dict rendered as hover tooltips
  (FR-U5) so UI and docs cannot drift.

### Phase 4 — Process-gated features (built dark)
- `planning.py`: due-date slip metrics (original = first changelog value, push
  count, slip days), start-date Reschedule Count + Total Days Pushed, missing-
  date and no-estimate checks — all behind their gates; flipping a gate lights
  them up with zero deploy.
- Attention Board Overdue / Missing-dates reasons now activate with the gates;
  Blocked (Flagged changelog primary, labels low-confidence) live from Phase 1.
- Disposition compliance metric (% dispositioned within 48h, stateless from
  the changelog) on the new /planning screen.
- /planning: sprint teaching empty state, Release Readiness as interim
  commitment view, hygiene tables; docs/jira_process_setup.md documents each
  feature's Jira-side prerequisite.

### Phase 5 — Trends, Meeting Mode, snapshots, digest, role landing
- `snapshots.py`: SQLite daily team aggregates (no individual names) +
  week-over-week deltas; `POST /tasks/snapshot` endpoint for cron/WebJob.
- `/exec` is now Team Trends: six aggregate cards with wk/wk deltas and a
  **Meeting Mode** (names hidden, distributions, large type); the legacy KPI
  dashboard moved to `/exec/kpis`.
- `digest.py`: Teams Adaptive Card morning digest (top 5 attention items +
  4 aggregates) via `?digest=1` on the snapshot endpoint; webhook URL in
  Settings (the one permitted settings secret).
- Sprint Health now gated by Settings (`sprints_enabled` + board IDs) instead
  of env, with the teaching empty state.
- Role-based landing on `/` (developer→My Day, lead→Attention, exec→Trends)
  via `?role=` or the Settings default.

### Post-phase polish
- FR-U6: click-to-sort (numeric-aware) on every v3 table; sticky headers.
- FR-A4: "Copy nudge" button on Attention rows — polite pre-written Teams
  message with the ticket link copied to clipboard.
- FR-D6: retry/backoff (Retry-After honored) on Jira search calls.
- Known deferrals: per-project checklist scoping (FR-C3 is global for now) and
  Jira dev-status PR links (FR-D4 uses URL/keyword detection).

### Ops: settings persist on Azure with no manual config
- settings.data_dir() detects Azure App Service (WEBSITE_SITE_NAME) and stores
  settings.json + snapshots.db under the persistent /home/data mount, which
  survives deploys — so Settings saving works out of the box. Local dev still
  uses ./data; APP_DATA_DIR / APP_CONFIG_PATH / SNAPSHOT_DB_PATH override.

### Dev Team Rules — LIFEDATAV2 workflow applied
- workflow.py encodes the full workflow: every status → bucket, the 5 active
  statuses with lane (dev/qa/staging/production) + pause counterpart, and
  parked-state thresholds. Seeds the settings store; re-applied via a "Load
  LIFEDATAV2 workflow" button on /settings.
- Rule 1 (one active per lane): Flow → Multiple active tickets is now lane-aware
  (dev + each testing lane enforced independently).
- Rule 3 (pause at EOD): My Day "Paused for end of day" check + Attention
  "Not paused" reason for tickets left active overnight (names the pause target).
- Rule 5 (belongs to a release): fixVersion ingested; My Day "Belongs to a
  release" check + Attention "No release" reason.
- Rules 4 & 6 (worklog + due date) enabled by the workflow; Rule 2 already via
  Silent/Aging; Rule 7 via Settings. docs/dev_team_rules_mapping.md added.

### Terminology: "active" vs "in-flight"
- Reserved "active status / in progress / actively working" for the 5 blue
  statuses only (is_active_status) = currently being worked, one per lane.
- "in-flight" = a developer's open assigned work (active, paused, QA, or
  reopened) — used where the broad set is meant, never called "active".
- Roll-up now measures % of tickets in an ACTIVE or PAUSED status with an EOD
  signal (active includes testing lanes; queue states excluded), relabeled
  accordingly. My Day lists in-flight tickets and marks each active one with an
  "⚡ active" chip. Planning/Trends labels de-ambiguated. Glossary defines
  active_status / in_flight / eod_signal.
