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
