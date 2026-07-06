"""
digest.py
---------
Morning Teams digest (PRD v3 FR-U8): posts an Adaptive Card with the top 5
attention items and 4 team aggregates to the incoming-webhook URL configured
in Settings. Triggered by the /tasks/snapshot?digest=1 endpoint — no scheduler
runs inside the app (container-friendly).
"""

from __future__ import annotations

import requests

import settings as st


def build_card(board_rows, agg) -> dict:
    facts = [
        {"title": "EOD signal", "value": f"{agg.get('eod_signal_pct', '—')}% of active tickets"},
        {"title": "Median cycle", "value": f"{agg.get('cycle_median_h') or '—'}h (n={agg.get('cycle_n', 0)})"},
        {"title": "QA return rate", "value": f"{agg.get('return_rate_pct', '—')}% "
                                             f"({agg.get('returns', 0)} of {agg.get('handoffs', 0)})"},
        {"title": "Attention board", "value": f"{agg.get('attention_size', 0)} ticket(s)"},
    ]
    items = [{"type": "TextBlock", "wrap": True,
              "text": f"**{r['issue'].key}** {r['issue'].summary[:60]} — "
                      f"{', '.join(x['tag'] for x in r['reasons'])} ({r['issue'].assignee})"}
             for r in board_rows[:5]] or [{"type": "TextBlock", "text": "Nothing needs attention 🎉"}]
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4", "body": [
                        {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                         "text": "Engineering morning digest"},
                        {"type": "FactSet", "facts": facts},
                        {"type": "TextBlock", "weight": "Bolder", "text": "Top attention items"},
                    ] + items}}]}


def send(board_rows, agg) -> bool:
    url = st.load().get("teams_webhook_url", "").strip()
    if not url:
        return False
    resp = requests.post(url, json=build_card(board_rows, agg), timeout=30)
    return resp.ok
