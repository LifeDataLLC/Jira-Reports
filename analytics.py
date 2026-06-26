"""
analytics.py
------------
Pure changelog math. Given an issue's status history, reconstruct the timeline and
derive every duration/transition metric the reports need. No network, no Flask —
which makes all of this straightforward to unit-test against real changelog fixtures.

Core idea: a Jira changelog gives us status *transitions* with timestamps. From the
issue's creation time, the transitions, and (resolved time or now), we can rebuild the
exact sequence of (status, entered, exited) segments and measure time spent anywhere.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import config as cfg


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def parse_ts(ts):
    if isinstance(ts, dt.datetime):
        return ts
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def days(a, b):
    if not (a and b):
        return None
    return round((b - a).total_seconds() / 86400, 2)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Transition extraction
# ---------------------------------------------------------------------------

def status_changes(changelog):
    """[(ts, from_status, to_status), ...] oldest-first for status fields only."""
    out = []
    for h in changelog or []:
        ts = parse_ts(h.get("created"))
        if not ts:
            continue
        for item in h.get("items", []):
            if item.get("field") == "status":
                out.append((ts, item.get("fromString") or "", item.get("toString") or ""))
    out.sort(key=lambda x: x[0])
    return out


def status_events(changelog):
    """[(ts, author, from_status, to_status), ...] — like status_changes but keeps the
    person who performed each transition, so reports can attribute work to whoever
    actually moved the ticket (more accurate than assignee for 'who did the work')."""
    out = []
    for h in changelog or []:
        ts = parse_ts(h.get("created"))
        if not ts:
            continue
        author = (h.get("author") or {}).get("displayName", "Unknown")
        for item in h.get("items", []):
            if item.get("field") == "status":
                out.append((ts, author, item.get("fromString") or "",
                            item.get("toString") or ""))
    out.sort(key=lambda x: x[0])
    return out


@dataclass
class Timeline:
    created: dt.datetime | None
    resolved: dt.datetime | None
    current_status: str
    # raw outputs
    segments: list = field(default_factory=list)            # (status, enter, exit)
    seconds_in_status: dict = field(default_factory=dict)   # status -> seconds
    seconds_in_stage: dict = field(default_factory=dict)    # stage  -> seconds
    stage_first_entry: dict = field(default_factory=dict)   # stage  -> ts
    transitions: list = field(default_factory=list)         # (ts, from_stage, to_stage)
    reopened_count: int = 0
    qa_rejections: int = 0

    # ---- convenience metrics ----
    def days_in_stage(self, stage):
        s = self.seconds_in_stage.get(stage)
        return round(s / 86400, 2) if s else None

    def days_in_status(self, status):
        s = self.seconds_in_status.get(status)
        return round(s / 86400, 2) if s else None

    def seconds_in_status_window(self, start, end):
        """Time spent in each status that falls INSIDE [start, end) — i.e. only the
        overlap of each status segment with the window. Used for 'time accrued in the
        last 24h / this week' rather than lifetime totals."""
        out = {}
        for status, enter, exit_ in self.segments:
            a = max(enter, start)
            b = min(exit_, end)
            if b > a:
                out[status] = out.get(status, 0) + (b - a).total_seconds()
        return out

    def seconds_in_stage_window(self, start, end):
        """Time accrued in each STAGE inside [start, end) — the windowed analog of
        seconds_in_stage. Lets Status Duration report 'time spent per stage during the
        last 24h / week / month' instead of lifetime totals."""
        out = {}
        for status, enter, exit_ in self.segments:
            a = max(enter, start)
            b = min(exit_, end)
            if b > a:
                stage = cfg.stage_of(status)
                out[stage] = out.get(stage, 0) + (b - a).total_seconds()
        return out

    @property
    def first_active(self):
        entries = [self.stage_first_entry.get(s) for s in cfg.ACTIVE_STAGES]
        entries = [e for e in entries if e]
        return min(entries) if entries else None

    @property
    def cycle_days(self):
        """First active stage entry -> resolved (optionally minus blocked time)."""
        start, end = self.first_active, self.resolved
        if not (start and end):
            return None
        total = (end - start).total_seconds()
        if cfg.EXCLUDE_BLOCKED_FROM_ACTIVE:
            for st in cfg.BLOCKED_STAGES:
                total -= self.seconds_in_stage.get(st, 0)
        return round(max(total, 0) / 86400, 2)

    @property
    def dev_duration_days(self):
        """First Development entry -> first Ready-for-QA entry."""
        return days(self.stage_first_entry.get(cfg.STAGE_DEVELOPMENT),
                    self.stage_first_entry.get(cfg.STAGE_READY_FOR_QA))

    @property
    def qa_duration_days(self):
        """First Ready-for-QA entry -> Done (resolved)."""
        return days(self.stage_first_entry.get(cfg.STAGE_READY_FOR_QA), self.resolved)


def analyze(changelog, created, resolved, current_status, current_category=None,
            now=None):
    """Build a Timeline from a changelog."""
    created = parse_ts(created)
    resolved = parse_ts(resolved)
    now = now or now_utc()
    end_cap = resolved or now

    changes = status_changes(changelog)
    tl = Timeline(created=created, resolved=resolved, current_status=current_status)

    # Reconstruct (status, enter, exit) segments.
    segments = []
    if changes:
        initial_status = changes[0][1] or current_status
        seg_start = created or changes[0][0]
        cur = initial_status
        for ts, frm, to in changes:
            if ts and seg_start and ts >= seg_start:
                segments.append((cur, seg_start, ts))
            cur, seg_start = to, ts
        if seg_start and end_cap and end_cap >= seg_start:
            segments.append((cur, seg_start, end_cap))
    elif created:
        segments.append((current_status, created, end_cap))

    tl.segments = segments

    # Durations per status and per stage; first stage entry.
    for status, enter, exit_ in segments:
        secs = max((exit_ - enter).total_seconds(), 0)
        tl.seconds_in_status[status] = tl.seconds_in_status.get(status, 0) + secs
        stage = cfg.stage_of(status, current_category if status == current_status else None)
        tl.seconds_in_stage[stage] = tl.seconds_in_stage.get(stage, 0) + secs
        if stage not in tl.stage_first_entry or enter < tl.stage_first_entry[stage]:
            tl.stage_first_entry[stage] = enter

    # Stage-level transitions + reopen / QA-rejection detection.
    back_targets = {cfg.STAGE_DEVELOPMENT, cfg.STAGE_IN_PROGRESS}
    from_after_qa = {cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING,
                     cfg.STAGE_STAGING, cfg.STAGE_DONE}
    for ts, frm, to in changes:
        fs, tstage = cfg.stage_of(frm), cfg.stage_of(to)
        tl.transitions.append((ts, fs, tstage))
        if tstage == cfg.STAGE_REOPENED or (fs in from_after_qa and tstage in back_targets):
            tl.reopened_count += 1
        if fs in {cfg.STAGE_READY_FOR_QA, cfg.STAGE_QA_TESTING} and tstage in back_targets:
            tl.qa_rejections += 1

    return tl


def entered_stage_in_window(tl: Timeline, stage, start, end):
    """True if the issue entered `stage` within [start, end) — for daily movement."""
    for ts, fs, tstage in tl.transitions:
        if tstage == stage and start <= ts < end:
            return True
    return False


def transitions_into(tl: Timeline, stage, start, end):
    return sum(1 for ts, fs, tstage in tl.transitions
               if tstage == stage and start <= ts < end)
