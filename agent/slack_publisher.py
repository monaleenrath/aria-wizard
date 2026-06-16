"""
slack_publisher.py
------------------
Posts the Dark Editorial briefing to Slack via an Incoming Webhook.

Design: Three-Act structure inspired by Concept 4 — Dark Editorial.
    ACT I   · WHERE WE ARE      — headline KPI + full metrics table
    ACT II  · WHAT MOVED IT     — top drivers, lifts and drags
    ACT III · THE CALL          — recommended action + speaker notes

Slack mrkdwn quick-reference (different from standard markdown!):
    *bold*          NOT **bold**
    _italic_        NOT *italic*
    `code`          same
    <url|text>      links
    > quote         block quote
    • bullet        no native list — use bullet character
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Optional

import requests

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _md_to_mrkdwn(text: str) -> str:
    """Convert standard markdown to Slack's mrkdwn dialect."""
    if not text:
        return ""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    return text


def _kpi_table_to_mrkdwn(md_table: str) -> str:
    """Convert a markdown KPI table to monospaced Slack code block."""
    lines = [ln for ln in md_table.splitlines()
             if ln.strip() and not ln.strip().startswith("|---")]
    if not lines:
        return md_table

    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)

    n_cols = max(len(r) for r in rows)
    widths = [0] * n_cols
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))

    out_lines = []
    for idx, r in enumerate(rows):
        padded = [c.ljust(widths[i]) for i, c in enumerate(r)]
        out_lines.append("  ".join(padded))
        if idx == 0:
            out_lines.append("─" * (sum(widths) + (n_cols - 1) * 2))
    return "```\n" + "\n".join(out_lines) + "\n```"


def _format_date_masthead(ref_date: str) -> str:
    """Format date as 'MON  MAY 20  2026' for the masthead."""
    try:
        d = date.fromisoformat(ref_date)
        return d.strftime("%a  %b %d  %Y").upper()
    except Exception:
        return ref_date


# --------------------------------------------------------------------------- #
# Dark Editorial Block Kit builder
# --------------------------------------------------------------------------- #

def build_slack_payload(narrative, config: dict) -> dict:
    """
    Build the Dark Editorial three-act Block Kit payload.
    ACT I · WHERE WE ARE  |  ACT II · WHAT MOVED IT  |  ACT III · THE CALL
    """
    ref_date = getattr(narrative, "reference_date", "")
    masthead_date = _format_date_masthead(ref_date) if ref_date else ""

    blocks = [
        # ── MASTHEAD ──────────────────────────────────────────────────────── #
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"```THE DAILY DESK  ·  BOARD-ROOM NARRATOR  ·  {masthead_date}```"
                )
            }
        },

        # ── HEADLINE ──────────────────────────────────────────────────────── #
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": narrative.headline,
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_{_md_to_mrkdwn(narrative.exec_summary)}_"
            }
        },
        {"type": "divider"},

        # ── ACT I · WHERE WE ARE ──────────────────────────────────────────── #
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*▸  ACT I  ·  WHERE WE ARE*\n"
                    f"{_kpi_table_to_mrkdwn(narrative.kpi_table_md)}"
                )
            }
        },
        {"type": "divider"},

        # ── ACT II · WHAT MOVED IT ────────────────────────────────────────── #
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*▸  ACT II  ·  WHAT MOVED IT*\n"
                    f"{_md_to_mrkdwn(narrative.drivers_md)}"
                )
            }
        },
    ]

    # Anomaly block — only shown when there is something to flag
    anomaly_text = _md_to_mrkdwn(narrative.anomaly or "")
    if anomaly_text and "no anomal" not in anomaly_text.lower():
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⚠️  Watch-out*\n{anomaly_text}"
            }
        })

    blocks += [
        {"type": "divider"},

        # ── ACT III · THE CALL ────────────────────────────────────────────── #
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*▸  ACT III  ·  THE CALL*\n"
                    f"➡️  {_md_to_mrkdwn(narrative.recommended_action)}"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"🎙️  _Speaker notes: {_md_to_mrkdwn(narrative.speaker_notes)}_"
                )
            }
        },
        {"type": "divider"},

        # ── FOOTER ────────────────────────────────────────────────────────── #
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_Board Room Narrator Agent_"
                }
            ]
        }
    ]

    return {
        "text": narrative.headline,   # fallback text for mobile notifications
        "blocks": blocks,
    }


def post_to_slack(narrative, config: dict,
                  webhook_url: Optional[str] = None) -> dict:
    """Send the briefing as a Block Kit message to Slack."""
    cfg = config.get("delivery", {}).get("slack", {})
    webhook = webhook_url or os.getenv(cfg.get("webhook_env_var", "SLACK_WEBHOOK_URL"))
    if not webhook:
        return {"status": "skipped",
                "reason": "SLACK_WEBHOOK_URL not set — narrative saved to file only."}

    payload = build_slack_payload(narrative, config)
    log.info("Posting briefing to Slack (%d blocks)", len(payload["blocks"]))
    resp = requests.post(webhook, json=payload, timeout=30)
    if resp.status_code >= 400:
        log.error("Slack webhook failed: %s %s", resp.status_code, resp.text)
        return {"status": "error", "code": resp.status_code, "body": resp.text}
    return {"status": "ok", "code": resp.status_code}


def render_slack_preview(narrative, config: dict) -> str:
    """Pretty-print what the Slack channel will see. Used by --dry-run."""
    payload = build_slack_payload(narrative, config)
    blocks = payload["blocks"]

    lines = []
    bar = "─" * 72
    lines.append(bar)
    lines.append("  SLACK PREVIEW — this is what #daily-briefing will receive")
    lines.append(bar)
    for b in blocks:
        if b["type"] == "header":
            lines.append("")
            lines.append(f"  {b['text']['text']}")
            lines.append("  " + "═" * (len(b['text']['text']) + 0))
        elif b["type"] == "divider":
            lines.append("  " + "·" * 60)
        elif b["type"] == "section":
            text = b["text"]["text"]
            for ln in text.splitlines():
                lines.append("  " + ln)
        elif b["type"] == "context":
            for el in b.get("elements", []):
                lines.append("  " + el["text"])
        lines.append("")
    lines.append(bar)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Image posting (Dark Editorial PNG card)
# --------------------------------------------------------------------------- #

def _resolve_channel_id(token: str, channel: str) -> Optional[str]:
    """
    Resolve a channel name (e.g. 'aria-ceo') to its Slack channel ID
    (e.g. 'C0B40FQ1K4K') using conversations.list pagination.
    If the value already looks like a channel ID, return it as-is.
    """
    # Channel IDs start with C (public), G (private group), or D (DM)
    if channel and channel[0] in ("C", "G", "D"):
        return channel

    name = channel.lstrip("#")   # strip leading # if present
    log.info("Resolving channel name '%s' to ID via conversations.list", name)

    cursor = None
    while True:
        params: dict = {"limit": 200, "types": "public_channel,private_channel"}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            log.error("conversations.list failed: %s", data.get("error"))
            return None

        for ch_obj in data.get("channels", []):
            if ch_obj.get("name") == name:
                log.info("Resolved '%s' → %s", name, ch_obj["id"])
                return ch_obj["id"]

        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    log.error("Channel '%s' not found in workspace — has it been created?", name)
    return None


def post_image_to_slack(png_bytes: bytes, narrative, config: dict,
                        bot_token: Optional[str] = None,
                        channel: Optional[str] = None,
                        initial_comment: Optional[str] = None,
                        card_url: Optional[str] = None) -> dict:
    """
    Upload a PNG card directly to a Slack channel.
    Requires SLACK_BOT_TOKEN (xoxb-...) with files:write + chat:write scopes.
    channel can be a channel name ('aria-ceo') or a raw ID ('C0B40FQ1K4K').
    initial_comment overrides the default "Daily Briefing — {date}" comment.
    card_url: optional GitHub Pages URL for the interactive HTML card; appended
              as a clickable link below the comment when provided.
    """
    cfg   = config.get("delivery", {}).get("slack", {})
    token = bot_token or os.getenv(cfg.get("bot_token_env_var", "SLACK_BOT_TOKEN"))
    ch    = channel   or os.getenv(cfg.get("channel_env_var",   "SLACK_CHANNEL_ID"), "#daily-briefing")
    ref   = getattr(narrative, "reference_date", "") or ""
    fname = f"briefing_{ref}.png"

    # Build the comment; append interactive card link when available
    if initial_comment is None:
        initial_comment = f"*Daily Briefing — {ref}*"
    if card_url:
        initial_comment = f"{initial_comment}\n<{card_url}|🔗 Open Interactive Card>"

    if not token:
        log.warning("SLACK_BOT_TOKEN not set — Slack delivery skipped.")
        return {"status": "skipped", "reason": "SLACK_BOT_TOKEN not set"}

    # Resolve channel name → ID (conversations.join only accepts IDs)
    resolved = _resolve_channel_id(token, ch)
    if not resolved:
        return {"status": "error", "error": f"channel '{ch}' not found — create it first"}
    ch = resolved

    # Join the channel so the bot can post
    join_resp   = requests.post(
        "https://slack.com/api/conversations.join",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": ch},
        timeout=30,
    )
    join_result = join_resp.json()
    if join_result.get("ok"):
        log.info("Bot joined channel %s", ch)
    else:
        log.warning("conversations.join: %s — will attempt upload anyway",
                    join_result.get("error"))

    # Step 1 — get an external upload URL (new Slack API, replaces files.upload)
    log.info("Requesting upload URL for PNG briefing card (%d bytes)", len(png_bytes))
    url_resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {token}"},
        data={"filename": fname, "length": len(png_bytes)},
        timeout=30,
    )
    url_resp.raise_for_status()
    url_result = url_resp.json()
    if not url_result.get("ok"):
        log.error("files.getUploadURLExternal failed: %s", url_result.get("error"))
        return {"status": "error", "error": url_result.get("error")}

    upload_url = url_result["upload_url"]
    file_id    = url_result["file_id"]

    # Step 2 — upload the raw PNG bytes to the provided URL
    upload_resp = requests.post(
        upload_url,
        data=png_bytes,
        headers={"Content-Type": "image/png"},
        timeout=60,
    )
    upload_resp.raise_for_status()
    log.info("PNG uploaded to Slack (file_id=%s)", file_id)

    # Step 3 — complete the upload and share to the channel
    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "files": [{"id": file_id, "title": narrative.headline}],
            "channel_id": ch,
            "initial_comment": initial_comment,
        },
        timeout=30,
    )
    complete_resp.raise_for_status()
    result = complete_resp.json()
    if not result.get("ok"):
        log.error("files.completeUploadExternal failed: %s", result.get("error"))
        return {"status": "error", "error": result.get("error")}
    log.info("Slack image posted successfully.")
    return {"status": "ok"}
