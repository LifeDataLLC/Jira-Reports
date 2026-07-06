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
