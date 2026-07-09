# Dev Team Rules → where the app checks each

Maps "Jira Ticket Rules for Developers & QA" to the screens that enforce them.
All rule config lives in `/settings` (Load LIFEDATAV2 workflow sets it up).

| Rule | Where it shows | How it's checked |
|------|----------------|------------------|
| 1. One ticket active at a time | **Flow** → Multiple active tickets | >1 ticket in the same active lane (dev / QA / staging / production), each enforced independently |
| 2. Status matches actual work | **Attention** → Silent / Aging | No activity while active (Silent), or over the status threshold (Aging) |
| 3. Pause your active ticket at EOD | **My Day** checklist + **Attention** → Not paused | A ticket left in an active status overnight (entered on a prior day) is flagged; My Day names the exact pause status to move to |
| 4. Log your time accurately | **My Day** checklist → Worklog today | Requires a worklog entry that day (gate `worklogs_required`, on) — verifies presence, not truthfulness |
| 5. Every ticket belongs to a release | **My Day** checklist + **Attention** → No release | Ticket has no fixVersion assigned |
| 6. Set a due date on every ticket | **My Day** checklist + **Attention** → Missing dates; **Planning** → slip table | Requires a due date (gate `due_dates_required`, on); tracks original vs current + push count + slip days |
| 7. Status reference (active/paused/next) | **Settings** | The 5 active statuses, their lanes, and their pause counterparts are configured here (Load LIFEDATAV2 workflow) |

The "evaluated every Monday" review = the **Attention Board** (lead view, severity-sorted)
and **My Day roll-up** (% of active tickets with an EOD signal). Individual coaching data
stays on each developer's My Day; team meetings use **Trends → Meeting Mode** (no names).
