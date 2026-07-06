# Implementation Instructions — Jira Reports App v3 Migration

**Audience:** Claude Code session working in the `jira_report_app` repo (the app now hosted in Azure).
**Inputs:** This file + `PRD_Jira_Developer_Reports_v3.md` (the spec) + `Jira_Developer_Reports_Analysis_and_Recommendations.docx` (rationale and comment answers — consult when a requirement seems ambiguous).
**Rule of precedence:** PRD v3 > this file > the analysis doc > the old PRD.md/IMPLEMENTATION_PLAN.md in the repo (which are superseded — see §2).

---

## 1. Codebase context (what you should find in the repo)

- `jira_client.py` — Jira REST fetch layer: issues + changelogs, paged, cached (~5 min), env-config (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECTS`, `JIRA_WINDOW_DAYS`, `PORT`, `JIRA_BOARD_IDS`).
- `analytics.py` — changelog analytics engine: per-ticket status timeline, stage durations, pause/reopen detection, window-clipping (`clip to window` logic used by Time in Status).
- `config.py` — status → logical-stage mapping for LIFEDATAV2 (hardcoded Python dict today).
- `reports.py` — builders for the 8 "framework" reports (Daily Work Movement, Developer Productivity, QA Productivity, Individual Activity, Status Duration Analysis, Release Readiness, Sprint Health [dormant], Exec Dashboard) + Time in Status.
- `reports_web.py` — Flask blueprint serving those reports; `app.py` — v0 pages (team overview, per-developer workload pages), Excel/JSON exports, blueprint registration.
- Tests against fixtures modeled on real tickets; `Dockerfile`, `CONTRIBUTING.md`, `requirements.txt` (Flask, requests, openpyxl).

If module names differ slightly, map by responsibility, not name. **Read the whole repo before changing anything.**

## 2. Supersede the old planning docs

- Move `PRD.md` and `IMPLEMENTATION_PLAN.md` to `docs/archive/` with a one-line deprecation banner at the top of each pointing to `PRD_Jira_Developer_Reports_v3.md`.
- Copy the three input documents into `docs/`.
- Update `README.md` and `CONTRIBUTING.md` to describe the v3 screen structure once Phase 1 lands.

## 3. Migration map — existing reports → v3 screens

**Do not delete report builders; refactor and re-home them.** The engines are validated; it's the navigation/screen layer that changes.

| Existing report/route | Fate |
|---|---|
| Exec Dashboard (`/exec`) | **Evolves** into Team Trends (PRD §4.10). Keep route; add discipline aggregates + Meeting Mode in Phase 5. |
| Daily Work Movement | **Absorbed** into the Activity Feed (FR-M5) and My Day engine. Retire the standalone page in Phase 1; 301-redirect its route to the activity feed view. |
| Developer Productivity | **Split**: transition-author throughput → QA Handoff feed (FR-Q1) + Quality screen; quality score → **remove** (PRD forbids decimal scores on people; replace with binary Pass/Needs-info and raw counts). |
| QA Productivity | **Absorbed** into QA Handoff screen (returned list + rejection rates, FR-Q3/Q4). Redirect. |
| Individual Activity | **Absorbed** into My Day (per-developer view) + per-developer drill-down. Redirect. |
| Status Duration Analysis | **Absorbed** into Flow Analytics (FR-F3 bottleneck view). Redirect. |
| Release Readiness | **Keep as-is**, re-homed under Sprint & Planning (FR-S2). |
| Sprint Health | **Keep dormant** code; re-home under Sprint & Planning behind the `sprints_enabled` gate with a teaching empty state (FR-S4). |
| Time in Status (`/reports/time-in-status`) | **Keep as-is**, re-homed under Flow Analytics (FR-F4). Keep route. |
| v0 Workload pages (team overview, per-dev) | Per-dev page **becomes the shell** for My Day; team overview's "open assigned" column feeds FR-F5. Retire v0 overview once My Day + Attention Board exist; redirect. |
| Excel/CSV/JSON exports | **Keep**; extend to new screens (FR-U7). Version the combined JSON feed: keep `/api/reports.json` responding (deprecation notice field) and add `/api/v2/…` per screen. |

**Deprecation mechanics:** add a `DEPRECATED_ROUTES = {old: new}` dict; register a catch-all that 301s and logs hits. Remove redirects only after logs show zero traffic for a month (leave that judgment to the team; just build the logging).

## 4. Build order

Work phase by phase. **Each phase = one PR-sized unit with tests passing before moving on.** Keep the app deployable at every step.

### Phase 0 — Config foundation (do first, everything depends on it)

1. Create a persistent config store (`settings.py` + `settings.json` on a volume path from env `APP_CONFIG_PATH`, default `./data/settings.json`; SQLite acceptable if you're also doing snapshots early). Contents: status→bucket map, per-status thresholds, feature gates (`worklogs_required`, `estimates_used`, `due_dates_required`, `start_dates_required`, `sprints_enabled` — all default **false**), checklist item toggles, handoff window, keyword/label lists, board IDs.
2. Seed it from the current `config.py` mapping on first run; `config.py` becomes a fallback/seed only.
3. Buckets: `todo`, `active_dev`, `qa_stage`, `paused`, `rework`, `done` (PRD §3.1). Migrate existing stage names to these.
4. Admin Settings screen (`/settings`): list every status seen in synced data; assign bucket via dropdown; set thresholds; toggle gates. Statuses present in data but unmapped get a prominent warning banner on all screens ("2 statuses need classification — metrics exclude them").
5. Unit tests: config load/save/seed, unmapped-status detection.

### Phase 1 — Ingestion + My Day + Attention Board

1. **Ingestion (FR-D1–D3):** extend `jira_client.py` to fetch comments and worklogs per issue (paged; `/rest/api/3/issue/{key}/comment`, `/worklog`). Extract additional changelog items: `duedate`, start date field (identify the field id — team-managed projects usually `Start date`; detect by name), `Flagged`, sprint field. Build the **unified activity feed** structure (PRD §3.3) in `analytics.py` or new `activity.py`. Be surgical about API cost: fetch comments/worklogs only for issues updated in the sync window; cache per-issue by `updated` timestamp.
2. **My Day (FR-M1/M2/M4):** new `checklist.py` engine — takes a developer + date + config, returns checklist rows. Checks are pure functions over the activity feed and issue fields; each check returns pass/fail/not-applicable (gated checks return n-a when their gate is off). New template with traffic-light chips; admin roll-up view (% tickets with EOD signal).
3. **Attention Board (FR-A1/A2):** new `attention.py` — evaluates every non-done ticket against reason rules (Silent: no activity-feed event in N days while in `active_dev`/`rework`; Aging: over per-status threshold). Reasons stack per ticket. Severity = max(days over threshold). One screen, filterable, reason chips.
4. **Nav + filter bar (FR-U1/U2):** restructure base template: My Day / Attention / QA / Flow / Quality / Planning / Investigate / Trends / Settings. Global filter bar persisting via query params + localStorage.
5. **Deprecations** per §3 table.
6. Tests: checklist engine (each check, each gate), attention reasons (boundary days), redirects.

### Phase 2 — QA Handoff + Ticket Investigator

1. **QA Handoff (FR-Q1–Q4):** handoff feed from transitions into `qa_stage` first-status (or any `active_dev`→`qa_stage` edge — make the edge definition config-driven); returned list from `qa_stage`→(`active_dev`|`rework`) edges. Handoff checks: comment by transition author within window before transition; PR/URL pattern in comments (FR-D4). Output Pass/Needs-info only.
2. **Ticket Investigator (FR-T1–T4):** route `/investigate?key=…`; merge feed chronologically; render vertical timeline (plain HTML/CSS is fine — icons per event type, gap spacers for inactivity ≥ threshold, stage ribbon on top). Deep link to Jira.
3. Tests: edge detection with the real status names from fixtures; timeline ordering; gap detection.

### Phase 3 — Flow + Quality

1. Switch all duration displays to median/p85 (add percentile helpers in `analytics.py`); show counts next to rates everywhere.
2. Stage-breakdown stacked bars per ticket (server-rendered CSS bars are acceptable; no JS framework needed) + team bottleneck medians (FR-F2/F3).
3. Multiple-active rule (FR-F5) and Focus view (FR-F6) from the activity feed.
4. Quality screen (FR-QL1–QL3): bug-type lens over existing productivity/reopen machinery; reopen-loop (≥2 rework cycles) highlighting.
5. "Explain this number" tooltips (FR-U5) — definitions from PRD §3; keep them in one `metrics_glossary.py` dict so docs and UI can't drift.

### Phase 4 — Process-gated features (build gated, ship dark)

Implement behind the gates so flipping a config bool lights them up with zero deploy:

1. Overdue + slip metrics (FR-S3, Attention reason `Overdue`): original due date = first `duedate` changelog value (or current value if never changed); push count; slip days.
2. Start-date rules + Reschedule Count; Attention reason `Missing dates`.
3. Blocked (FR-A1 `Blocked` reason): Flagged changelog primary; labels/links as low-confidence hints, visually distinguished.
4. Disposition tracking (FR-A3): flag-time recorded in the snapshot store; resolved by observing a Backlog move or future start date in the changelog.

### Phase 5 — Trends, Meeting Mode, Sprint, digest

1. SQLite snapshot store (`snapshots.db`, path env-configurable): nightly job (APScheduler or a `/tasks/snapshot` endpoint hit by cron/Azure WebJob — prefer the endpoint; it's container-friendly) storing team aggregates per day.
2. Team Trends on `/exec`: aggregates + week-over-week deltas (FR-X1); **Meeting Mode** toggle (FR-X2) — a template variant hiding names, showing distributions, `font-size` scaled up.
3. Sprint Health enablement: wire `sprints_enabled` + board IDs from settings (not env); teaching empty state until then.
4. Teams webhook digest (FR-U8): `digest.py` posting an Adaptive Card (top 5 attention items + 4 team aggregates) to a webhook URL from settings; triggered by the same scheduled endpoint.
5. Role-based landing (FR-X4): simple `?role=` / settings default; no auth build-out.

## 5. Conventions & guardrails

- **Read-only Jira.** Never add write calls. Enforcement ideas (required comments, required fields) are Jira-side Automation — document them in `docs/jira_process_setup.md` for the admins, don't implement them here.
- **No decimal quality scores on individuals** anywhere in UI or exports. Binary checks + raw counts + medians.
- **Never guess unmapped statuses.** Exclude + warn.
- **Never block a page on a live Jira pull.** Serve cache, show freshness timestamp, refresh in background thread.
- **Every new table**: CSV export + JSON under `/api/v2/`, sortable headers, issue keys deep-linked to `JIRA_BASE_URL/browse/{key}`.
- **Tests are non-negotiable** for metric engines — extend the existing fixture pattern (real LIFEDATAV2-shaped changelogs, including: a paused ticket, a reopen loop, a QA-parked ticket, a due-date pushed ticket).
- **Stack discipline:** stay Python/Flask/openpyxl; server-rendered templates; no SPA framework, no new services. SQLite + JSON files only. Azure deployment (gunicorn/Docker) must keep working — don't change the entrypoint contract.
- **Secrets:** credentials remain env-only; settings store must contain no secrets except the Teams webhook URL (acceptable) — note that in docs.

## 6. Definition of done (per phase)

1. All tests pass (`python -m pytest` or the repo's existing runner); app boots with **no settings file** (seeds itself) and with an existing one.
2. Docker image builds; gunicorn entrypoint unchanged.
3. Deprecated routes 301 to their replacements and log.
4. README screen list updated; CHANGELOG entry appended.
5. A `docs/jira_process_setup.md` grows alongside: each gated feature's Jira-side prerequisite documented as you build it (due-date validator/automation, Flagged usage, Reopen-requires-comment, sprint board setup).

## 7. Questions you may hit — pre-answered

- **"What's the start-date field id?"** Detect at runtime by field name (`Start date`); store the resolved id in settings; expose it on the Settings screen for override.
- **"Which transition counts as the handoff if a ticket skips Ready for QA?"** Any `active_dev`→`qa_stage` edge counts; use the first `qa_stage` status entered. Config-driven edge definitions.
- **"How to attribute a return when QA reassigns then transitions?"** Attribution is always the changelog author of the transition itself. Show current assignee separately.
- **"Reopened ticket cycle time?"** Keep current policy (first `active_dev` entry → first `done`); additionally expose rework-loop count and total rework time as separate columns. Don't mix them into one number.
- **"What if comments API pagination is slow on old tickets?"** Sync window default 30–60 days for comments/worklogs; the Investigator may do a live, uncached full fetch for its single ticket.
- **Anything else ambiguous:** check the analysis doc's per-report section and comment answers (§3/§5); if still unclear, choose the option that is config-gated and reversible, and note it in the CHANGELOG.
