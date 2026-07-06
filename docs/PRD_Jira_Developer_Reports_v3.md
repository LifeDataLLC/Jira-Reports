# PRD — Jira Developer Reports Platform (v3)

**Date:** July 6, 2026
**Status:** Draft for implementation
**Supersedes:** PRD.md v2 in the repo (Amalesh framework integration) and the 18-report catalog in *Jira_Developer_Reports_Definition*
**Companion documents:** *Jira_Developer_Reports_Analysis_and_Recommendations* (rationale, comment responses) and *IMPLEMENTATION_INSTRUCTIONS_v3.md* (how to apply this to the existing codebase)

---

## 1. Overview

### 1.1 Problem

Leadership needs daily visibility into developer discipline (updates, handoffs, focus), work health (stuck/silent/blocked/overdue tickets), and delivery flow (cycle time, QA returns, sprint reliability) — without manually opening tickets or calling people out publicly. The original spec defined 18 standalone reports; review comments (Kevin Eklund, Braden Sweet) surfaced overlaps, unanswered definitional questions, and missing process prerequisites.

### 1.2 Solution

Consolidate into **seven purpose-built screens plus a team-level trends layer**, built on the existing Azure-hosted Flask app (changelog analytics engine, status mapping config, exports). Every output column from the original 18 reports lands on one of these screens. Reports whose prerequisites don't exist yet (worklogs, story points, sprint boards, due-date policy) are gated behind explicit configuration flags rather than shipped broken.

### 1.3 Goals

1. A developer can clear their day in <2 minutes using a per-ticket checklist ("My Day").
2. A lead can see every ticket needing intervention on one screen with reasons ("Attention Board").
3. Team meetings use aggregate trends, never individual call-outs ("Meeting Mode").
4. Workflow/status changes never require a code deploy (admin-editable configuration).
5. All metrics are changelog-derived, attribution-fair (transition author, not just assignee), and median-based.

### 1.4 Non-Goals

- Writing to Jira (app stays read-only; enforcement happens via Jira Automation/workflow validators).
- Story-point/velocity metrics until the team adopts estimation (config-gated).
- Worklog-completeness enforcement until a worklog policy exists (config-gated).
- Individual performance scoring/ranking. Metrics are flow and discipline indicators.

---

## 2. Personas

| Persona | Primary surface | Needs |
|---|---|---|
| **Developer** | My Day | End the day clean: know exactly what's missing on each active ticket |
| **Team Lead / Admin (e.g., Kevin)** | Attention Board, QA Handoff, Quality | Intervene early, coach privately, run standups from data |
| **Engineering Management** | Team Trends / Meeting Mode | Weekly trends, sprint reliability, no individual exposure |
| **Investigator (any role)** | Ticket Investigator | Forensic answer to "why did this ticket take 6 weeks?" |

---

## 3. Core Concepts and Definitions

### 3.1 Status classification (foundational)

Every workflow status is assigned to exactly one bucket. Buckets drive all metrics. Must be **admin-editable in the UI** (stored config, not hardcoded).

| Bucket | Draft LIFEDATAV2 mapping (confirm against live workflow) |
|---|---|
| `active_dev` | In Progress / Start Investigation; Development / In Design; other development statuses |
| `qa_stage` | Ready for QA (QA Env); In QA Testing (QA Env); QA Review Completed |
| `paused` | Pause Investigation |
| `rework` | Reopen |
| `done` | Development Completed; Closed; Done; Released |
| `todo` | To Do; Backlog; anything pre-work |

Unmapped statuses appear in the admin UI as "needs classification" and are excluded from rule-based metrics (never silently guessed).

### 3.2 Per-status aging thresholds

Each status gets a max-days threshold (admin-editable, with bucket-level defaults, e.g. `qa_stage` statuses default to 2–3 days). Exceeding a threshold puts the ticket on the Attention Board. This is what catches "parked in QA Review Completed forever" (the Tanvir case) without polluting the active-ticket count.

### 3.3 Activity feed

Unified event stream per ticket and per developer: status transitions, assignee changes, comments, worklogs, due-date changes, start-date changes, flag changes, sprint-field changes. Sourced from changelog + comments API + worklogs API. This feed powers My Day, the checklist, Focus, the Investigator, and Daily Activity views.

### 3.4 Attribution

Throughput and quality events are credited to the **changelog author of the transition** (who moved it), not the current assignee. Current assignee is shown alongside for context.

### 3.5 Statistical defaults

Medians and p85 for all durations; averages never shown alone. Every rate displays its underlying counts (e.g., "50% return rate (1 of 2)").

### 3.6 Date discipline metrics (changelog-derived)

- **Start-date rule:** every `active_dev` ticket must have a start date; not-yet-started tickets must have start date ≥ today. **Reschedule Count** = number of start-date changes; **Total Days Pushed** also tracked.
- **Due-date slip:** **Original Due Date** = first value ever set; **Push Count** = number of due-date changes to a later date; **Slip Days** = current − original.
- **Disposition rule:** a ticket flagged over-threshold must either move to Backlog or receive a future start date within 48h. **Disposition Compliance** = % dispositioned within 48h.

### 3.7 Blocked convention

Standard signal: Jira **Flagged** field ("Flagged as impediment") + a reason comment. Days Blocked computed from the flag's changelog timestamp. Labels (`blocked`, `waiting`, `dependency`) and "is blocked by" links are secondary detection hints, shown but marked lower-confidence.

---

## 4. Functional Requirements

Priorities: **P0** = first release, **P1** = fast follow, **P2** = gated on process decisions/config.

### 4.1 Data ingestion

| ID | Requirement | Pri |
|---|---|---|
| FR-D1 | Ingest issue comments (author, timestamp, body) for all synced issues | P0 |
| FR-D2 | Ingest worklogs (author, started, timeSpent, comment) | P0 |
| FR-D3 | Extract from changelog: due-date changes, start-date changes, Flagged changes, sprint-field changes (in addition to existing status/assignee extraction) | P0 |
| FR-D4 | Detect PR/build references: Jira dev-status links where available; else URL patterns (GitHub/Azure DevOps/Bitbucket) in comments | P1 |
| FR-D5 | Sprint data via Agile API when `JIRA_BOARD_IDS` configured | P2 |
| FR-D6 | Incremental sync strategy: fetch only issues updated since last sync where possible; respect API rate limits with retry/backoff | P1 |

### 4.2 Configuration & admin (Screen 0 — Settings)

| ID | Requirement | Pri |
|---|---|---|
| FR-C1 | Admin settings screen: assign every discovered status to a bucket (drag or dropdown); unmapped statuses prominently flagged | P0 |
| FR-C2 | Per-status aging thresholds with bucket defaults | P0 |
| FR-C3 | Checklist item configuration: enable/disable each My Day check per project (see FR-M2) | P1 |
| FR-C4 | Feature gates: `worklogs_required` (default off), `estimates_used` (default off), `due_dates_required` (default off), `start_dates_required` (default off), `sprints_enabled` (default off). Gated UI elements show an explanatory empty state, not an empty table | P0 |
| FR-C5 | Config persisted server-side (JSON file or SQLite), versioned, survives restarts; env vars remain for credentials only | P0 |
| FR-C6 | Handoff-comment window (default: comment within 4h before transition), keyword lists, blocked-label list — all editable | P1 |

### 4.3 Screen 1 — My Day (developer home)

Absorbs original reports 1, 4, 9, 13.

| ID | Requirement | Pri |
|---|---|---|
| FR-M1 | Per-developer view: one checklist row per ticket in `active_dev`/`rework` (and optionally `qa_stage` tickets they own) | P0 |
| FR-M2 | Checklist items (each ✓/✗/n-a): status is current bucket-appropriate; comment added today; worklog logged today (gated on `worklogs_required`); start date present/valid (gated); due date present (gated); not over aging threshold; if moved to Ready-for-QA today → handoff comment present; if blocked → reason comment present | P0 |
| FR-M3 | Date selector (default today) so a developer or lead can review any past day | P1 |
| FR-M4 | Admin roll-up: % of active tickets with an EOD signal, per day — the compliance view of the same engine | P0 |
| FR-M5 | Raw activity feed view (filterable by project/developer/date) retained as the audit lens | P1 |

### 4.4 Screen 2 — Attention Board (lead home)

Absorbs original reports 2, 8, 11, 14.

| ID | Requirement | Pri |
|---|---|---|
| FR-A1 | One row per ticket needing intervention, with stacked **reason tags**: `Silent Nd` (no update in N days while active), `Aging Nd` (over status threshold), `Overdue Nd` (gated on due dates), `Blocked` (Flagged), `Needs disposition`, `Missing dates` (gated) | P0 (Silent + Aging), P2 (Overdue, Blocked, dates) |
| FR-A2 | Severity sort (worst first), filters by project/developer/reason | P0 |
| FR-A3 | Disposition tracking: over-threshold tickets flagged until moved to Backlog or given a future start date; disposition compliance metric computed | P1 |
| FR-A4 | "Copy nudge" button: pre-written polite Teams/Slack message about the ticket copied to clipboard | P2 |

### 4.5 Screen 3 — QA Handoff

Absorbs original reports 5, 6, 12.

| ID | Requirement | Pri |
|---|---|---|
| FR-Q1 | Handoff feed: every transition into Ready-for-QA in the window — when, **who moved it** (transition author), previous status, current status/assignee | P0 |
| FR-Q2 | Handoff checks per handoff: developer comment within configured window before/at transition; PR/build reference present. Result: **Pass / Needs info** (binary — no decimal scores on people) | P1 |
| FR-Q3 | Returned-from-QA list: every `qa_stage` → `active_dev`/`rework` back-transition — when, who returned it, from/to status, current developer, return-reason comment if present | P0 |
| FR-Q4 | Return-rate summary by developer (transition-author attribution) with raw counts | P1 |

### 4.6 Screen 4 — Flow Analytics

Absorbs original reports 7, 17, 3. Extends existing cycle-time and time-in-status features.

| ID | Requirement | Pri |
|---|---|---|
| FR-F1 | Cycle time (dev start → Ready for QA → done) — retained from current app; switch displays to median/p85 | P0 |
| FR-F2 | Per-ticket stage breakdown: stacked horizontal bar, one color per stage (dev, waiting-QA, QA, rework, paused) | P1 |
| FR-F3 | Team bottleneck view: median days per status/stage | P1 |
| FR-F4 | Time in Status report retained as-is (windowed/lifetime modes, CSV) | P0 |
| FR-F5 | Multiple-active-tickets rule: developers holding >1 `active_dev` ticket, with the ticket list. `qa_stage` excluded | P0 |
| FR-F6 | Focus view: per developer per day — distinct tickets touched, total activities, breakdown by type | P1 |

### 4.7 Screen 5 — Quality

Absorbs original report 18 (+ report 6 trends).

| ID | Requirement | Pri |
|---|---|---|
| FR-QL1 | Bug lens per developer: bug count, completed, returned-from-QA count, median resolution hours, return rate with raw counts | P1 |
| FR-QL2 | Reopen-loop detection: tickets with ≥2 rework cycles highlighted | P1 |
| FR-QL3 | Team-level return-rate trend over time | P1 |

### 4.8 Screen 6 — Sprint & Planning

Absorbs original reports 15, 10, 11 (trend views).

| ID | Requirement | Pri |
|---|---|---|
| FR-S1 | Sprint commitment vs completion (existing Sprint Health code): committed at sprint start, added mid-sprint (scope creep from sprint-field changelog), completed, carryover, completion %. Ticket counts; story points only if `estimates_used` | P2 (gated on boards) |
| FR-S2 | Release Readiness (existing, fixVersion-based) retained as the interim commitment view; shown prominently until sprints enabled | P0 |
| FR-S3 | Planning hygiene: active tickets missing start date / due date (gated); due-date slip table (original vs current, push count, slip days) | P2 |
| FR-S4 | Empty states teach setup: sprint screen without boards explains exactly what to configure in Jira | P0 |

### 4.9 Screen 7 — Ticket Investigator

Original report 16.

| ID | Requirement | Pri |
|---|---|---|
| FR-T1 | Issue-key input → full chronological timeline: transitions, assignee changes, comments, worklogs, field changes (due date, flags, sprint), with actor and from/to values | P0 |
| FR-T2 | Inactivity gaps ≥ configurable days rendered as labeled visual spacers ("14 days — no activity") | P1 |
| FR-T3 | Stage-duration ribbon summarizing the ticket's life across the top | P1 |
| FR-T4 | Deep link to the ticket in Jira; optional date-range limit | P0 |

### 4.10 Team Trends layer & Meeting Mode

Extends existing `/exec` dashboard. Responds to comment C11.

| ID | Requirement | Pri |
|---|---|---|
| FR-X1 | Team aggregates: % active tickets with EOD signal, median cycle time, QA return rate, blocked count + median days blocked, disposition compliance, attention-board size — each with week-over-week delta | P1 |
| FR-X2 | **Meeting Mode** toggle: hides all individual names; shows distributions ("3 tickets aged >7 days"); large type for screen sharing | P1 |
| FR-X3 | Historical snapshots (SQLite) enabling trends; nightly snapshot job | P1 |
| FR-X4 | Role-based landing: developer → My Day, lead → Attention Board, exec → Team Trends (simple config/URL-based; full auth out of scope for now) | P2 |

### 4.11 Cross-cutting UI

| ID | Requirement | Pri |
|---|---|---|
| FR-U1 | Global persistent filter bar (project, developer, date range) shared across screens; state survives navigation | P0 |
| FR-U2 | Navigation grouped by purpose: My Day / Attention / QA / Flow / Quality / Planning / Investigate / Trends / Settings | P0 |
| FR-U3 | Every issue key deep-links to Jira | P0 |
| FR-U4 | Freshness indicator ("data as of HH:MM") on every page; background refresh; never block page render on a live Jira pull | P0 |
| FR-U5 | "Explain this number" affordance: metric definitions on hover/click, sourced from this PRD's glossary | P1 |
| FR-U6 | Color + icon (never color alone); sortable sticky-header tables | P1 |
| FR-U7 | CSV/Excel export on every table view (extend existing export layer); JSON API for all new screens | P0 |
| FR-U8 | Scheduled morning digest to a Teams channel via incoming webhook (top attention items + team aggregates) | P2 |

---

## 5. Non-Functional Requirements

- **Read-only** Jira access; API token scoped read-only; credentials env-only, never in repo or config store.
- **Performance:** cached results (existing 5-min cache retained or improved); target <2s page render from cache; full sync may run in background.
- **Reliability:** retry/backoff on Jira API; graceful degradation per data source (e.g., comments fetch failing doesn't break status-based views).
- **Deployability:** existing Azure hosting, Docker, gunicorn unchanged; new config store must work in a container (mounted volume or SQLite file path env-configurable).
- **Testing:** every metric engine has unit tests against fixtures modeled on real LIFEDATAV2 changelogs (existing test pattern); route smoke tests for all screens.
- **Privacy/culture:** no ranking tables; Meeting Mode default for the Trends screen when projected; individual views framed as self-service and coaching tools.

---

## 6. Rollout Phases

| Phase | Contents | Gate |
|---|---|---|
| **1** | FR-D1–D3, FR-C1/C2/C4/C5, My Day (FR-M1/M2/M4), Attention Board (Silent+Aging), nav restructure + filter bar, deprecation of obsolete routes | None — ship immediately |
| **2** | QA Handoff screen, Ticket Investigator UI, activity feed view | Phase 1 ingestion |
| **3** | Flow upgrades (medians, stage bars, Focus), Quality screen, explain-this-number | None |
| **4** | Overdue/slip, Blocked, planning hygiene, disposition tracking | Jira process decisions (due dates, start dates, Flagged convention) |
| **5** | Sprint Health enablement, Team Trends + Meeting Mode + snapshots, Teams digest, role-based landing | Sprint boards + board IDs |

---

## 7. Success Metrics

- ≥80% of active tickets have an EOD signal within 3 weeks of My Day launch.
- Attention Board median "Silent" age drops week over week for the first month.
- QA handoffs marked "Needs info" decline ≥30% within 6 weeks of Handoff checks.
- Team meeting uses Meeting Mode screen (qualitative adoption check).
- Zero code deploys required for the next workflow status change.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Feels like surveillance → gaming/resentment | Team-level defaults, Meeting Mode, no ranking, binary checks not scores, self-service framing |
| Status mapping wrong → wrong metrics everywhere | Unmapped-status alerts; admin UI; audit view listing statuses seen in data vs config |
| Comments/worklogs volume slows sync | Incremental sync, per-source caching, background refresh |
| Process decisions stall (dates, flagged, sprints) | All dependent features config-gated with teaching empty states; nothing blocks Phases 1–3 |
| Small-sample rates misread | Raw counts always shown; medians; minimum-n footnotes |

---

## 9. Open Questions (answers needed from Braden/Kevin, non-blocking for Phase 1)

1. Confirm the status-bucket mapping in §3.1 against the live LIFEDATAV2 workflow (and V2BR/Support if in scope).
2. Which projects are in scope beyond LIFEDATAV2?
3. Aging threshold defaults per bucket (proposal: active_dev 5d, qa_stage 3d, rework 2d, paused 10d).
4. Due-date and start-date enforcement date (flips FR-C4 gates).
5. Blocked convention sign-off (Flagged field + reason comment).
6. Board IDs once sprint boards exist.
7. Teams channel + webhook URL for the digest (Phase 5).
