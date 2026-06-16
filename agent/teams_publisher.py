"""
teams_publisher.py
------------------
Sends the card-style briefing to Microsoft Teams via incoming webhook.

Each narrative section becomes its own colored section in the MessageCard,
with emoji "icons" that mirror the PepsiCo-style sample card.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)


def _build_teams_actions(card_url: Optional[str] = None) -> list:
    """Build the potentialAction list for the Teams MessageCard."""
    actions = [
        {
            "@type": "OpenUri",
            "name": "Open Tableau Dashboard",
            "targets": [{
                "os": "default",
                "uri": "https://public.tableau.com/app/profile/mona7677/viz/Superstore_17790771422410/Overview",
            }],
        }
    ]
    if card_url:
        actions.insert(0, {
            "@type": "OpenUri",
            "name": "🔗 Open Interactive Card",
            "targets": [{"os": "default", "uri": card_url}],
        })
    return actions


def post_to_teams(narrative, config: dict,
                  webhook_url: Optional[str] = None,
                  card_url: Optional[str] = None) -> dict:
    """
    Post the briefing to Teams.
    card_url: optional GitHub Pages URL for the interactive HTML card; shown
              as an 'Open Interactive Card' button in the potentialAction bar.
    """
    cfg = config.get("delivery", {}).get("teams", {})
    title_prefix = cfg.get("title_prefix", "Daily Performance Briefing")

    webhook = webhook_url or os.getenv(cfg.get("webhook_env_var", "TEAMS_WEBHOOK_URL"))
    if not webhook:
        return {"status": "skipped",
                "reason": "TEAMS_WEBHOOK_URL not set — narrative saved to file only."}

    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": narrative.headline,
        "themeColor": "0F6CBD",
        "title": f"⚡ {title_prefix}",
        "text": f"**{narrative.headline}**",
        "sections": [
            {
                "activityTitle": "📄 **Executive Summary**",
                "text": narrative.exec_summary,
            },
            {
                "activityTitle": "📊 **Key Performance Indicators**",
                "text": narrative.kpi_table_md,
            },
            {
                "activityTitle": "🔎 **What Drove the Move**",
                "text": narrative.drivers_md,
            },
            {
                "activityTitle": "⚠️ **Anomaly & Watch-out**",
                "text": narrative.anomaly,
            },
            {
                "activityTitle": "➡️ **Recommended Action**",
                "text": narrative.recommended_action,
            },
            {
                "activityTitle": "🎙️ **Speaker Notes**",
                "text": narrative.speaker_notes,
            },
        ],
        "potentialAction": _build_teams_actions(card_url),
    }

    log.info("Posting briefing to Teams (%d sections)", len(payload["sections"]))
    resp = requests.post(webhook, json=payload, timeout=30)
    if resp.status_code >= 400:
        log.error("Teams webhook failed: %s %s", resp.status_code, resp.text)
        return {"status": "error", "code": resp.status_code, "body": resp.text}
    return {"status": "ok", "code": resp.status_code}
