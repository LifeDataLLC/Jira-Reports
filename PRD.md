# Product Requirements Document — Developer Activity Report (Jira)

**Status:** Draft v2
**Owner:** Braden Sweet
**Contributing framework:** *Jira Executive Reporting Framework for LifeData LLC* — Amalesh Debnath
**Last updated:** 2026-06-19
**Hosting target (this phase):** Local / internal only

---

## 1. Overview

### 1.1 Problem

Engineering leadership at LifeData needs visibility into what the software team is
doing in Jira — how long in-progress tickets are taking, what has been completed,
and what each developer is currently carrying. Jira's native reports are shallow,
and the marketplace add-ons that provide this (Time in Status, EazyBI, etc.) carry
recurring per-user costs. The data those add-ons surface, however, is fully
available through the standard Jira REST API and each issue's changelog.

### 1.2 Solution

A self-hosted reporting application that reads Jira via the REST API (read-only API
token), computes engineering-flow metrics from the issue changelog, and presents
them as a web dashboard plus exportable reports. A working v0 already exists (Flask
app: team overview, per-developer pages, cycle time, status aging, throughput,
current assignments, Excel/JSON export). This PRD defines the requirements to mature
it into a dependable internal tool.

This version incorporates the *Jira Executive Reporting Framework for LifeData LLC*
(Amalesh Debnath), which expands the scope from a single developer-activity view to a
catalog of eight executive/management reports (see §12). A feasibility review confirms
every report is buildable on the same REST-API-plus-changelog foundation, hosted
locally, with no paid add-on. The few items that need more than raw Jira data — story
points, defect leakage, team capacity, and your specific workflow status names — are
called out explicitly in the assumptions (§8), risks (§9), and report catalog (§12).

### 1.3 Goals

- Give managers an at-a-glance, per-developer picture of completed work, work in
  progress, and current workload.
- Accurately measure how long work takes (cycle time) and where it gets stuck
  (status aging), derived from changelog history rather than guesswork.
- Replace paid add-ons for these specific reporting needs at zero per-seat cost.
- Be trustworthy: numbers must reconcile with what someone sees in Jira.
- Be low-maintenance: survive workflow/status renames and run unattended.

### 1.4 Non-goals (this phase)

- Cloud/SaaS hosting, multi-tenant support, or public exposure.
- Writing back to Jira (no transitions, edits, or comments — read-only).
- Replacing Jira boards or sprint planning tooling.
- Individual performance scoring or stack-ranking of developers.
- Real-time/live streaming updates (periodic refresh is sufficient).

---

## 2. Users & personas

| Persona | Needs | Primary views |
|---|---|---|
| **Engineering manager / boss** | Weekly/daily sense of throughput, stuck work, per-person workload; something to share upward | Team overview, per-developer pages, exported report |
| **Team lead** | Spot blocked/aging tickets, balance load across the team | Status aging, current assignments, in-progress |
| **Individual developer** | See their own open queue and what's aging | Their developer page |
| **Operator (whoever runs it)** | Easy local setup, clear config, reliable scheduled refresh | Config, logs, health |

---

## 3. Definitions (metric glossary)

These definitions are normative — implementation must match them, and the UI must
make them discoverable (tooltips/footnotes).

- **Completed (throughput):** count of issues that reached a `Done`-category status
  with a resolution date inside the reporting window, grouped by current assignee.
- **Lead time:** calendar time from issue `created` to `resolved` (days).
- **Cycle time:** time from the issue's **first** entry into any `In Progress`-category
  status to `resolved` (days). Tickets that never entered an In Progress status
  (e.g. To Do → Done admin items) have no cycle time by design.
- **Status aging / time-in-status:** for a currently open issue, time from its most
  recent status change to now (days). Surfaces stuck work.
- **Open age:** for an open issue, calendar time from `created` to now. Cheaper proxy
  used in workload lists where per-ticket changelog fetches are undesirable.
- **In progress:** issues whose current status category is `In Progress`.
- **Currently assigned / workload:** all unresolved issues assigned to a person
  (includes To Do not yet started).
- **Status category:** Jira's built-in classification (`To Do` / `In Progress` /
  `Done`). The app keys off category so custom status names (e.g. "In Progress /
  Start Investigation", "Development / In Design") are handled automatically.

Framework (executive report) metrics:

- **Status-transition events:** named movements detected from the changelog, e.g.
  *Tickets Started* (To Do → In Progress), *Development Started*, *Development
  Completed* (Dev → Ready for QA), *QA Verified* (Ready for QA → Done). Defined via a
  **status-mapping config** (§4.1) because LifeData's real status names differ from
  the framework's idealized names.
- **Developer output (primary KPI):** count of tickets a developer moved to *Ready for
  QA* in the window (per the framework). Configurable to *Done* if preferred.
- **Reopened count:** backward transitions *Ready for QA → Development* (rework signal).
- **Quality score:** `Completed / (Completed + Reopened)` per developer. A defined
  formula, not a stored field.
- **QA verified / QA rejection rate:** *Ready for QA → Done* vs *Ready for QA →
  Development*, per QA engineer.
- **Average development / testing duration:** elapsed time a ticket spends between the
  relevant status transitions (from the changelog).
- **Status duration (per status):** time each ticket spends in each status; averaged
  across tickets for the team-level bottleneck view.
- **Sprint completion:** completed vs total issues in the active sprint.
- **Scope change:** issues added / removed / moved between sprints after sprint start
  (from the Sprint field's changelog).
- **Spillover risk:** incomplete issues near sprint end.
- **Release progress / readiness %:** completion of issues under a `fixVersion`.
- **Release risk score:** a defined formula over open critical/high bugs, pending
  stories, and blocked tickets.
- **Velocity / story points delivered:** sum of story-point estimates of completed
  issues — **only available if the team populates a story-points field** (see §8/§9).
- **Team capacity:** planned availability of the team — **not native to Jira**; requires
  an external input (see §9).
- **Elapsed time vs logged time:** all durations above are *elapsed* status time from
  the changelog. "Hours" figures are elapsed, not Jira worklog/Tempo logged hours
  (which would require worklog data the team rarely fills in).

---

## 4. Functional requirements

Each requirement has an ID and a priority: **P0** (must, near-term), **P1** (should),
**P2** (nice-to-have). Items marked ✅ already exist in v0.

### 4.1 Data ingestion

- **FR-IN-1 (P0) ✅** Authenticate to Jira Cloud with an email + API token; read-only
  scopes only.
- **FR-IN-2 (P0) ✅** Query issues via JQL using the supported paginated search
  endpoint; retrieve all pages.
- **FR-IN-3 (P0) ✅** Retrieve issue changelogs to compute cycle time and status aging.
- **FR-IN-4 (P0)** Configurable project set (one or many). Default `LIFEDATAV2`;
  must support adding `V2BR`, `SUPPORT`, `KAN` without code changes.
- **FR-IN-5 (P1)** Configurable reporting window (e.g. 7/14/30 days, "this sprint",
  custom date range).
- **FR-IN-6 (P1)** Resilient API handling: retry with backoff on 429/5xx, respect
  rate limits, fail gracefully with a clear message if credentials are bad.
- **FR-IN-7 (P2)** Optional local persistence (SQLite) of fetched issues/changelog
  snapshots to enable trend history and reduce API load.
- **FR-IN-8 (P0)** **Status-mapping config:** a config file mapping LifeData's real
  workflow statuses (e.g. "Development / In Design", "Ready for QA (QA Env)", "In QA
  Testing (QA Env)", "Development Completed", "Reopen") to the framework's logical
  stages (To Do, In Progress, Development, Ready for QA, QA Testing, Done, Blocked).
  All transition-based metrics depend on this. *Foundational for Reports 1, 3, 4, 6.*
- **FR-IN-9 (P1)** **Agile/Sprint API:** read boards and sprints via `/rest/agile/1.0`
  (board → sprint → issues) to support sprint reporting. Confirmed feasible — the team
  uses sprints (Sprint field `customfield_10007`). Requires configured board IDs.
- **FR-IN-10 (P1)** Read **project versions** (`/rest/api/3/project/{key}/versions`)
  and filter by `fixVersion` for release reporting. Confirmed feasible — the team uses
  fix versions.
- **FR-IN-11 (P2)** Detect whether a **story-points field** is populated; expose
  velocity metrics only when it is, otherwise fall back to ticket counts.
- **FR-IN-12 (P2)** Optional **capacity input file** (per-person available days) to
  power team-capacity / utilization KPIs that Jira cannot supply alone.

### 4.2 Metrics & computation

- **FR-ME-1 (P0) ✅** Per-developer throughput, avg/median cycle time, lead time.
- **FR-ME-2 (P0) ✅** Status aging for in-progress issues; flag items over a threshold
  (default 14 days).
- **FR-ME-3 (P0) ✅** Current workload: all open assigned issues with status + open age.
- **FR-ME-4 (P1)** Configurable cycle-time policy (first vs. last In Progress entry;
  whether to subtract paused/blocked statuses from "active" time).
- **FR-ME-5 (P1)** Team-level rollups: totals, medians, and percentiles (p50/p85) for
  cycle time and aging.
- **FR-ME-6 (P2)** Trend over time: throughput and median cycle time per week/sprint
  (requires FR-IN-7 history).
- **FR-ME-7 (P2)** Work-type breakdown (Bug vs Task vs Story) per developer/team.
- **FR-ME-8 (P2)** Configurable working-days/business-hours calculation (exclude
  weekends) for cycle time and aging.
- **FR-ME-9 (P1)** Daily work-movement counts: created, started, dev-completed,
  QA-completed, blocked — derived from transitions in the day window. *(Report 1)*
- **FR-ME-10 (P1)** Developer productivity: output (→ Ready for QA), average
  development duration, reopened count, quality score. *(Report 3)*
- **FR-ME-11 (P1)** QA productivity: tickets verified, QA rejection rate, average
  testing duration. *(Report 4)*
- **FR-ME-12 (P1)** Per-status duration analysis per ticket and team averages, to rank
  bottleneck stages. *(Report 6)*
- **FR-ME-13 (P1)** Sprint metrics: completion %, status distribution, scope change
  (added/removed/moved), spillover risk. *(Report 2)*
- **FR-ME-14 (P1)** Release metrics: release progress %, open bugs by priority, pending
  testing, release risk score (defined formula). *(Report 7)*
- **FR-ME-15 (P2)** Velocity / story points delivered, when a points field is populated
  (FR-IN-11). *(Reports 2, 8)*
- **FR-ME-16 (P2)** Defect-leakage metric — **requires a labeling convention** (e.g. a
  "found in production" flag) before it can be computed. *(Report 8)*

### 4.3 Presentation (web UI)

- **FR-UI-1 (P0) ✅** Team overview: per-developer summary table + headline cards.
- **FR-UI-2 (P0) ✅** Per-developer page ordered **In progress → Completed → Currently
  assigned**, each ticket linking back to Jira.
- **FR-UI-3 (P1)** Filtering/sorting: by project, issue type, status, date window;
  sortable table columns.
- **FR-UI-4 (P1)** Visual charts: throughput bar, cycle-time distribution, aging
  histogram (client-side, no heavy deps).
- **FR-UI-5 (P1)** "Stuck work" view across the whole team (all aging items above
  threshold, regardless of assignee).
- **FR-UI-6 (P2)** Search/jump to a developer; remember last-used filters locally.
- **FR-UI-7 (P2)** Light/dark and print-friendly layout.
- **FR-UI-8 (P1)** Dedicated report views matching the framework: Daily Work Movement,
  Sprint Health, Developer Productivity, QA Productivity, Individual Activity, Status
  Duration, Release Readiness. *(Reports 1–7)*
- **FR-UI-9 (P1)** **Executive dashboard:** one-page summary for CEO/CTO/Product —
  delivery, productivity, quality, and risk KPIs in a top/middle/bottom-row layout.
  *(Report 8)*
- **FR-UI-10 (P2)** Visual indicators for risk (critical bugs, blocked, spillover,
  tickets stuck > N days) prominent on the executive view.

### 4.4 Exports & delivery

- **FR-EX-1 (P0) ✅** Excel export (summary + completed + in-progress + assigned sheets).
- **FR-EX-2 (P0) ✅** JSON API endpoint for automation.
- **FR-EX-3 (P1)** Static self-contained HTML snapshot export (single file, openable
  anywhere — basis for later SharePoint/email delivery).
- **FR-EX-4 (P1)** CSV export per table.
- **FR-EX-5 (P2)** Scheduled local generation: a CLI/cron entry that builds the report
  file(s) to a folder each morning (local-only this phase; delivery to
  email/Teams/SharePoint deferred to a later phase).

### 4.5 Configuration & operations

- **FR-OP-1 (P0) ✅** All secrets/config via environment variables; nothing hard-coded.
- **FR-OP-2 (P0)** Configurable HTTP port (avoid the macOS AirPlay :5000 clash).
- **FR-OP-3 (P1)** `.env` file support and a one-command run (script or Makefile).
- **FR-OP-4 (P1)** Structured logging + a `/health` endpoint reporting last refresh
  time and Jira connectivity.
- **FR-OP-5 (P1)** Configurable cache TTL and a manual "refresh now" control.
- **FR-OP-6 (P2)** Map Jira accountIds → display names / teams via a config file, so
  reports can group by squad and tolerate renames.

---

## 5. Non-functional requirements

- **NFR-1 Accuracy:** computed metrics must reconcile with Jira for spot-checked
  tickets; cycle-time/aging logic covered by automated tests using real changelog
  fixtures.
- **NFR-2 Performance:** overview page renders in < 3s on cached data; a full refresh
  for the active project completes in a reasonable time via pagination and avoids
  unnecessary per-ticket calls where a bulk query suffices.
- **NFR-3 Security:** read-only token; secrets never logged or committed; when bound
  beyond localhost, require authentication (deferred — local only this phase).
- **NFR-4 Reliability:** a Jira hiccup shows a friendly error, not a stack trace;
  partial data is labeled as such.
- **NFR-5 Maintainability:** metric logic isolated from transport and UI; new
  projects/statuses need config, not code; code documented.
- **NFR-6 Portability:** runs with `python3 + pip install -r requirements.txt` on
  macOS/Linux; no external services required for local hosting.
- **NFR-7 Privacy/ethics:** present metrics as flow/workload indicators, not
  individual performance scores; documentation states intended use.

---

## 6. Data model (logical)

- **Ticket:** key, summary, type, assignee (display + accountId), current status,
  status category, created, resolved, lead_days, cycle_days, days_in_status, age_days,
  project, url.
- **DeveloperReport:** name, accountId, completed[], in_progress[], assigned[], and
  derived rollups (throughput, avg/median cycle, oldest WIP, open count).
- **(Phase 2+) Snapshot:** date, project, per-developer + per-ticket metrics persisted
  to SQLite for trend history.

---

## 7. Success metrics

- Manager can answer "how long are tickets taking and what's stuck?" without opening
  Jira, in under a minute.
- Reported numbers reconcile with Jira on audit (≥ 95% of spot-checked tickets exact).
- Zero recurring license cost for the covered reporting needs.
- Adopted as the team's weekly/daily reporting source within one month of rollout.
- Operator can stand it up locally from the README in < 15 minutes.

---

## 8. Assumptions & dependencies

- Jira Cloud (lifedata.atlassian.net) with REST API v3 access and a read-only token.
- Workflow statuses carry correct Jira status categories.
- Reporting is based on **current assignee**; acknowledged this isn't always who did
  the work.
- Python 3.9+ available on the host.
- A **status-mapping config** will be authored once for the LifeData workflow; the
  framework's transition metrics depend on it (FR-IN-8).
- The team **uses sprints** (confirmed: Sprint field `customfield_10007`) and **fix
  versions** (confirmed), enabling Reports 2 and 7.
- **Story points:** to be confirmed whether a points field is populated. If not,
  velocity/story-point KPIs are replaced with ticket-count equivalents.
- **Defect leakage** requires a labeling/convention decision before it can be reported.
- **Team capacity** requires an external availability input; it is not in Jira.
- Duration metrics are **elapsed status time** from the changelog, not logged worklog
  hours.

---

## 9. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Changelog edge cases (re-opens, pauses, renames) skew cycle time | Wrong numbers | Category-based classification; configurable policy; test fixtures from real tickets |
| Assignee ≠ actual contributor | Misattributed work | Document clearly; optional changelog-author attribution later |
| API rate limits / large instances | Slow or failed refresh | Pagination, backoff, caching, optional bulk changelog endpoint |
| Metrics misused as performance scores | Team trust / morale | Explicit framing in UI and docs as flow indicators |
| Secret leakage | Security | Env-only secrets, no logging of tokens, local-only binding |
| Local host only — single user reach | Limited adoption | Static HTML export now; plan for shared hosting in a later phase |
| Status names don't match framework's idealized stages | Transition metrics wrong | Status-mapping config (FR-IN-8) authored from the real workflow; verified against changelog |
| Story-points field not populated | Velocity KPIs unavailable | Detect at runtime (FR-IN-11); fall back to ticket counts |
| Defect leakage undefined | Metric can't be computed/misleading | Require a labeling convention before enabling (FR-ME-16) |
| Team capacity not in Jira | Utilization KPI incomplete | Optional external capacity input (FR-IN-12); otherwise omit |
| "Hours" misread as logged worklog time | Misinterpretation | Label all durations as elapsed status time |
| Team-managed (next-gen) project quirks in Agile API | Sprint data gaps | Validate board/sprint endpoints against the actual board early |

---

## 10. Out of scope / future phases

- Shared hosting (Azure App Service), Teams tab embedding, SharePoint/email delivery.
- Single sign-on (Entra ID) and per-user access control.
- Historical trend warehouse and forecasting.
- Cross-tool correlation (Git/PR data, deploys).

---

## 11. Open questions

1. Which projects beyond `LIFEDATAV2` are in scope for the first managed rollout?
2. Preferred default reporting window (14 days vs. current sprint)?
3. For cycle time, should paused/blocked statuses be excluded from "active" time?
4. Should reports group by individual, by squad, or both?
5. What's the aging threshold that should flag a ticket as "stuck" (default 14d / framework uses 7d)?
6. Does the team populate a **story-points** field? (Determines velocity KPIs.)
7. What is the precise **status mapping** from the LifeData workflow to the framework's
   stages (To Do / In Progress / Development / Ready for QA / QA Testing / Done / Blocked)?
8. Is the primary developer KPI **"moved to Ready for QA"** (per framework) or **Done**?
9. How should **defect leakage** be identified (label, found-in-production flag)?
10. What is the **release risk score** formula (weights for critical/high bugs, pending
    stories, blocked)?
11. Source for **team capacity** (PTO/availability) if utilization KPIs are wanted?
12. Which **board(s)** define the active sprints for Report 2?

---

## 12. Report catalog (Executive Reporting Framework)

Eight reports from Amalesh's framework, with feasibility verdict and key dependencies.
All are buildable on the REST-API-plus-changelog foundation, hosted locally.

| # | Report | Audience | Feasible | Key dependency |
|---|--------|----------|----------|----------------|
| 1 | **Daily Work Movement** — created / started / dev-completed / QA-completed / blocked today | Mgmt, leads | ✅ Yes | Status-mapping config (FR-IN-8) |
| 2 | **Sprint Health** — completion %, status distribution, scope change, spillover | Leads, PM | ✅ Yes | Agile API + board IDs (FR-IN-9); sprint-field changelog |
| 3 | **Developer Productivity** — output (→ Ready for QA), avg dev duration, reopened, quality score | Eng mgmt | ✅ Yes | Status mapping; quality-score formula |
| 4 | **QA Productivity** — verified, rejection rate, avg testing duration | Eng/QA mgmt | ✅ Yes | Status mapping |
| 5 | **Individual Activity** — per-person history, durations, delivered, pending | Mgmt | ✅ Yes | Changelog (already close in v0) |
| 6 | **Status Duration Analysis** — time per status, bottleneck ranking | Leads | ✅ Yes | Changelog (our cycle-time engine generalized) |
| 7 | **Release Readiness** — progress %, open bugs by priority, pending QA, risk score | PM, leadership | ✅ Yes | fixVersions (confirmed); risk-score formula |
| 8 | **Executive Dashboard** — one-page delivery/productivity/quality/risk KPIs | CEO/CTO/PM | ⚠️ Mostly | Aggregates 1–7; **story points** and **team capacity** are conditional (see §8/§9) |

**Verdict:** ~95% of the framework is directly implementable now, locally, with no paid
add-on. The conditional items are not technical blockers — they need a decision
(defect-leakage convention, risk-score formula), a field to be populated (story
points), or an external input (team capacity).
