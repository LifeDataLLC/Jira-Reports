> **DEPRECATED** — superseded by [IMPLEMENTATION_INSTRUCTIONS_v3.md](../IMPLEMENTATION_INSTRUCTIONS_v3.md). Kept for historical reference.

# Implementation Plan — Developer Activity Report (Local Hosting)

Companion to `PRD.md`. Sequences the work into phases, all **locally hosted** (runs on
your Mac/an internal machine, no cloud). Each phase is independently shippable and
leaves the app fully working. Requirement IDs reference the PRD.

**Current state (v0, done):** Flask app with team overview, per-developer pages
(In progress → Completed → Currently assigned), changelog-based cycle time, status
aging, throughput, current workload, Excel + JSON export, env-based config, tested
metric logic.

---

## Guiding principles

- Keep the three layers separate: **transport** (`jira_client` REST calls),
  **metrics** (pure functions on changelog), **presentation** (Flask + templates).
  New work goes in the right layer so each stays testable.
- Every phase ends green: tests pass, app runs, no half-finished UI.
- Config over code: new projects, windows, thresholds come from env/config.
- Local-only constraints honored: no external services required to run.

---

## Phase 1 — Hardening & operability (P0 gaps)

*Goal: make the existing app dependable and friction-free to run. ~1–2 days.*

| # | Task | Req | Notes |
|---|---|---|---|
| 1.1 | Configurable port via `PORT` env (default 5050 to dodge AirPlay :5000) | FR-OP-2 | small change in `app.py` `__main__` + README |
| 1.2 | `.env` auto-loading (python-dotenv) + `run.sh`/Makefile one-command start | FR-OP-3 | `pip add python-dotenv` |
| 1.3 | Robust API error handling: clear messages for 401/403, retry+backoff on 429/5xx | FR-IN-6, NFR-4 | wrap requests in a helper |
| 1.4 | `/health` endpoint: last refresh time + Jira reachability | FR-OP-4 | also surfaces stale cache |
| 1.5 | Structured logging (no secrets) | FR-OP-4, NFR-3 | stdlib `logging` |
| 1.6 | "Refresh now" button + configurable cache TTL | FR-OP-5 | clears `_CACHE` |
| 1.7 | Multi-project config already supported — verify with `JIRA_PROJECTS=LIFEDATAV2,V2BR,SUPPORT` and add a project column/filter | FR-IN-4 | |

**Exit criteria:** one-command launch, friendly errors, `/health` green, runs across
multiple projects.

---

## Phase 2 — Reporting depth (P1 metrics & filtering)

*Goal: the metrics managers actually ask for. ~3–5 days.*

| # | Task | Req | Notes |
|---|---|---|---|
| 2.1 | Configurable reporting window + custom date range (UI control + JQL) | FR-IN-5 | "7/14/30/this sprint/custom" |
| 2.2 | Team rollups with p50/p85 percentiles for cycle time & aging | FR-ME-5 | add to overview cards |
| 2.3 | Configurable cycle-time policy (first/last In Progress; optionally subtract paused/blocked statuses) | FR-ME-4 | env-driven; extend `first_active_entry` + an "active time" calculator |
| 2.4 | Work-type breakdown (Bug/Task/Story) per developer & team | FR-ME-7 | group by issue_type |
| 2.5 | Filtering/sorting in UI: project, type, status, window; sortable columns | FR-UI-3 | client-side JS, no framework |
| 2.6 | Team-wide "Stuck work" view (all aging > threshold) | FR-UI-5 | configurable threshold |
| 2.7 | Optional business-days calculation for cycle/aging | FR-ME-8 | toggle |

**Exit criteria:** managers can slice by window/project/type, see percentiles, and
open a single "what's stuck" list.

---

## Phase 3 — Visualization & exports (P1 presentation/delivery)

*Goal: make it skimmable and shareable as files. ~2–4 days.*

| # | Task | Req | Notes |
|---|---|---|---|
| 3.1 | Charts: throughput bar, cycle-time distribution, aging histogram | FR-UI-4 | lightweight (e.g. Chart.js from CDN or inline SVG) |
| 3.2 | Static self-contained HTML snapshot export (single file) | FR-EX-3 | render templates to a file with data inlined |
| 3.3 | Per-table CSV export | FR-EX-4 | |
| 3.4 | Local scheduled generation: CLI `generate_report.py` + cron example that writes HTML/Excel to a local folder each morning | FR-EX-5 | delivery to Teams/SharePoint/email explicitly deferred |
| 3.5 | Print-friendly / optional dark layout | FR-UI-7 | |

**Exit criteria:** a daily local file drop and on-screen charts; static export opens
standalone (sets up future SharePoint/email delivery without committing to it now).

---

## Phase 4 — History & trends (P2, requires persistence)

*Goal: see direction over time, not just a snapshot. ~3–5 days.*

| # | Task | Req | Notes |
|---|---|---|---|
| 4.1 | SQLite persistence layer; store daily snapshots of tickets + metrics | FR-IN-7 | local file DB |
| 4.2 | Trend charts: weekly/sprint throughput & median cycle time | FR-ME-6 | reads snapshots |
| 4.3 | accountId → name/squad mapping file; group by squad; tolerate renames | FR-OP-6 | |
| 4.4 | Backfill job to seed history from current changelogs | — | one-time |

**Exit criteria:** week-over-week trends render from locally stored history.

---

## Phase 5 — Quality & polish (cross-cutting, ongoing)

| # | Task | Req | Notes |
|---|---|---|---|
| 5.1 | Expand automated tests: changelog fixtures (re-open, pause, rename), date-window edges, pagination | NFR-1 | extend existing test suite |
| 5.2 | Reconciliation script: sample N tickets, compare computed vs Jira | NFR-1 | audit tool |
| 5.3 | README/runbook updates per phase; metric tooltips in UI | NFR-5, FR-ME glossary | |
| 5.4 | Performance pass: ensure bulk queries where possible, cache tuning | NFR-2 | |

---

## Sequencing & dependencies

```
Phase 1 (hardening) ──► Phase 2 (depth) ──► Phase 3 (viz/exports)
                                   └──► Phase 4 (history)  [needs SQLite from 4.1]
Phase 5 runs alongside every phase.
```

Recommended order: **1 → 2 → 3**, then 4 if trend history is wanted. Phase 5 tasks are
folded into each phase as you go (don't batch them to the end).

---

## Deferred (explicitly out of this local-hosting plan)

Tracked so they aren't lost, but **not** scheduled here:

- Shared hosting on Azure App Service.
- Microsoft Teams tab embedding (manifest + iframe/CSP + Entra ID SSO).
- SharePoint document-library delivery of the static export via Microsoft Graph.
- Email/Teams webhook daily delivery.
- Authentication/authorization for multi-user network access.

When you're ready to move off localhost, Phase 3's static export (3.2) and scheduled
generation (3.4) are the natural hand-off points into any of these delivery channels.

---

## Effort summary (rough)

| Phase | Theme | Est. |
|---|---|---|
| 1 | Hardening & operability | 1–2 days |
| 2 | Reporting depth | 3–5 days |
| 3 | Visualization & exports | 2–4 days |
| 4 | History & trends | 3–5 days |
| 5 | Quality & polish | ongoing |

Phases 1–3 (a genuinely solid, shareable local tool) land in roughly **1.5–2 weeks**
of focused work; Phase 4 adds trends on top.
