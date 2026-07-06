# Jira-side process setup for gated features

The reporting app is **read-only**. Enforcement lives in Jira (workflow
validators / Automation); the app measures compliance. Each gated feature
below lists its Jira prerequisite; flip the matching gate in `/settings` once
the process is adopted — no deploy needed.

## Due dates (`due_dates_required` gate → Overdue reason, slip table)
- Require a due date when a ticket enters an active status: company-managed
  projects → workflow validator on the transition; team-managed → Automation
  rule ("when status changes to In Progress and due date is empty → comment
  and reassign/notify").
- The app derives Original Due Date from the first changelog value, counts
  pushes (changes to a later date), and computes slip days automatically.

## Start dates (`start_dates_required` gate → Missing dates, Reschedule Count)
- Adopt a "planned start" field (team-managed projects have a built-in
  Start date field; the app auto-detects it, override in /settings).
- Rule: every active ticket has a start date; not-yet-started tickets must
  have start date ≥ today. Moving the date is allowed — every move is counted
  (Reschedule Count, Total Days Pushed) as a prioritization signal.

## Blocked convention (Attention `Blocked` reason)
- Standardize on Jira's built-in **Flagged** field ("Flag as impediment") plus
  a required reason comment. Flag changes are changelog-tracked, so Days
  Blocked is exact. Labels (`blocked`, `waiting`, `dependency`) remain
  low-confidence hints and are shown as such.
- Optional Automation: after N days in status → add flag + comment + notify.

## Reopen requires a comment (QA return reasons)
- Add a workflow validator or Automation rule: transitions into Reopen (or any
  QA→dev back-transition) require a comment. The QA screen then shows the
  return reason on every returned ticket.

## Sprints (`sprints_enabled` gate + board IDs → Sprint Health)
- Create real scrum boards, start/end sprints on schedule, keep the sprint
  field maintained. Enter board IDs in /settings and enable the gate.

## Worklogs (`worklogs_required` gate → worklog checklist item)
- Only adopt if the team agrees to log time. Stage durations from the
  changelog answer "where did the time go" without manual logging.
