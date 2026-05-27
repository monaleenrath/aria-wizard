"""
streamlit_app.py  —  ARIA Onboarding Wizard
============================================
8-step setup wizard for Board Room Narrator Agent.

    streamlit run streamlit_app.py

Features
--------
• Dark / Light theme toggle (top-right) — persists to ui_config.yaml
• Inline ℹ️ help tips on every technical field
• Floating ARIA help sidebar — FAQ + step-by-step guides
• Step 8 fully automated: GitHub account → repo → PAT → secrets → live

Step 7 renders the *actual* SVG card from svg_generator.py — pixel-identical
to what gets posted to Slack every morning.
"""

from __future__ import annotations

import base64
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import requests as _req
import streamlit as st
import yaml

# ── page config must be first streamlit call ────────────────────────────────
_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "agent"))

_UI_CONFIG_PATH       = _ROOT / "ui_config.yaml"
_STREAMLIT_CONFIG_DIR = _ROOT / ".streamlit"
_STREAMLIT_TOML       = _STREAMLIT_CONFIG_DIR / "config.toml"

# ── read stored theme BEFORE set_page_config ────────────────────────────────
def _load_ui_config() -> dict:
    try:
        return yaml.safe_load(_UI_CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}

def _save_ui_config(cfg: dict):
    try:
        _UI_CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False))
    except OSError:
        pass  # read-only FS on Streamlit Cloud

def _apply_theme(dark: bool):
    """Write .streamlit/config.toml — Streamlit auto-detects and reloads.
    Silently skips on read-only filesystems (e.g. Streamlit Community Cloud)."""
    try:
        _STREAMLIT_CONFIG_DIR.mkdir(exist_ok=True)
        if dark:
            toml = (
                "[theme]\n"
                'base = "dark"\n'
                'primaryColor = "#F59E0B"\n'
                'backgroundColor = "#0B1220"\n'
                'secondaryBackgroundColor = "#111827"\n'
                'textColor = "#F8FAFC"\n'
            )
        else:
            toml = (
                "[theme]\n"
                'base = "light"\n'
                'primaryColor = "#F59E0B"\n'
            )
        _STREAMLIT_TOML.write_text(toml)
    except OSError:
        pass  # read-only FS on Streamlit Cloud — theme toggle is a no-op

_ui_cfg  = _load_ui_config()
_is_dark = _ui_cfg.get("dark_mode", True)
# Ensure config.toml matches stored preference on first run
if not _STREAMLIT_TOML.exists():
    _apply_theme(_is_dark)

st.set_page_config(
    page_title="ARIA Setup Wizard",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

TOTAL_STEPS = 8
STEP_LABELS = [
    "Welcome", "Upload Data", "Discover KPIs", "Pick Role",
    "Choose AI", "Set Delivery", "Preview Card", "Go Live",
]

# ════════════════════════════════════════════════════════════════════════════════
# CARD ACCENT THEMES
# ════════════════════════════════════════════════════════════════════════════════

CARD_STYLES = {
    "role_accent": {
        "label":    "🎨 Role Accent",
        "tagline":  "Colour pulled from your selected role.",
        "bg":       "#0B1220",
        "surface":  "#111827",
        "accent_override": None,
        "swatch":   "linear-gradient(135deg, #0B1220 50%, #F59E0B 50%)",
    },
    "crimson": {
        "label":    "🔴 Crimson",
        "tagline":  "Bold red — urgency and decisive action.",
        "bg":       "#0B1220",
        "surface":  "#111827",
        "accent_override": "#EF4444",
        "swatch":   "linear-gradient(135deg, #0B1220 50%, #EF4444 50%)",
    },
    "electric_blue": {
        "label":    "🔵 Electric Blue",
        "tagline":  "Cool blue — analytical, data-forward.",
        "bg":       "#0A0E1A",
        "surface":  "#111827",
        "accent_override": "#60A5FA",
        "swatch":   "linear-gradient(135deg, #0A0E1A 50%, #60A5FA 50%)",
    },
    "emerald": {
        "label":    "🟢 Emerald",
        "tagline":  "Green — growth story, positive momentum.",
        "bg":       "#0B1220",
        "surface":  "#111827",
        "accent_override": "#34D399",
        "swatch":   "linear-gradient(135deg, #0B1220 50%, #34D399 50%)",
    },
    "rose": {
        "label":    "🌸 Rose",
        "tagline":  "Pink — creative, stands out in the feed.",
        "bg":       "#0B1220",
        "surface":  "#111827",
        "accent_override": "#F472B6",
        "swatch":   "linear-gradient(135deg, #0B1220 50%, #F472B6 50%)",
    },
}

# ════════════════════════════════════════════════════════════════════════════════
# ROLE ROSTER
# ════════════════════════════════════════════════════════════════════════════════

ROLE_GROUPS: dict[str, dict[str, dict]] = {
    "🏛️ C-Suite": {
        "CEO": {
            "title": "Chief Executive Officer",
            "badge": "CEO  ·  EXECUTIVE BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Profit", "Orders", "Margin%"],
            "accent_color": "#F59E0B",
            "driver_focus": ["Category", "Region"],
            "tone": (
                "Strategic and big-picture. Lead with top-line revenue then profitability. "
                "One sharp strategic insight per section. Zero operational detail. "
                "If there is a tension between growth and margin, name it directly."
            ),
        },
        "CFO": {
            "title": "Chief Financial Officer",
            "badge": "CFO  ·  FINANCIAL BRIEFING",
            "primary_kpi": "Profit",
            "kpis": ["Profit", "Margin%", "Sales", "Orders"],
            "accent_color": "#10B981",
            "driver_focus": ["Category", "Region", "Segment"],
            "tone": (
                "Numbers-first. Margin health, cost discipline, profitability drivers. "
                "Flag any compression. One risk and one opportunity per briefing."
            ),
        },
        "COO": {
            "title": "Chief Operating Officer",
            "badge": "COO  ·  OPERATIONS BRIEFING",
            "primary_kpi": "Orders",
            "kpis": ["Orders", "Quantity", "AOV", "Sales"],
            "accent_color": "#2DD4BF",
            "driver_focus": ["Ship Mode", "Sub-Category", "Region"],
            "tone": (
                "Efficiency-focused. Order volume, fulfilment throughput, shipping performance. "
                "Surface bottlenecks. One fix with a clear owner and timeline."
            ),
        },
        "CTO": {
            "title": "Chief Technology Officer",
            "badge": "CTO  ·  TECHNOLOGY BRIEFING",
            "primary_kpi": "Orders",
            "kpis": ["Orders", "Sales", "Quantity", "Margin%"],
            "accent_color": "#8B5CF6",
            "driver_focus": ["Ship Mode", "Sub-Category", "Region"],
            "tone": (
                "Systems-thinking. Surface data quality issues and process breaks. "
                "One technical fix with measurable business impact."
            ),
        },
    },
    "🎯 Leadership": {
        "VP": {
            "title": "Vice President",
            "badge": "VP  ·  LEADERSHIP BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Profit", "Orders", "Quantity"],
            "accent_color": "#A78BFA",
            "driver_focus": ["Category", "Region", "Segment"],
            "tone": (
                "Balanced cross-functional. Cover both growth and efficiency. "
                "Surface tension between revenue and margin. "
                "Recommend one action spanning two functions."
            ),
        },
        "Director": {
            "title": "Director",
            "badge": "DIRECTOR  ·  LEADERSHIP BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Profit", "Orders", "Margin%"],
            "accent_color": "#F472B6",
            "driver_focus": ["Category", "Sub-Category", "Region"],
            "tone": (
                "Execution-focused. What is working, what is stalling. "
                "Bridge strategy to team-level action. One clear priority for the week."
            ),
        },
        "Associate Director": {
            "title": "Associate Director",
            "badge": "ASSOC DIR  ·  BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Orders", "Margin%", "AOV"],
            "accent_color": "#FB923C",
            "driver_focus": ["Sub-Category", "Region", "Segment"],
            "tone": (
                "Tactical and specific. Sub-category and segment level. "
                "What to escalate up and what to fix now."
            ),
        },
        "Sales Head": {
            "title": "Sales Head",
            "badge": "SALES  ·  COMMERCIAL BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Orders", "AOV", "Margin%"],
            "accent_color": "#60A5FA",
            "driver_focus": ["Sub-Category", "Region", "Segment"],
            "tone": (
                "Action-oriented. What is moving, what is stalling, where to push harder today. "
                "Name specific regions and segments. One clear move before end of day."
            ),
        },
    },
    "⚙️ Management": {
        "Senior Manager": {
            "title": "Senior Manager",
            "badge": "SR MANAGER  ·  BRIEFING",
            "primary_kpi": "Sales",
            "kpis": ["Sales", "Orders", "Quantity", "AOV"],
            "accent_color": "#34D399",
            "driver_focus": ["Sub-Category", "Segment", "Region"],
            "tone": (
                "Team-level detail. What specific products or customers are moving the number. "
                "One actionable recommendation the team can act on this week."
            ),
        },
        "Manager": {
            "title": "Manager",
            "badge": "MANAGER  ·  BRIEFING",
            "primary_kpi": "Orders",
            "kpis": ["Orders", "Sales", "Quantity", "AOV"],
            "accent_color": "#22D3EE",
            "driver_focus": ["Sub-Category", "Segment", "Ship Mode"],
            "tone": (
                "Ground-level detail. Specific products, shipments, customer groups. "
                "Two actionable tasks the team can complete today."
            ),
        },
        "Team Lead": {
            "title": "Team Lead",
            "badge": "TEAM LEAD  ·  BRIEFING",
            "primary_kpi": "Orders",
            "kpis": ["Orders", "Quantity", "Sales", "AOV"],
            "accent_color": "#A3E635",
            "driver_focus": ["Sub-Category", "Ship Mode", "Segment"],
            "tone": (
                "Operational and granular. What is shipping, what is delayed, "
                "what needs attention today. No strategic language — just the facts."
            ),
        },
        "Operations Head": {
            "title": "Operations Head",
            "badge": "OPS  ·  OPERATIONS BRIEFING",
            "primary_kpi": "Orders",
            "kpis": ["Orders", "Quantity", "AOV", "Sales"],
            "accent_color": "#2DD4BF",
            "driver_focus": ["Ship Mode", "Sub-Category", "Region"],
            "tone": (
                "Efficiency-focused. Order volume, fulfilment throughput, shipping performance. "
                "One fix with owner and timeline. Avoid revenue language — speak in units and orders."
            ),
        },
    },
}

# ════════════════════════════════════════════════════════════════════════════════
# HELP CONTENT  —  answers to every technical question a first-time user asks
# ════════════════════════════════════════════════════════════════════════════════

HELP_CONTENT = {
    "slack_channel_id": {
        "q": "How do I find my Slack Channel ID?",
        "a": (
            "**Method 1 (easiest):** \n"
            "1. Open Slack in a web browser (not the desktop app)\n"
            "2. Click on the channel you want ARIA to post to\n"
            "3. Look at the URL — it ends with something like `/C0B5U431C3U`\n"
            "4. That last part (starting with **C**) is your Channel ID\n\n"
            "**Method 2 (desktop app):**\n"
            "1. Right-click the channel name\n"
            "2. Select **Copy link**\n"
            "3. Paste it anywhere — the last segment after the final `/` is the ID"
        ),
    },
    "slack_bot_token": {
        "q": "How do I get a Slack Bot Token?",
        "a": (
            "1. Go to **[api.slack.com/apps](https://api.slack.com/apps)**\n"
            "2. Click **Create New App → From scratch**\n"
            "3. Name it **ARIA** and pick your workspace\n"
            "4. Go to **OAuth & Permissions**\n"
            "5. Under **Bot Token Scopes** add: `chat:write`, `files:write`, `channels:read`\n"
            "6. Click **Install to Workspace** at the top\n"
            "7. Copy the **Bot User OAuth Token** — it starts with `xoxb-`\n\n"
            "⚠️ Keep this token private — treat it like a password."
        ),
    },
    "teams_webhook": {
        "q": "How do I create a Teams Incoming Webhook?",
        "a": (
            "1. Open **Microsoft Teams**\n"
            "2. Go to the channel where you want ARIA to post\n"
            "3. Click the **⋯ (three dots)** next to the channel name\n"
            "4. Select **Connectors** (or **Manage channel → Connectors**)\n"
            "5. Search for **Incoming Webhook** and click **Add**\n"
            "6. Give it a name (e.g. **ARIA Daily Briefing**) and click **Create**\n"
            "7. Copy the webhook URL — it starts with `https://your-org.webhook.office.com/...`\n\n"
            "ℹ️ You need **Channel Manager** permissions to add connectors."
        ),
    },
    "gemini_key": {
        "q": "How do I get a free Gemini API key?",
        "a": (
            "1. Go to **[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)**\n"
            "2. Sign in with your Google account\n"
            "3. Click **Create API key**\n"
            "4. Copy the key — it starts with `AIza`\n\n"
            "**Free tier limits:**\n"
            "- 1,500 requests/day\n"
            "- 15 requests/minute\n"
            "- No credit card required\n"
            "- Works from India and most countries\n\n"
            "ARIA uses one request per role per morning, so the free tier is more than enough."
        ),
    },
    "github_account": {
        "q": "Why do I need a GitHub account?",
        "a": (
            "GitHub hosts the ARIA agent code and runs it automatically every morning via "
            "**GitHub Actions** — a free scheduling service.\n\n"
            "Think of it as: your ARIA lives in GitHub, and GitHub wakes it up at 9 AM every day "
            "to fetch your data, generate the card, and post it to Slack.\n\n"
            "**GitHub Free plan** is completely free and sufficient for ARIA:\n"
            "- 2,000 Action minutes/month (ARIA uses ~2 min/day)\n"
            "- Unlimited public and private repositories\n"
            "- No credit card required"
        ),
    },
    "github_pat": {
        "q": "What is a Personal Access Token and how do I create one?",
        "a": (
            "A Personal Access Token (PAT) is like a password that lets ARIA's setup wizard "
            "write files and secrets to your GitHub repo automatically.\n\n"
            "**Quickest way — direct link:**\n"
            "👉 Go straight to **[github.com/settings/tokens](https://github.com/settings/tokens)**\n\n"
            "**Step by step (if the link doesn't work):**\n"
            "1. Click your **profile picture** (your avatar, top-right corner of GitHub)\n"
            "2. In the dropdown that appears, click **Settings**\n"
            "   ⚠️ This is YOUR ACCOUNT settings — NOT the repo settings tab\n"
            "3. In the left sidebar, scroll all the way to the **bottom**\n"
            "4. Click **Developer settings** (very last item)\n"
            "5. Click **Personal access tokens → Tokens (classic)**\n"
            "6. Click **Generate new token (classic)**\n"
            "7. Give it a name like **ARIA**, set expiration to **90 days**\n"
            "8. Tick these two boxes: ✅ **repo** (select all under it), ✅ **workflow**\n"
            "9. Scroll to the bottom → click **Generate token**\n"
            "10. Copy the token immediately — it starts with `ghp_`\n\n"
            "⚠️ You only see the token **once**. Copy it before closing the page."
        ),
    },
    "github_repo": {
        "q": "How do I find my repository URL?",
        "a": (
            "1. Go to **[github.com](https://github.com)** and sign in\n"
            "2. Click the repository you created for ARIA\n"
            "3. The URL in your browser is your repo URL, e.g.:\n"
            "   `https://github.com/yourname/board-room-narrator`\n\n"
            "If you haven't created a repo yet, click **New** (green button on your GitHub home page), "
            "name it **board-room-narrator**, set it to **Private**, and click **Create repository**."
        ),
    },
    "github_secrets": {
        "q": "What are GitHub Secrets and why do they matter?",
        "a": (
            "GitHub Secrets are encrypted variables that your ARIA agent reads at runtime — "
            "without exposing them in your code.\n\n"
            "Instead of putting your Slack token directly in a file (risky!), you store it as "
            "a Secret. GitHub injects it as an environment variable when the workflow runs.\n\n"
            "ARIA needs these secrets:\n"
            "- `GEMINI_API_KEY` — your AI key (if using Gemini)\n"
            "- `SLACK_BOT_TOKEN` — your Slack bot token\n"
            "- `SLACK_CHANNEL_ID` — your Slack channel ID\n"
            "- `TEAMS_WEBHOOK_URL` — your Teams webhook (if using Teams)\n\n"
            "ARIA's setup wizard sets all of these automatically when you provide your PAT."
        ),
    },
    "google_sheets": {
        "q": "How do I share my Google Sheet so ARIA can read it?",
        "a": (
            "1. Open your Google Sheet\n"
            "2. Click **Share** (top right)\n"
            "3. Click **Change to anyone with the link**\n"
            "4. Set the role to **Viewer** (not Editor)\n"
            "5. Click **Copy link** and paste it in ARIA\n\n"
            "ℹ️ ARIA only reads the sheet — it never writes to it. "
            "Viewer access is all it needs."
        ),
    },
}


# ════════════════════════════════════════════════════════════════════════════════
# HELP SYSTEM UI
# ════════════════════════════════════════════════════════════════════════════════

def help_tip(key: str, label: str = "ℹ️ How?"):
    """Render a popover help button next to a field."""
    content = HELP_CONTENT.get(key, {})
    if not content:
        return
    with st.popover(label, use_container_width=False):
        st.markdown(f"**{content['q']}**")
        st.markdown(content["a"])


def render_help_sidebar():
    """ARIA help assistant in the sidebar — FAQ + step guides."""
    with st.sidebar:
        st.markdown("### ⚡ ARIA Help")
        st.caption("Ask ARIA anything about the setup.")

        # FAQ quick links
        faq_options = [c["q"] for c in HELP_CONTENT.values()]
        chosen = st.selectbox("Frequently asked questions", ["— Select a question —"] + faq_options,
                              label_visibility="collapsed")
        if chosen != "— Select a question —":
            key = next(k for k, v in HELP_CONTENT.items() if v["q"] == chosen)
            st.markdown(f"**{HELP_CONTENT[key]['q']}**")
            st.markdown(HELP_CONTENT[key]["a"])

        st.divider()
        st.markdown("**Quick links**")
        st.markdown("- [Slack API apps](https://api.slack.com/apps)")
        st.markdown("- [Gemini free key](https://aistudio.google.com/app/apikey)")
        st.markdown("- [GitHub sign up](https://github.com/signup)")
        st.markdown("- [GitHub Actions docs](https://docs.github.com/en/actions)")
        st.markdown("- [Teams webhooks guide](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook)")

        st.divider()
        st.caption("Still stuck? Open an issue on the ARIA GitHub repo.")


def render_theme_toggle():
    """Theme toggle button — top right of main area."""
    ui_cfg  = _load_ui_config()
    is_dark = ui_cfg.get("dark_mode", True)
    icon    = "☀️ Light" if is_dark else "🌙 Dark"

    # Align to top-right via columns trick
    _, tcol = st.columns([6, 1])
    if tcol.button(icon, key="theme_toggle", help="Switch between dark and light mode"):
        new_dark = not is_dark
        ui_cfg["dark_mode"] = new_dark
        _save_ui_config(ui_cfg)
        _apply_theme(new_dark)
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# KPI HEURISTICS
# ════════════════════════════════════════════════════════════════════════════════

KPI_HEURISTICS = [
    {"pattern": r"(sale|revenue|gmv|turnover|gross.?revenue|net.?sales)",
     "name": "Sales",   "agg": "sum",     "format": "currency",
     "description": "Total revenue generated in the period."},
    {"pattern": r"(profit|net.?income|ebit|ebitda|earnings)",
     "name": "Profit",  "agg": "sum",     "format": "currency",
     "description": "Net profit after costs."},
    {"pattern": r"(order.?id|order.?no|transaction.?id|invoice.?id|booking.?id)",
     "name": "Orders",  "agg": "nunique", "format": "integer",
     "description": "Unique orders / transactions in the period."},
    {"pattern": r"(^qty$|quantity|units?.?sold|^volume$|pieces?)",
     "name": "Quantity", "agg": "sum",    "format": "integer",
     "description": "Total units sold or shipped."},
    {"pattern": r"(customer.?id|customer.?no|client.?id|member.?id|user.?id)",
     "name": "Customers", "agg": "nunique", "format": "integer",
     "description": "Unique customers in the period."},
    {"pattern": r"(discount|rebate|coupon)",
     "name": "Discount", "agg": "sum",   "format": "currency",
     "description": "Total discount amount applied."},
    {"pattern": r"(^cost$|cogs|expense|spend)",
     "name": "Cost",    "agg": "sum",    "format": "currency",
     "description": "Total cost or expense."},
]


# ════════════════════════════════════════════════════════════════════════════════
# DATA UTILITIES
# ════════════════════════════════════════════════════════════════════════════════

def detect_kpis(df: pd.DataFrame) -> list:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    suggestions, used = [], set()
    for col in df.columns:
        col_lower = col.lower().replace(" ", "_").replace("-", "_")
        for h in KPI_HEURISTICS:
            if re.search(h["pattern"], col_lower):
                if h["agg"] == "sum" and col not in numeric_cols:
                    continue
                if h["name"] in used:
                    continue
                used.add(h["name"])
                formula = f"COUNT DISTINCT({col})" if h["agg"] == "nunique" else f"SUM({col})"
                suggestions.append({
                    "column": col, "suggested_name": h["name"], "user_name": h["name"],
                    "formula": formula, "agg": h["agg"], "format": h["format"],
                    "description": h["description"], "enabled": True,
                })
                break
    return suggestions


def detect_date_col(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "datetime" in str(df[col].dtype):
            return col
    for col in df.columns:
        if any(k in col.lower() for k in ["date", "day", "time", "period", "month"]):
            try:
                pd.to_datetime(df[col].dropna().iloc[:5])
                return col
            except Exception:
                pass
    return None


def _fmt_kv(v: float, fmt: str) -> str:
    if fmt == "currency":  return f"${v:,.0f}"
    if fmt == "integer":   return f"{int(v):,}"
    if fmt == "percent":   return f"{v:.1%}"
    return f"{v:,.2f}"


def _pct(a: float, b: float):
    return None if not b else (a - b) / abs(b)


def compute_preview_metrics(df: pd.DataFrame, kpis_cfg: list, date_col: str) -> dict:
    df = df.copy()
    try:
        df["_date"] = pd.to_datetime(df[date_col]).dt.date
    except Exception:
        return {}
    ref = df["_date"].max()

    def agg_val(sub, k):
        col = k["column"]
        if col not in sub.columns or sub.empty:
            return 0.0
        return float(sub[col].sum()) if k["agg"] == "sum" else float(sub[col].nunique())

    today = df[df["_date"] == ref]
    kpis: dict = {}
    for k in kpis_cfg:
        if not k.get("enabled", True):
            continue
        name = k["user_name"]
        curr = agg_val(today, k)
        kpis[name] = {
            "value":     curr,
            "value_fmt": _fmt_kv(curr, k["format"]),
            "dod_pct":   _pct(curr, agg_val(df[df["_date"] == ref - timedelta(1)],   k)),
            "wow_pct":   _pct(curr, agg_val(df[df["_date"] == ref - timedelta(7)],   k)),
            "mom_pct":   _pct(curr, agg_val(df[df["_date"] == ref - timedelta(30)],  k)),
            "yoy_pct":   _pct(curr, agg_val(df[df["_date"] == ref - timedelta(365)], k)),
        }

    s = kpis.get("Sales",  {}).get("value", 0)
    p = kpis.get("Profit", {}).get("value", 0)
    o = kpis.get("Orders", {}).get("value", 0)
    blank = {"dod_pct": None, "wow_pct": None, "mom_pct": None, "yoy_pct": None}
    if s and o:
        kpis["AOV"]    = {"value": s/o, "value_fmt": _fmt_kv(s/o, "currency"), **blank}
    if s and p:
        m = p / s
        kpis["Margin%"] = {"value": m, "value_fmt": _fmt_kv(m, "percent"), **blank}

    # 30-day sparkline
    primary_cfg = next((k for k in kpis_cfg if k.get("enabled")), None)
    trend_series: list[float] = []
    if primary_cfg:
        for i in range(29, -1, -1):
            d    = ref - timedelta(i)
            d_df = df[df["_date"] == d]
            trend_series.append(agg_val(d_df, primary_cfg))

    # Driver analysis
    drivers: list[dict] = []
    dim_priority = ["Category", "Sub-Category", "Region", "Segment", "Ship Mode"]
    dim_col      = next((c for c in dim_priority if c in df.columns), None)
    if dim_col and primary_cfg:
        pcol, pagg = primary_cfg["column"], primary_cfg["agg"]
        prev_df    = df[df["_date"] == ref - timedelta(365)]

        def grp(sub):
            if sub.empty or pcol not in sub.columns:
                return pd.Series(dtype=float)
            return sub.groupby(dim_col)[pcol].sum() if pagg == "sum" \
                else sub.groupby(dim_col)[pcol].nunique()

        curr_g = grp(today)
        prev_g = grp(prev_df)
        for member in curr_g.index:
            cv    = float(curr_g[member])
            pv    = float(prev_g.get(member, 0))
            delta = cv - pv
            if delta != 0:
                drivers.append({
                    "dimension": dim_col, "member": str(member),
                    "delta": delta, "delta_pct": (delta / pv) if pv else None,
                })
        drivers.sort(key=lambda d: abs(d["delta"]), reverse=True)

    return {
        "reference_date": str(ref),
        "kpis":           kpis,
        "trend_series":   trend_series,
        "drivers":        drivers,
    }


def build_stub_narrative(metrics: dict, role_cfg: dict) -> tuple[dict, object]:
    try:
        from narrative_generator import _generate_stub
        prim    = role_cfg.get("primary_kpi", "Sales")
        drivers = metrics.get("drivers", [])
        payload = {
            "reference_date": metrics.get("reference_date", str(date.today())),
            "kpis":           metrics.get("kpis", {}),
            "anomalies":      [],
            "drivers":        {prim: drivers, "Sales": drivers},
        }
        result = _generate_stub(payload, {}, role_cfg)
        result.reference_date = metrics.get("reference_date", str(date.today()))
        return result.to_dict() | {"reference_date": result.reference_date}, result
    except Exception:
        kpis = metrics.get("kpis", {})
        prim = role_cfg.get("primary_kpi", "Sales")
        pval = kpis.get(prim, {}).get("value_fmt", "—")
        ref  = metrics.get("reference_date", str(date.today()))
        rows = "\n".join(
            f"| {n} | {d.get('value_fmt','—')} | — | — | — | — |"
            for n, d in list(kpis.items())[:4]
        )
        d = {
            "reference_date":      ref,
            "headline":            f"{pval} on {ref} — {prim} performance briefing",
            "exec_summary":        f"{prim} closed at {pval} on {ref}. Broad-based performance.",
            "kpi_table_md":        "| Metric | Value | DoD | WoW | MoM | YoY |\n|---|---|---|---|---|---|\n" + rows,
            "anomaly":             "No anomalies on the 90-day window.",
            "recommended_action":  f"Review {prim} by dimension. Owner: Analytics. By end of week.",
            "speaker_notes":       f"Prepared for {role_cfg.get('title','Leadership')}.",
            "drivers_md":          "- Drivers unavailable for this preview.",
            "model":               "fallback",
            "role":                role_cfg.get("title", "Leadership"),
        }
        obj = SimpleNamespace(**d)
        return d, obj


# ════════════════════════════════════════════════════════════════════════════════
# SVG PREVIEW
# ════════════════════════════════════════════════════════════════════════════════

def generate_svg_preview(narrative_obj, metrics: dict, role_cfg: dict, style_key: str) -> str | None:
    try:
        from svg_generator import generate_svg
    except ImportError:
        try:
            from agent.svg_generator import generate_svg
        except ImportError:
            return None

    style    = CARD_STYLES.get(style_key, CARD_STYLES["role_accent"])
    prim_kpi = role_cfg.get("primary_kpi", "Sales")
    drivers  = metrics.get("drivers", [])

    payload = {
        "reference_date":  metrics.get("reference_date", str(date.today())),
        "kpis":            metrics.get("kpis", {}),
        "drivers":         {prim_kpi: drivers, "Sales": drivers},
        "daily_sales_30d": metrics.get("trend_series", []),
    }

    mod_role = dict(role_cfg)
    if style.get("accent_override"):
        mod_role["accent_color"] = style["accent_override"]

    try:
        svg = generate_svg(narrative_obj, payload, {}, mod_role)
    except Exception:
        return None

    bg      = style.get("bg", "#0B1220")
    surface = style.get("surface", "#111827")
    if bg != "#0B1220":
        svg = svg.replace('fill="#0B1220"', f'fill="{bg}"', 1)
    if surface != "#111827":
        svg = svg.replace('fill="#111827"', f'fill="{surface}"')

    svg = svg.replace(
        '<svg viewBox="0 0 800 480"',
        '<svg width="100%" viewBox="0 0 800 480"',
        1,
    )
    return svg


# ════════════════════════════════════════════════════════════════════════════════
# GITHUB AUTOMATION HELPERS
# ════════════════════════════════════════════════════════════════════════════════

def _gh_api(method: str, path: str, token: str, data=None):
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com{path}"
    return getattr(_req, method)(url, headers=headers, json=data, timeout=12)


def _gh_encrypt_secret(public_key_str: str, secret_value: str) -> str | None:
    try:
        from nacl import encoding, public as nacl_public
        pk  = nacl_public.PublicKey(public_key_str.encode(), encoding.Base64Encoder())
        box = nacl_public.SealedBox(pk)
        enc = box.encrypt(secret_value.encode())
        return base64.b64encode(enc).decode()
    except ImportError:
        return None


def _gh_set_secret(owner: str, repo: str, token: str, name: str, value: str) -> tuple[bool, str]:
    r = _gh_api("get", f"/repos/{owner}/{repo}/actions/secrets/public-key", token)
    if r.status_code != 200:
        return False, f"Could not fetch repo public key (HTTP {r.status_code})"
    pk_data = r.json()
    enc = _gh_encrypt_secret(pk_data["key"], value)
    if enc is None:
        return False, "PyNaCl not installed — run `pip install PyNaCl` then retry"
    r2 = _gh_api("put", f"/repos/{owner}/{repo}/actions/secrets/{name}", token,
                  data={"encrypted_value": enc, "key_id": pk_data["key_id"]})
    if r2.status_code in (201, 204):
        return True, "OK"
    return False, f"HTTP {r2.status_code}: {r2.text[:120]}"


def _gh_push_file(owner: str, repo: str, token: str,
                   path: str, content: str, message: str) -> tuple[bool, str]:
    """Create or update a file in the repo via GitHub Contents API."""
    # Check if file exists (need its SHA to update)
    r_get = _gh_api("get", f"/repos/{owner}/{repo}/contents/{path}", token)
    sha = r_get.json().get("sha") if r_get.status_code == 200 else None
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    r = _gh_api("put", f"/repos/{owner}/{repo}/contents/{path}", token, data=body)
    if r.status_code in (200, 201):
        return True, "OK"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def _parse_repo_url(url: str) -> tuple[str, str] | None:
    """Extract owner and repo name from a GitHub URL."""
    m = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", url.strip().rstrip("/"))
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    return None


def _generate_workflow_yaml(del_hour: int, tz: str, provider: str, channels: list) -> str:
    """Generate the GitHub Actions workflow YAML."""
    import pytz
    try:
        tz_obj  = pytz.timezone(tz)
        # Convert local hour to UTC
        from datetime import datetime as _dt
        local = _dt.now(tz_obj).replace(hour=del_hour, minute=0, second=0, microsecond=0)
        utc_h = local.astimezone(pytz.utc).hour
    except Exception:
        utc_h = max(0, del_hour - 5)  # rough IST→UTC fallback

    data_h = (utc_h - 1) % 24

    secrets_env = "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}\n" if provider == "gemini" else ""
    if "slack" in channels:
        secrets_env += (
            "          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}\n"
            "          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}\n"
        )
    if "teams" in channels:
        secrets_env += "          TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}\n"

    return f"""name: ARIA Daily Briefing

on:
  schedule:
    - cron: '0 {data_h} * * *'   # Data refresh  ({del_hour-1:02d}:00 {tz})
    - cron: '0 {utc_h} * * *'   # Narrative + delivery ({del_hour:02d}:00 {tz})
  workflow_dispatch:              # Manual trigger

jobs:
  aria-daily:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run ARIA
        env:
          GOOGLE_CREDS_JSON: ${{ secrets.GOOGLE_CREDS_JSON }}
          {secrets_env.strip()}
        run: python agent/main.py
"""


# ════════════════════════════════════════════════════════════════════════════════
# PROGRESS BAR
# ════════════════════════════════════════════════════════════════════════════════

def render_progress():
    step  = st.session_state.get("step", 1)
    items = ""
    for i, label in enumerate(STEP_LABELS, 1):
        if i < step:
            c, bg, tc, sym = "#10B981", "#10B98120", "#10B981", "✓"
        elif i == step:
            c, bg, tc, sym = "#10B981", "#10B981", "#ffffff", str(i)   # green filled = active
        else:
            c, bg, tc, sym = "#374151", "transparent", "#6B7280", str(i)
        items += (
            f'<div style="display:flex;flex-direction:column;align-items:center;min-width:52px">'
            f'<div style="width:28px;height:28px;border-radius:50%;background:{bg};border:2px solid {c};'
            f'display:flex;align-items:center;justify-content:center;color:{tc};font-size:11px;'
            f'font-weight:700;margin-bottom:4px">{sym}</div>'
            f'<div style="font-size:9px;color:{tc};text-align:center;line-height:1.2;max-width:50px">{label}</div>'
            f'</div>'
        )
        if i < TOTAL_STEPS:
            lc = "#10B981" if i < step else "#374151"
            items += f'<div style="flex:1;height:2px;background:{lc};margin:14px 2px 0"></div>'
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;padding:16px 0 24px;overflow-x:auto">{items}</div>',
        unsafe_allow_html=True,
    )


def nav_buttons(back=True, next_label="Next →", next_disabled=False):
    cols = st.columns([1, 2, 2])
    if back and st.session_state.step > 1:
        if cols[0].button("← Back", use_container_width=True):
            st.session_state.step -= 1
            st.rerun()
    if cols[2].button(next_label, type="primary", disabled=next_disabled, use_container_width=True):
        st.session_state.step += 1
        st.rerun()


def _resolve_role() -> dict:
    role_name = st.session_state.get("role_name", "CEO")
    for grp in ROLE_GROUPS.values():
        if role_name in grp:
            return grp[role_name]
    return ROLE_GROUPS["🏛️ C-Suite"]["CEO"]


def _clear_preview():
    for k in ("narrative", "narrative_obj", "metrics"):
        st.session_state.pop(k, None)


# ════════════════════════════════════════════════════════════════════════════════
# STEP 1 — WELCOME
# ════════════════════════════════════════════════════════════════════════════════

def step_welcome():
    st.markdown("""
    <div style="text-align:center;padding:32px 0 24px">
      <div style="font-size:56px;margin-bottom:8px">⚡</div>
      <h1 style="font-size:32px;font-weight:800;margin:0">Meet ARIA</h1>
      <p style="font-size:16px;color:#9CA3AF;margin:8px 0 0">
        Autonomous Report &amp; Insight AI Agent
      </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
ARIA turns your sales data into **board-ready briefing cards** — delivered to the right person, every morning, automatically.

Each role gets a personalised editorial card with:
- A **hero KPI** in large serif type with MoM / YoY deltas
- A **30-day sparkline** from your actual data
- **Driver bar charts** — what lifted, what dragged, by how much
- A single crisp **recommended action** with owner and timeline
- **Speaker notes** so you know what the board will ask before they do

This wizard takes **5 minutes**. At the end you'll have:
- A preview card built from your real data
- Your configuration saved automatically
- ARIA posting to Slack / Teams every morning — with **zero manual work**
    """)

    st.info("📊 Works with any Excel or CSV dataset — not just Superstore.", icon="ℹ️")
    st.info("❓ New to GitHub or Slack bots? Use the **Help** button in the sidebar — ARIA walks you through everything.", icon="💡")

    _, col, _ = st.columns([1, 2, 1])
    if col.button("Let's build your ARIA →", type="primary", use_container_width=True):
        st.session_state.step = 2
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# STEP 2 — UPLOAD DATA
# ════════════════════════════════════════════════════════════════════════════════

def step_upload_data():
    st.header("📁 Upload Your Data")
    st.caption("Excel (.xlsx / .xls) or CSV. Needs a date column and at least one numeric column.")

    tab1, tab2 = st.tabs(["📤 Upload File", "🔗 Google Sheets URL"])

    with tab1:
        uploaded = st.file_uploader("Drop your file here", type=["xlsx", "xls", "csv"])
        if uploaded:
            try:
                df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") \
                     else pd.read_excel(uploaded)
                st.session_state.df          = df
                st.session_state.data_source = "file"
                st.session_state.data_name   = uploaded.name
                _clear_preview()
                st.success(f"✅ Loaded {len(df):,} rows × {len(df.columns)} columns from **{uploaded.name}**")
                st.dataframe(df.head(5), use_container_width=True)
            except Exception as e:
                st.error(f"Could not read file: {e}")

    with tab2:
        gc1, gc2 = st.columns([6, 1])
        url = gc1.text_input(
            "Google Sheets URL",
            placeholder="https://docs.google.com/spreadsheets/d/...",
            label_visibility="collapsed",
        )
        with gc2:
            help_tip("google_sheets")
        if st.button("Load from Sheets") and url:
            with st.spinner("Fetching sheet…"):
                try:
                    csv_url = url.split("/edit")[0] + "/export?format=csv&gid=0"
                    df = pd.read_csv(csv_url)
                    st.session_state.df               = df
                    st.session_state.data_source      = "google_sheets"
                    st.session_state.google_sheet_url = url
                    _clear_preview()
                    st.success(f"✅ Loaded {len(df):,} rows from Google Sheets")
                    st.dataframe(df.head(5), use_container_width=True)
                except Exception as e:
                    st.error(f"Could not load: {e}")

    st.divider()
    st.caption("No file? Use the built-in Superstore sample.")
    if st.button("Use Superstore sample data"):
        sample = _ROOT / "Superstore.xls"
        if sample.exists():
            df = pd.read_excel(sample, sheet_name="Orders")
            st.session_state.df          = df
            st.session_state.data_source = "file"
            st.session_state.data_name   = "Superstore.xls"
            _clear_preview()
            st.success(f"✅ Loaded {len(df):,} rows from Superstore sample")
            st.rerun()
        else:
            st.warning("Superstore.xls not found in project root.")

    nav_buttons(back=True, next_label="Next: Discover KPIs →",
                next_disabled="df" not in st.session_state)


# ════════════════════════════════════════════════════════════════════════════════
# STEP 3 — DISCOVER KPIs
# ════════════════════════════════════════════════════════════════════════════════

def step_discover_kpis():
    st.header("🔬 Discover KPIs")
    st.caption("ARIA scanned your columns and suggested KPI definitions. Review, rename, or disable each one.")

    df: pd.DataFrame = st.session_state.get("df")
    if df is None:
        st.warning("No data loaded — go back to Step 2.")
        nav_buttons(back=True, next_label="Next →", next_disabled=True)
        return

    if "date_col" not in st.session_state:
        st.session_state.date_col = detect_date_col(df)

    st.session_state.date_col = st.selectbox(
        "📅 Date column",
        options=df.columns.tolist(),
        index=df.columns.tolist().index(st.session_state.date_col)
              if st.session_state.date_col in df.columns else 0,
        help="ARIA uses this to compute DoD / WoW / MoM / YoY comparisons.",
    )

    st.divider()

    if "kpis" not in st.session_state:
        st.session_state.kpis = detect_kpis(df)

    kpis = st.session_state.kpis
    if not kpis:
        st.warning("ARIA couldn't auto-detect KPIs. Add them manually below.")
    else:
        st.subheader("ARIA's KPI Suggestions")
        h1, h2, h3, h4 = st.columns([0.5, 2, 2, 3])
        h1.caption("On"); h2.caption("KPI Name")
        h3.caption("Source Column"); h4.caption("Formula — Description")

        updated: list = []
        for i, k in enumerate(kpis):
            c1, c2, c3, c4 = st.columns([0.5, 2, 2, 3])
            enabled   = c1.checkbox("", value=k["enabled"],   key=f"en_{i}", label_visibility="collapsed")
            user_name = c2.text_input("n", value=k["user_name"], key=f"nm_{i}",
                                       label_visibility="collapsed", disabled=not enabled)
            c3.markdown(f"`{k['column']}`")
            c4.caption(f"{k['formula']}  —  *{k['description']}*")
            updated.append({**k, "enabled": enabled, "user_name": user_name})
        st.session_state.kpis = updated

    st.divider()
    with st.expander("➕ Add a custom KPI"):
        m1, m2, m3, m4 = st.columns([2, 2, 1.5, 1.5])
        m_col  = m1.selectbox("Column", df.columns.tolist(), key="m_col")
        m_name = m2.text_input("KPI Name", key="m_name", placeholder="e.g. Returns")
        m_agg  = m3.selectbox("Aggregation", ["sum", "nunique", "mean"], key="m_agg")
        m_fmt  = m4.selectbox("Format", ["currency", "integer", "percent"], key="m_fmt")
        if st.button("Add KPI") and m_name:
            formula = f"COUNT DISTINCT({m_col})" if m_agg == "nunique" else f"SUM({m_col})"
            st.session_state.kpis.append({
                "column": m_col, "suggested_name": m_name, "user_name": m_name,
                "formula": formula, "agg": m_agg, "format": m_fmt,
                "description": "Custom KPI", "enabled": True,
            })
            st.rerun()

    active = sum(1 for k in st.session_state.get("kpis", []) if k.get("enabled"))
    nav_buttons(back=True,
                next_label=f"Next: Pick Role → ({active} KPI{'s' if active != 1 else ''} active)",
                next_disabled=active == 0)


# ════════════════════════════════════════════════════════════════════════════════
# STEP 4 — PICK ROLE
# ════════════════════════════════════════════════════════════════════════════════

def step_pick_role():
    st.header("👤 Pick Your Role")
    st.caption("Each role drives a different hero KPI, driver focus, accent colour, and narrative tone.")

    if "role_name" not in st.session_state:
        st.session_state.role_name = "CEO"

    for group_name, roles in ROLE_GROUPS.items():
        st.subheader(group_name)
        cols = st.columns(min(len(roles), 4))
        for col, (role_key, rd) in zip(cols, roles.items()):
            is_sel  = st.session_state.role_name == role_key
            border  = rd["accent_color"] if is_sel else "#374151"
            bg      = f"{rd['accent_color']}18" if is_sel else "transparent"
            col.markdown(
                f'<div style="border:2px solid {border};background:{bg};border-radius:10px;'
                f'padding:14px 16px;margin-bottom:6px;min-height:88px">'
                f'<div style="font-weight:700;font-size:13px;margin-bottom:3px">{role_key}</div>'
                f'<div style="font-size:11px;color:#9CA3AF;margin-bottom:6px">{rd["title"]}</div>'
                f'<div style="font-size:10px;color:{rd["accent_color"]}">'
                f'{"  ·  ".join(rd["kpis"][:3])}</div></div>',
                unsafe_allow_html=True,
            )
            if col.button("✓ Selected" if is_sel else "Select",
                          key=f"role_{role_key}", use_container_width=True,
                          type="primary" if is_sel else "secondary"):
                st.session_state.role_name = role_key
                _clear_preview()
                st.rerun()

    st.divider()
    with st.expander("🎨 Build a Custom Role"):
        c1, c2 = st.columns(2)
        c_title  = c1.text_input("Role Title",  placeholder="e.g. Regional Sales Director", key="c_title")
        c_badge  = c2.text_input("Badge Text",  placeholder="e.g. REGIONAL  ·  SALES BRIEFING", key="c_badge")
        avail    = [k["user_name"] for k in st.session_state.get("kpis", []) if k.get("enabled")] \
                   or ["Sales", "Profit", "Orders", "Margin%"]
        c3, c4, c5 = st.columns([2, 2, 1])
        c_kpis   = c3.multiselect("KPIs", avail, default=avail[:4], key="c_kpis")
        c_prim   = c4.selectbox("Primary KPI", c_kpis or avail, key="c_primary")
        c_color  = c5.color_picker("Accent", value="#60A5FA", key="c_color")
        c_tone   = st.text_area("Narrative tone",
                                 placeholder="Tactical. Focus on territory performance.", key="c_tone", height=70)
        if st.button("Use this custom role") and c_title:
            ROLE_GROUPS.setdefault("🎨 Custom", {})["Custom"] = {
                "title": c_title,
                "badge": c_badge or f"{c_title.upper()}  ·  BRIEFING",
                "primary_kpi": c_prim, "kpis": c_kpis or avail[:4],
                "accent_color": c_color, "driver_focus": ["Category", "Region"],
                "tone": c_tone or "Executive and strategic.",
            }
            st.session_state.role_name = "Custom"
            _clear_preview()
            st.success(f"Custom role '{c_title}' created!")
            st.rerun()

    nav_buttons(back=True,
                next_label=f"Next: Choose AI → (Role: {st.session_state.get('role_name','—')})",
                next_disabled=not st.session_state.get("role_name"))


# ════════════════════════════════════════════════════════════════════════════════
# STEP 5 — CHOOSE AI
# ════════════════════════════════════════════════════════════════════════════════

def step_choose_ai():
    st.header("🤖 Choose Your AI Engine")
    st.caption("The built-in engine works instantly with no key. Gemini gives richer, more natural prose.")

    if "ai_provider" not in st.session_state:
        st.session_state.ai_provider = "stub"

    col1, col2 = st.columns(2)
    for col, key, emoji, title, tagline, pros, con, ok_border, ok_bg in [
        (col1, "stub",   "⚙️", "Built-in Engine",  "No API key. Instant.",
         ["Zero setup", "Data-accurate", "Deterministic", "Offline"], "Less natural prose",
         "#10B981", "#10B98118"),
        (col2, "gemini", "✨", "Gemini 2.5 Flash",  "Free · 1 500/day · Works in India.",
         ["Best narrative quality", "Free — no credit card", "1 500 req/day", "Works in India"],
         "Requires API key setup",
         "#F59E0B", "#F59E0B18"),
    ]:
        is_sel = st.session_state.ai_provider == key
        border = ok_border if is_sel else "#374151"
        bg     = ok_bg     if is_sel else "transparent"
        pros_html = "".join(f"✅ {p}<br>" for p in pros)
        col.markdown(
            f'<div style="border:2px solid {border};background:{bg};border-radius:12px;'
            f'padding:20px;min-height:200px">'
            f'<div style="font-size:26px;margin-bottom:6px">{emoji}</div>'
            f'<div style="font-weight:800;font-size:15px;margin-bottom:3px">{title}</div>'
            f'<div style="font-size:11px;color:#9CA3AF;margin-bottom:10px">{tagline}</div>'
            f'<div style="font-size:12px;line-height:1.9">{pros_html}⚠️ {con}</div></div>',
            unsafe_allow_html=True,
        )
        if col.button(f"Use {title}", key=f"btn_{key}",
                      type="primary" if is_sel else "secondary", use_container_width=True):
            st.session_state.ai_provider = key
            st.rerun()

    if st.session_state.ai_provider == "gemini":
        ki1, ki2 = st.columns([6, 1])
        ki1.info(
            "Get your free Gemini key at "
            "[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)",
            icon="🔑",
        )
        with ki2:
            help_tip("gemini_key")
        kv = st.text_input("Paste key here (stored as a GitHub Secret, not locally)",
                            type="password", placeholder="AIza...", key="gemini_key_input")
        if kv:
            st.session_state.gemini_key = kv

    nav_buttons(back=True, next_label="Next: Set Delivery →")


# ════════════════════════════════════════════════════════════════════════════════
# STEP 6 — SET DELIVERY  (with inline help tips)
# ════════════════════════════════════════════════════════════════════════════════

def step_set_delivery():
    st.header("📬 Set Delivery")
    st.caption("Choose how ARIA sends your card each morning.")

    if "delivery" not in st.session_state:
        st.session_state.delivery = {"file": True, "slack": False, "teams": False}

    c1, c2, c3 = st.columns(3)

    # ── File ──────────────────────────────────────────────────────────────── #
    with c1:
        st.session_state.delivery["file"] = st.toggle("📄 Download File",
                                                        value=st.session_state.delivery["file"])
        if st.session_state.delivery["file"]:
            st.caption("Generates a Markdown + Word .docx in the `output/` folder.")

    # ── Slack ─────────────────────────────────────────────────────────────── #
    with c2:
        st.session_state.delivery["slack"] = st.toggle("💬 Slack",
                                                         value=st.session_state.delivery["slack"])
        if st.session_state.delivery["slack"]:
            tk1, tk2 = st.columns([5, 1])
            st.session_state.slack_bot_token_input = tk1.text_input(
                "Slack Bot Token", type="password", placeholder="xoxb-...",
                key="slack_token",
            )
            with tk2:
                st.markdown("<br>", unsafe_allow_html=True)
                help_tip("slack_bot_token", "ℹ️")

            ch1, ch2 = st.columns([5, 1])
            st.session_state.slack_channel_input = ch1.text_input(
                "Channel ID", placeholder="C0B5U431C3U",
                key="slack_channel",
            )
            with ch2:
                st.markdown("<br>", unsafe_allow_html=True)
                help_tip("slack_channel_id", "ℹ️")

            st.caption("Multiple channels? ARIA adds one per role automatically.")

    # ── Teams ─────────────────────────────────────────────────────────────── #
    with c3:
        st.session_state.delivery["teams"] = st.toggle("🟦 Microsoft Teams",
                                                         value=st.session_state.delivery["teams"])
        if st.session_state.delivery["teams"]:
            tw1, tw2 = st.columns([5, 1])
            st.session_state.teams_webhook_input = tw1.text_input(
                "Teams Webhook URL", type="password",
                placeholder="https://your-org.webhook.office.com/...",
                key="teams_webhook",
            )
            with tw2:
                st.markdown("<br>", unsafe_allow_html=True)
                help_tip("teams_webhook", "ℹ️")

    st.divider()
    st.subheader("⏰ Delivery Schedule")
    tz_opts = ["Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo",
               "America/Toronto", "America/New_York", "America/Los_Angeles",
               "Europe/London", "Europe/Paris", "Australia/Sydney"]
    c_tz, c_hr = st.columns(2)
    st.session_state.timezone      = c_tz.selectbox("Timezone", tz_opts, index=0, key="tz_sel")
    delivery_hour                  = c_hr.selectbox("Deliver at",
                                                     [f"{h:02d}:00" for h in range(6, 13)],
                                                     index=3, key="del_hr")
    st.session_state.delivery_hour = int(delivery_hour.split(":")[0])

    active = [k for k, v in st.session_state.delivery.items() if v]
    nav_buttons(back=True, next_label="Next: Preview Card →", next_disabled=len(active) == 0)


# ════════════════════════════════════════════════════════════════════════════════
# STEP 7 — PREVIEW CARD
# ════════════════════════════════════════════════════════════════════════════════

def step_preview_card():
    st.header("🎨 Preview Your Card")
    st.caption(
        "The card below is the **actual SVG** sent to Slack — not a mockup. "
        "Choose an accent theme, then generate from your real data."
    )

    role_cfg = _resolve_role()

    st.subheader("1. Choose Accent Theme")
    if "card_style" not in st.session_state:
        st.session_state.card_style = "role_accent"

    style_cols = st.columns(5)
    for col, (sk, sd) in zip(style_cols, CARD_STYLES.items()):
        is_sel  = st.session_state.card_style == sk
        preview_accent = sd.get("accent_override") or role_cfg.get("accent_color", "#F59E0B")
        swatch = f"linear-gradient(135deg, {sd['bg']} 50%, {preview_accent} 50%)"
        border = preview_accent if is_sel else "#374151"
        col.markdown(
            f'<div style="border:2.5px solid {border};border-radius:10px;padding:10px 8px;text-align:center">'
            f'<div style="width:100%;height:34px;border-radius:6px;'
            f'background:{swatch};margin-bottom:7px"></div>'
            f'<div style="font-size:11px;font-weight:700;color:{"#F59E0B" if is_sel else "var(--color-text-primary)"}">'
            f'{sd["label"]}</div>'
            f'<div style="font-size:9px;color:#9CA3AF;margin-top:3px;line-height:1.3">'
            f'{sd["tagline"]}</div></div>',
            unsafe_allow_html=True,
        )
        if col.button("✓ Active" if is_sel else "Choose",
                      key=f"sty_{sk}", use_container_width=True,
                      type="primary" if is_sel else "secondary"):
            st.session_state.card_style = sk
            _clear_preview()
            st.rerun()

    st.divider()
    st.subheader("2. Your Card")

    df       = st.session_state.get("df")
    date_col = st.session_state.get("date_col")
    kpis_cfg = [k for k in st.session_state.get("kpis", []) if k.get("enabled")]

    if "narrative" not in st.session_state or "metrics" not in st.session_state:
        if df is not None and date_col and kpis_cfg:
            with st.spinner("🧠 ARIA is computing your card from real data…"):
                try:
                    metrics                        = compute_preview_metrics(df, kpis_cfg, date_col)
                    narr_dict, narr_obj            = build_stub_narrative(metrics, role_cfg)
                    st.session_state.metrics       = metrics
                    st.session_state.narrative     = narr_dict
                    st.session_state.narrative_obj = narr_obj
                except Exception as e:
                    st.error(f"Preview generation failed: {e}")
        else:
            st.info(
                "No data loaded — showing a sample card. "
                "Go back to Step 2 to use your own data.", icon="ℹ️",
            )
            sample_metrics = {
                "reference_date": str(date.today() - timedelta(1)),
                "kpis": {
                    "Sales":   {"value":12847,"value_fmt":"$12,847","dod_pct":0.034,"wow_pct":-0.112,"mom_pct":0.078,"yoy_pct":0.114},
                    "Profit":  {"value": 3341,"value_fmt":"$3,341", "dod_pct":0.021,"wow_pct":-0.089,"mom_pct":0.051,"yoy_pct":0.093},
                    "Orders":  {"value":   47,"value_fmt":"47",     "dod_pct":-0.042,"wow_pct":-0.143,"mom_pct":0.062,"yoy_pct":0.087},
                    "Margin%": {"value":0.260,"value_fmt":"26.0%",  "dod_pct":None,"wow_pct":None,"mom_pct":None,"yoy_pct":None},
                    "AOV":     {"value":  273,"value_fmt":"$273",   "dod_pct":None,"wow_pct":None,"mom_pct":None,"yoy_pct":None},
                },
                "trend_series": [
                    8200,9100,7800,10200,9600,11400,8900,10800,12100,9700,
                    11200,10400,13100,11800,12400,10900,14200,13500,11700,12900,
                    10600,13800,12300,14500,11400,13200,12600,14800,13100,12847,
                ],
                "drivers": [
                    {"dimension":"Category","member":"Technology",    "delta":3240,"delta_pct":0.18},
                    {"dimension":"Category","member":"Office Supplies","delta":1420,"delta_pct":0.09},
                    {"dimension":"Category","member":"Furniture",      "delta":-1180,"delta_pct":-0.11},
                ],
            }
            narr_dict, narr_obj            = build_stub_narrative(sample_metrics, role_cfg)
            st.session_state.metrics       = sample_metrics
            st.session_state.narrative     = narr_dict
            st.session_state.narrative_obj = narr_obj

    if "narrative_obj" in st.session_state and "metrics" in st.session_state:
        import streamlit.components.v1 as components

        svg = generate_svg_preview(
            st.session_state.narrative_obj,
            st.session_state.metrics,
            role_cfg,
            st.session_state.card_style,
        )

        if svg:
            html_page = (
                "<!DOCTYPE html><html><head><style>"
                "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
                "</style></head>"
                f"<body>{svg}</body></html>"
            )
            components.html(html_page, height=430, scrolling=False)
        else:
            st.warning(
                "SVG generator not importable in this environment — "
                "the card will render correctly when running the full pipeline.",
                icon="⚠️",
            )

        narr = st.session_state.narrative
        m    = st.session_state.metrics
        style_label = CARD_STYLES[st.session_state.card_style]["label"]
        eff_accent  = (CARD_STYLES[st.session_state.card_style].get("accent_override")
                       or role_cfg.get("accent_color", "#F59E0B"))
        meta_col, regen_col = st.columns([4, 1])
        meta_col.caption(
            f"Theme: **{style_label}** · Accent: **{eff_accent}** · "
            f"Role: **{role_cfg.get('title','—')}** · "
            f"Engine: **{narr.get('model','stub')}** · "
            f"Drivers: **{len(m.get('drivers',[]))}** · "
            f"Trend pts: **{len(m.get('trend_series',[]))}**"
        )
        if regen_col.button("🔄 Refresh", use_container_width=True):
            _clear_preview()
            st.rerun()

    nav_buttons(back=True, next_label="Next: Go Live →")


# ════════════════════════════════════════════════════════════════════════════════
# STEP 8 — GO LIVE  (single form → one button → done)
# ════════════════════════════════════════════════════════════════════════════════

def _build_configs() -> tuple[str, str]:
    """Build config.yaml and roles.yaml strings from session state."""
    channels      = [k for k, v in st.session_state.get("delivery", {}).items() if v]
    kpis_cfg      = [k for k in st.session_state.get("kpis", []) if k.get("enabled")]
    provider      = st.session_state.get("ai_provider", "stub")
    tz            = st.session_state.get("timezone", "Asia/Kolkata")
    gs_url        = st.session_state.get("google_sheet_url", "")
    data_source   = st.session_state.get("data_source", "file")
    role_cfg      = _resolve_role()
    role_name     = st.session_state.get("role_name", "CEO")
    slack_channel = st.session_state.get("slack_channel", "")
    style_key     = st.session_state.get("card_style", "role_accent")
    eff_accent    = (CARD_STYLES[style_key].get("accent_override")
                     or role_cfg.get("accent_color", "#F59E0B"))

    # If the wizard session used a local file upload, fall back to the
    # production Google Sheet URL so GitHub Actions (which has no local file)
    # can still load data. Read the live sheet URL from the local config.yaml.
    if not gs_url or data_source != "google_sheets":
        try:
            _local_cfg = yaml.safe_load((_ROOT / "config.yaml").read_text())
            gs_url = _local_cfg.get("data", {}).get("google_sheet_url", gs_url)
        except Exception:
            pass

    config = {
        "data": {
            "google_sheet_url": gs_url,
            "excel_path":       "data/Superstore.xls",
            "sheet_name":       "Orders",
            "date_column":      st.session_state.get("date_col", "Order Date"),
            "timezone":         tz,
            "fallback_to_max_date_if_missing": False,
        },
        "metrics": {
            "kpis": [
                {"name": k["user_name"], "column": k["column"],
                 "agg": k["agg"], "format": k["format"]}
                for k in kpis_cfg
            ],
            "derived": ["AOV", "Margin%"],
            "anomaly_zscore_threshold": 2.0,
            "anomaly_lookback_days":    90,
        },
        "drivers": {
            "dimensions": ["Category", "Sub-Category", "Region", "Segment", "Ship Mode"],
            "top_n": 3,
        },
        "llm": {
            "provider": provider, "model": "gemini-2.5-flash",
            "temperature": 0.4, "max_output_tokens": 8192,
        },
        "delivery": {
            "channels": channels,
            "slack":  {"webhook_env_var": "SLACK_WEBHOOK_URL"},
            "teams":  {"webhook_env_var": "TEAMS_WEBHOOK_URL",
                       "title_prefix": "Daily Performance Briefing"},
            "file":   {"output_dir": "output", "formats": ["markdown", "docx"]},
        },
    }

    roles = {"roles": {role_name: {
        "title":         role_cfg["title"],
        "badge":         role_cfg["badge"],
        "slack_channel": slack_channel,
        "primary_kpi":   role_cfg["primary_kpi"],
        "kpis":          role_cfg["kpis"],
        "accent_color":  eff_accent,
        "driver_focus":  role_cfg.get("driver_focus", ["Category", "Region"]),
        "tone":          role_cfg["tone"],
    }}}

    return (
        yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False),
        yaml.dump(roles,  default_flow_style=False, allow_unicode=True, sort_keys=False),
    )


def _render_key_inputs(provider: str, channels: list):
    """Render API key input fields. Called from Step 8 for missing or editable keys."""
    if provider == "gemini":
        g1, g2 = st.columns([5, 1])
        gemini_key = g1.text_input(
            "Gemini API key",
            type="password",
            placeholder="AIza...",
            key="gemini_key_launch",
            value=st.session_state.get("gemini_key", ""),
        )
        with g2:
            st.markdown("<br>", unsafe_allow_html=True)
            help_tip("gemini_key", "ℹ️")
        if gemini_key:
            st.session_state.gemini_key = gemini_key

    if "slack" in channels:
        s1, s2 = st.columns([5, 1])
        slack_tok = s1.text_input(
            "Slack Bot Token",
            type="password",
            placeholder="xoxb-...",
            key="slack_tok_launch",
            value=st.session_state.get("slack_token", ""),
        )
        with s2:
            st.markdown("<br>", unsafe_allow_html=True)
            help_tip("slack_bot_token", "ℹ️")
        c1, c2 = st.columns([5, 1])
        slack_ch = c1.text_input(
            "Slack Channel ID",
            placeholder="C0B5U431C3U",
            key="slack_ch_launch",
            value=st.session_state.get("slack_channel", ""),
        )
        with c2:
            st.markdown("<br>", unsafe_allow_html=True)
            help_tip("slack_channel_id", "ℹ️")
        if slack_tok:
            st.session_state.slack_token   = slack_tok
        if slack_ch:
            st.session_state.slack_channel = slack_ch

    if "teams" in channels:
        t1, t2 = st.columns([5, 1])
        teams_wh = t1.text_input(
            "Microsoft Teams Webhook URL",
            type="password",
            placeholder="https://your-org.webhook.office.com/...",
            key="teams_wh_launch",
            value=st.session_state.get("teams_webhook", ""),
        )
        with t2:
            st.markdown("<br>", unsafe_allow_html=True)
            help_tip("teams_webhook", "ℹ️")
        if teams_wh:
            st.session_state.teams_webhook = teams_wh


def step_export_go():
    st.header("🚀 Go Live")

    config_yaml, roles_yaml = _build_configs()
    channels  = [k for k, v in st.session_state.get("delivery", {}).items() if v]
    provider  = st.session_state.get("ai_provider", "stub")
    tz        = st.session_state.get("timezone", "Asia/Kolkata")
    del_hour  = st.session_state.get("delivery_hour", 9)
    role_name = st.session_state.get("role_name", "CEO")
    role_cfg  = _resolve_role()
    kpis_cfg  = [k for k in st.session_state.get("kpis", []) if k.get("enabled")]
    style_key = st.session_state.get("card_style", "role_accent")
    eff_accent = (CARD_STYLES[style_key].get("accent_override")
                  or role_cfg.get("accent_color", "#F59E0B"))

    # ═══════════════════════════════════════════════════════════════════════════
    # SCREEN A — LAUNCH PAD  (the only screen the user needs to fill in)
    # ═══════════════════════════════════════════════════════════════════════════
    if "launch_done" not in st.session_state:

        # ── Config summary ────────────────────────────────────────────────── #
        st.markdown(
            f"Your card is ready. One last thing — paste your tokens below and "
            f"ARIA will handle **everything else** automatically."
        )
        st.markdown(
            f'<div style="background:#F59E0B18;border:1px solid #F59E0B40;border-radius:10px;'
            f'padding:14px 20px;margin-bottom:16px;font-size:13px;line-height:2">'
            f'📊 <b>{len(kpis_cfg)} KPIs</b> &nbsp;·&nbsp; '
            f'👤 <b>{role_name}</b> &nbsp;·&nbsp; '
            f'🎨 <b>{CARD_STYLES[style_key]["label"]}</b> &nbsp;·&nbsp; '
            f'⏰ <b>{del_hour}:00 {tz}</b> &nbsp;·&nbsp; '
            f'📬 <b>{", ".join(channels) or "File"}</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        # ── SECTION 1: GitHub PAT ─────────────────────────────────────────── #
        st.markdown("### 1️⃣ &nbsp; GitHub — where ARIA runs every morning")

        st.markdown(
            "GitHub Actions is a **free** cloud scheduler. ARIA will create your "
            "repo automatically — you just need to give it a token once.",
            help="GitHub Actions gives you 2,000 free minutes/month. ARIA uses ~2 min/day.",
        )

        # No account yet?
        no_gh = st.checkbox("I don't have a GitHub account yet", key="no_github")
        if no_gh:
            st.info(
                "**Create your free account (2 min):**\n\n"
                "👉 [github.com/signup](https://github.com/signup) — enter email, "
                "pick a username, verify email. Then come back here.",
                icon="👤",
            )

        pa1, pa2 = st.columns([5, 1])
        gh_username = pa1.text_input(
            "Your GitHub username",
            placeholder="e.g.  monaleenrath",
            key="gh_username_input",
        )

        st.markdown(
            "**Personal Access Token** &nbsp;"
            "<span style='font-size:12px;color:#9CA3AF'>— like a one-time password for ARIA</span>",
            unsafe_allow_html=True,
        )

        # Inline guide — collapsed by default so it's not overwhelming
        with st.expander("📋 How to get your token in 60 seconds"):
            st.markdown(
                "1. Click this link → **[github.com/settings/tokens/new](https://github.com/settings/tokens/new)**\n"
                "   *(opens GitHub's token page directly)*\n\n"
                "2. Give it any name, e.g. **ARIA**\n\n"
                "3. Tick **`repo`** ✅ and **`workflow`** ✅\n\n"
                "4. Scroll down → click **Generate token**\n\n"
                "5. Copy the token (starts with `ghp_`) and paste below\n\n"
                "⚠️ The token is shown **only once** — copy it before closing."
            )
            st.warning(
                "Open the link above in a **new tab**, keep this wizard open.",
                icon="💡",
            )

        pat_input = st.text_input(
            "Paste token here",
            type="password",
            placeholder="ghp_xxxxxxxxxxxxxxxxxxxx",
            key="gh_pat_input",
            label_visibility="collapsed",
        )

        # Validate token live (non-blocking)
        pat_ok = False
        if pat_input and gh_username:
            r = _gh_api("get", "/user", pat_input)
            if r.status_code == 200:
                actual_user = r.json().get("login", gh_username)
                st.success(f"✅ Token valid — connected as **{actual_user}**")
                st.session_state.gh_pat_valid    = pat_input
                st.session_state.gh_owner        = actual_user
                st.session_state.gh_repo         = "aria-daily-briefing"
                pat_ok = True
            elif r.status_code == 401:
                st.error("Token not recognised — double-check you copied it fully.")
            else:
                st.caption(f"Could not verify token right now (HTTP {r.status_code}) — you can still continue.")
                st.session_state.gh_pat_valid = pat_input
                st.session_state.gh_owner     = gh_username
                st.session_state.gh_repo      = "aria-daily-briefing"
                pat_ok = True

        st.divider()

        # ── SECTION 2: Delivery tokens (only what they need) ─────────────── #
        needs_keys = (
            (provider == "gemini") or
            ("slack" in channels) or
            ("teams" in channels)
        )

        if needs_keys:
            # Check which keys are already present from earlier steps
            _has_gemini    = bool(st.session_state.get("gemini_key"))
            _has_slack_tok = bool(st.session_state.get("slack_token"))
            _has_slack_ch  = bool(st.session_state.get("slack_channel"))
            _has_teams     = bool(st.session_state.get("teams_webhook"))

            _all_present = (
                (not (provider == "gemini") or _has_gemini) and
                (not ("slack" in channels) or (_has_slack_tok and _has_slack_ch)) and
                (not ("teams" in channels) or _has_teams)
            )

            if not _all_present:
                # Only show this section if something is actually missing
                st.markdown("### 2️⃣ &nbsp; Your API keys")
                st.caption("These are stored as encrypted secrets in your GitHub repo — never in any file.")
                _render_key_inputs(provider, channels)
                st.divider()

        # ── LAUNCH button ─────────────────────────────────────────────────── #
        ready = bool(st.session_state.get("gh_pat_valid") and gh_username)
        st.button(
            "🚀  Launch ARIA — set everything up automatically",
            type="primary",
            use_container_width=True,
            disabled=not ready,
            key="launch_btn",
            on_click=lambda: st.session_state.update({"launch_triggered": True}),
        )
        if not ready:
            st.caption("👆 Paste your GitHub token above to unlock.")

        # Manual fallback (collapsed, out of the way)
        with st.expander("⬇️ Prefer to set up manually? Download config files"):
            dl1, dl2 = st.columns(2)
            dl1.download_button("config.yaml", config_yaml, "config.yaml", "text/plain", use_container_width=True)
            dl2.download_button("roles.yaml",  roles_yaml,  "roles.yaml",  "text/plain", use_container_width=True)

        # ── Execute launch when button clicked ───────────────────────────── #
        if st.session_state.get("launch_triggered"):
            st.session_state.pop("launch_triggered", None)
            owner  = st.session_state.get("gh_owner", "")
            repo   = st.session_state.get("gh_repo",  "aria-daily-briefing")
            pat    = st.session_state.get("gh_pat_valid", "")
            results: dict[str, tuple[bool, str]] = {}

            progress = st.progress(0, text="Starting…")

            # 1. Create repo if it doesn't exist
            progress.progress(10, text="Creating your ARIA repo…")
            r_check = _gh_api("get", f"/repos/{owner}/{repo}", pat)
            if r_check.status_code == 404:
                r_create = _gh_api("post", "/user/repos", pat, data={
                    "name":        repo,
                    "description": "ARIA — Autonomous Report & Insight AI Agent",
                    "private":     True,
                    "auto_init":   True,
                })
                results["Create repo"] = (
                    r_create.status_code in (200, 201),
                    "created" if r_create.status_code in (200, 201) else f"HTTP {r_create.status_code}",
                )
                import time as _time; _time.sleep(2)  # let GitHub finish init
            else:
                results["Create repo"] = (True, "already exists")

            # 2. Push config files
            progress.progress(20, text="Pushing config files…")
            for fname, content, msg in [
                ("config.yaml", config_yaml, "chore: ARIA wizard config"),
                ("roles.yaml",  roles_yaml,  "chore: ARIA wizard roles"),
            ]:
                ok, m = _gh_push_file(owner, repo, pat, fname, content, msg)
                results[fname] = (ok, m)

            # 2b. Push requirements.txt and all agent code
            progress.progress(35, text="Uploading agent code…")
            _agent_files = [
                "requirements.txt",
                "agent/__init__.py",
                "agent/main.py",
                "agent/narrative_generator.py",
                "agent/svg_generator.py",
                "agent/metrics_engine.py",
                "agent/data_loader.py",
                "agent/driver_analysis.py",
                "agent/slack_publisher.py",
                "agent/report_writer.py",
                "agent/teams_publisher.py",
            ]
            for _rel in _agent_files:
                _local = _ROOT / _rel
                if _local.exists():
                    try:
                        _content = _local.read_text(encoding="utf-8")
                    except Exception:
                        _content = _local.read_bytes().decode("latin-1")
                    ok, m = _gh_push_file(owner, repo, pat, _rel, _content,
                                          f"chore: ARIA agent — {_rel}")
                    results[f"📄 {_rel}"] = (ok, m)
                else:
                    results[f"📄 {_rel}"] = (False, "not found locally")

            # 3. Push workflow
            progress.progress(60, text="Setting up daily schedule…")
            workflow_yaml = _generate_workflow_yaml(del_hour, tz, provider, channels)
            ok, m = _gh_push_file(owner, repo, pat,
                                   ".github/workflows/aria-daily.yml",
                                   workflow_yaml, "chore: ARIA daily workflow")
            results["workflow"] = (ok, m)

            # 4. Set secrets
            progress.progress(80, text="Encrypting and storing your API keys…")
            secrets_to_set: dict[str, str] = {}

            # Google credentials — try st.secrets first (Streamlit Cloud),
            # then fall back to local file (local dev). Not needed for public sheets
            # (data_loader uses CSV export), but set it if available for future use.
            _gcreds_val = None
            try:
                _gcreds_val = st.secrets.get("GOOGLE_CREDS_JSON")
            except Exception:
                pass
            if not _gcreds_val:
                _gcreds_path = _ROOT / "google_creds.json"
                if _gcreds_path.exists():
                    try:
                        _gcreds_val = _gcreds_path.read_text(encoding="utf-8")
                    except Exception:
                        pass
            if _gcreds_val:
                secrets_to_set["GOOGLE_CREDS_JSON"] = _gcreds_val

            if provider == "gemini" and st.session_state.get("gemini_key"):
                secrets_to_set["GEMINI_API_KEY"] = st.session_state.gemini_key
            if "slack" in channels:
                if st.session_state.get("slack_token"):
                    secrets_to_set["SLACK_BOT_TOKEN"] = st.session_state.slack_token
                if st.session_state.get("slack_channel"):
                    secrets_to_set["SLACK_CHANNEL_ID"] = st.session_state.slack_channel
            if "teams" in channels and st.session_state.get("teams_webhook"):
                secrets_to_set["TEAMS_WEBHOOK_URL"] = st.session_state.teams_webhook

            for sname, sval in secrets_to_set.items():
                ok, m = _gh_set_secret(owner, repo, pat, sname, sval)
                results[f"🔑 {sname}"] = (ok, m)

            progress.progress(100, text="Done!")

            st.session_state.setup_results = results
            st.session_state.launch_done   = True
            st.session_state.gh_repo_final = repo
            st.rerun()

    # ═══════════════════════════════════════════════════════════════════════════
    # SCREEN B — RESULT  (auto-shown after launch)
    # ═══════════════════════════════════════════════════════════════════════════
    else:
        owner    = st.session_state.get("gh_owner", "—")
        repo     = st.session_state.get("gh_repo_final", "aria-daily-briefing")
        results  = st.session_state.get("setup_results", {})
        all_ok   = all(v[0] for v in results.values())

        if all_ok:
            st.markdown("""
            <div style="text-align:center;padding:24px 0 12px">
              <div style="font-size:52px">🎉</div>
              <h2 style="margin:8px 0 4px">ARIA is live!</h2>
              <p style="color:#9CA3AF;margin:0">Everything was set up automatically.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.warning("Setup completed with some issues — see details below.", icon="⚠️")

        # Result detail (collapsed by default if all OK)
        with st.expander("Setup log", expanded=not all_ok):
            for item, (ok, msg) in results.items():
                st.markdown(f"{'✅' if ok else '❌'} **{item}** — {msg if not ok else 'done'}")

        if not all_ok:
            failed_secrets = [
                k.replace("🔑 ", "") for k, (ok, _) in results.items()
                if not ok and "🔑" in k
            ]
            if failed_secrets:
                st.info(
                    "The config files were pushed but some secrets need to be added manually.\n\n"
                    "Go to: **your repo → Settings → Secrets and variables → Actions → New repository secret**\n\n"
                    + "\n".join(f"- `{s}`" for s in failed_secrets),
                    icon="🔑",
                )

        st.divider()

        repo_url = f"https://github.com/{owner}/{repo}"

        # Determine if the scheduled hour is still upcoming today
        try:
            import pytz as _pytz, datetime as _dtt
            _now_local = _dtt.datetime.now(_pytz.timezone(tz))
            _delivery_day = "Today" if _now_local.hour < del_hour else "Tomorrow"
        except Exception:
            _delivery_day = "Tomorrow"

        st.markdown(f"""
| | |
|---|---|
| 📦 Repository | [{owner}/{repo}]({repo_url}) |
| 👤 Role | {role_name} — {role_cfg['title']} |
| ⏰ First delivery | {_delivery_day} at **{del_hour}:00 {tz}** |
| 📬 Delivery | {', '.join(channels) or 'File only'} |
        """)

        st.info(
            f"**Want to test right now?**\n\n"
            f"Go to [{owner}/{repo} → Actions → ARIA Daily Briefing → Run workflow]"
            f"({repo_url}/actions)",
            icon="▶️",
        )

        # "Edit Settings" keeps all wizard data — just clears the launch result
        # so the user goes back to Step 8 with everything pre-filled.
        c_edit, c_reset = st.columns([2, 1])
        if c_edit.button("← Edit Settings", use_container_width=True):
            _launch_keys = ["launch_done", "setup_results", "launch_triggered",
                            "gh_pat_valid", "gh_owner", "gh_repo", "gh_repo_final",
                            "gh_username_input", "gh_pat_input"]
            for k in _launch_keys:
                st.session_state.pop(k, None)
            st.rerun()
        if c_reset.button("Full Reset", use_container_width=True, type="secondary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════════

def main():
    st.markdown("""
    <style>
      section.main > div { max-width: 820px; margin: 0 auto; }
      .stButton > button  { border-radius: 8px; font-weight: 600; }

      /* ── Green primary buttons (Next, Launch, etc.) ── */
      .stButton > button[kind="primary"] {
          background-color: #10B981 !important;
          border-color:     #059669 !important;
          color:            #ffffff !important;
      }
      .stButton > button[kind="primary"]:hover {
          background-color: #059669 !important;
          border-color:     #047857 !important;
      }
      .stButton > button[kind="primary"]:disabled {
          background-color: #374151 !important;
          border-color:     #374151 !important;
          color:            #6B7280 !important;
      }
    </style>
    """, unsafe_allow_html=True)

    # Theme toggle — top right
    render_theme_toggle()

    # Help sidebar
    render_help_sidebar()

    if "step" not in st.session_state:
        st.session_state.step = 1

    render_progress()

    step = st.session_state.step
    if   step == 1: step_welcome()
    elif step == 2: step_upload_data()
    elif step == 3: step_discover_kpis()
    elif step == 4: step_pick_role()
    elif step == 5: step_choose_ai()
    elif step == 6: step_set_delivery()
    elif step == 7: step_preview_card()
    elif step == 8: step_export_go()
    else:
        st.session_state.step = 1
        st.rerun()


if __name__ == "__main__":
    main()
