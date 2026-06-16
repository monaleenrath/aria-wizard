"""
narrative_generator.py
----------------------
Produces a *card-style* executive briefing:

    ⚡  Headline
    📄  Exec Summary
    ⚠️  Anomaly
    →   Recommended Action
    🎙️  Speaker Notes

Role-aware: tone, KPI focus, and language tier adapt to the recipient's role.

ROLE TIERS (controls phrasing vocabulary, urgency, abstraction level):
  ● C-Suite (CEO, CFO, COO, CTO)     — board-level, strategic, risk-aware
  ● Leadership (VP, Director, AD)    — cross-functional, bridge strategy↔execution
  ● Commercial (Sales Head)          — commercial, urgent, region/segment specific
  ● Operations (Ops Head, COO)       — units & orders, fulfilment, process-fix
  ● Management (Sr Mgr, Mgr, Lead)   — granular, team-actionable, today-focused

Backed by Gemini 2.5 Flash with a deterministic stub fallback.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import date as _date_type
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Role tier classification
# --------------------------------------------------------------------------- #

_C_SUITE      = {"CEO", "CFO", "COO", "CTO",
                  "Chief Executive Officer", "Chief Financial Officer",
                  "Chief Operating Officer", "Chief Technology Officer"}
_LEADERSHIP   = {"VP", "Vice President", "Director", "Associate Director",
                  "Assoc Dir"}
_COMMERCIAL   = {"Sales Head", "Commercial Head", "Chief Revenue Officer", "CRO"}
_OPERATIONS   = {"Operations Head", "Chief Operating Officer", "COO"}
_MANAGEMENT   = {"Senior Manager", "Sr Manager", "Manager", "Team Lead"}

def _tier(role_title: str) -> str:
    """Return the narrative tier for a given role title string."""
    if role_title in _C_SUITE:          return "c_suite"
    if role_title in _LEADERSHIP:       return "leadership"
    if role_title in _COMMERCIAL:       return "commercial"
    if role_title in _OPERATIONS:       return "operations"
    if role_title in _MANAGEMENT:       return "management"
    return "leadership"   # safe fallback


# --------------------------------------------------------------------------- #
# Tier vocabulary: verbs, adjectives, framing phrases
# --------------------------------------------------------------------------- #

_TIER_VOCAB = {
    "c_suite": {
        "momentum_verb":  "accelerated",
        "drag_verb":      "compressed",
        "action_verb":    "Align",
        "time_box":       "Before the next board review",
        "urgency_phrase": "worth a capital-allocation call",
        "impact_prefix":  "Potential P&L recovery of",
        "speaker_open":   "The board will probe the growth-vs-margin trade-off",
        "bright_prefix":  "Strategic bright spot:",
        "call_owner":     "C-Suite + Finance",
    },
    "leadership": {
        "momentum_verb":  "outperformed",
        "drag_verb":      "underperformed",
        "action_verb":    "Prioritise",
        "time_box":       "By end of week",
        "urgency_phrase": "deserves cross-functional attention",
        "impact_prefix":  "Expected recovery of",
        "speaker_open":   "Leadership will ask which function owns the fix",
        "bright_prefix":  "Cross-functional bright spot:",
        "call_owner":     "VP + relevant function heads",
    },
    "commercial": {
        "momentum_verb":  "drove hard on",
        "drag_verb":      "stalled in",
        "action_verb":    "Push",
        "time_box":       "Before end of today",
        "urgency_phrase": "needs a commercial response now",
        "impact_prefix":  "Revenue upside of",
        "speaker_open":   "Sales leadership will ask which rep or territory owns this",
        "bright_prefix":  "Commercial bright spot:",
        "call_owner":     "Sales Head + Regional Managers",
    },
    "operations": {
        "momentum_verb":  "processed",
        "drag_verb":      "bottlenecked at",
        "action_verb":    "Fix",
        "time_box":       "By next fulfilment cycle",
        "urgency_phrase": "blocks throughput",
        "impact_prefix":  "Order recovery of",
        "speaker_open":   "Ops will ask about the SLA impact and root cause",
        "bright_prefix":  "Throughput bright spot:",
        "call_owner":     "Operations + Logistics",
    },
    "management": {
        "momentum_verb":  "moved",
        "drag_verb":      "dragged on",
        "action_verb":    "Action",
        "time_box":       "Today",
        "urgency_phrase": "needs the team's attention right now",
        "impact_prefix":  "Expected lift of",
        "speaker_open":   "The team will want a clear list of what to do first",
        "bright_prefix":  "Bright spot for the team:",
        "call_owner":     "Team Lead + direct reports",
    },
}


# --------------------------------------------------------------------------- #
# System prompt builder — role-aware (used by Gemini)
# --------------------------------------------------------------------------- #

_BASE_SYSTEM = """You are ARIA — Autonomous Report & Insight AI Agent.
You are the Chief Data Storyteller briefing {role_title}.
Your audience has 90 seconds. They are smart, impatient, and allergic to corporate filler.

ROLE CONTEXT:
{role_tone}

AUDIENCE TIER: {tier_label}
{tier_guidance}

━━━ THREE-ACT NARRATIVE STRUCTURE ━━━
Every section must follow this flow — without labelling the acts:
  ACT 1 — THE NUMBER  : Lead with the most material metric for {role_title}. Anchor in reality.
  ACT 2 — THE CAUSE   : Name the specific driver. One dimension, one member, one verb.
  ACT 3 — THE IMPLICATION: What does this mean for {role_title}'s decisions in the next 48 hours?

━━━ COMPOUND SIGNALS — DETECT AND NAME ━━━
Look for these patterns in the data and call them out explicitly if present:
  • Sales ▲ + Margin ▼  → "volume at the cost of quality — discount dependency signal"
  • Orders ▲ + AOV ▼   → "transaction count growing but basket shrinking"
  • Sales ▲ + Orders ▼  → "fewer deals, bigger tickets — mix shift to high-value"
  • Profit ▼ + Sales ▲  → "top-line growing, bottom-line eroding — cost pressure"
  • All KPIs ▲ + Anomaly → "broad momentum with one outlier worth watching"
If no compound signal exists, do not force one.

━━━ MOMENTUM CHARACTERISATION ━━━
Use trend_series (30 daily values) if provided to characterise trajectory:
  • 3+ consecutive rises at increasing rate → "accelerating"
  • 3+ consecutive falls                   → "decelerating"
  • Sharp drop followed by recovery        → "recovering"
  • Oscillating within ±3%                 → "plateauing"
If trend_series is absent, infer direction from DoD / WoW / MoM alignment.
Include one momentum word in the headline or exec_summary.

━━━ YOUR VOICE (always) ━━━
  • Confident, conversational, never preachy.
  • Lead every section with a number, a name, or a verb — NEVER with "Overall",
    "In terms of", "It is worth noting", or "As we can see".
  • Banned phrases: "significantly", "leverage", "going forward", "robust",
    "drive synergies", "exciting opportunity", "deep dive", "it's important to".
  • Quantify everything. "+11.4%" not "strong growth". "$3,240 drag" not "notable decline".
  • Name names. "Furniture in the West" not "underperforming products".
  • One adjective per sentence, max.
  • Present tense for what IS happening. Past tense for what HAS happened.

KPI FOCUS for this role: {role_kpis}
Lead the narrative with these metrics. De-emphasise others unless they contain a compound signal.

━━━ QUESTIONS {role_title} WILL ASK FIRST ━━━
{tier_questions}
Address at least one of these proactively inside speaker_notes.

You will receive a JSON payload with:
  — kpis        : metric values with DoD / WoW / MoM / YoY % changes
  — targets     : target values + achievement_pct per KPI (present only when targets are configured)
  — anomalies   : z-score flagged outliers (direction, value, expected, zscore)
  — drivers     : ranked dimension members by delta impact
  — trend_series: 30-day daily values for the primary KPI (may be absent)

When targets are present, include achievement % in exec_summary S1 (e.g. "…at 94% of target") and in kpi_table_md as an extra "vs Target" column.

Return a JSON object with EXACTLY these keys:

{{
  "headline":           "≤14 words. Lead with the number. Embed momentum word. Name compound signal if found.",
  "exec_summary":       "Exactly 3 sentences. S1: the fact (metric + value + direction). S2: the cause (dimension → member + verb + delta). S3: the implication for {role_title}'s next decision.",
  "kpi_table_md":       "Markdown table with header row: | Metric | Value | DoD | WoW | MoM | YoY |",
  "anomaly":            "One tight paragraph: metric name, direction, magnitude, z-score, one hypothesis for the cause, and the urgency level for {role_title}. OR exactly: 'No anomalies on the 90-day window. All metrics inside normal variance.'",
  "recommended_action": "Exactly this format: '<Verb> <specific object>. Owner: <named function>. Deadline: <specific timeframe>. Success metric: <one measurable outcome>.'",
  "speaker_notes":      "Exactly 2 sentences. S1: the first question {role_title} will ask and your suggested one-line answer. S2: the bright spot to name before they ask.",
  "drivers_md":         "3–5 markdown bullets. Each: '**<Dimension> → <Member>** <tier-verb> {primary_kpi} by $X (Y% vs prior).' Add one compound-signal bullet at the end if detected."
}}

Return ONLY the JSON object. No preamble, no markdown fences, no trailing commentary."""

_TIER_GUIDANCE = {
    "c_suite": (
        "This is a board-level audience. Lead with strategic implications — growth vs margin trade-offs, "
        "capital exposure, competitive positioning. One insight per section. Zero operational detail. "
        "Frame every number in terms of shareholder value or strategic risk."
    ),
    "leadership": (
        "This audience bridges strategy and execution. Cover growth AND efficiency. "
        "Name which functions own the move. Recommend one action that spans two functions. "
        "Flag any escalation risk — what could land on the C-Suite's desk if not fixed this week."
    ),
    "commercial": (
        "This audience lives in deals and territories. Be verb-heavy and urgent. "
        "Name specific regions, sub-categories, and customer segments. "
        "Give them one clear commercial move to make before end of day. "
        "No cost or margin language unless it is the root cause of a revenue miss."
    ),
    "operations": (
        "This audience speaks in units and orders, not dollars. "
        "Surface Ship Mode anomalies, Quantity bottlenecks, fulfilment gaps. "
        "One fix with a clear owner, a clear timeline, and measurable expected impact on throughput. "
        "Avoid revenue language unless it is the direct consequence of an ops failure."
    ),
    "management": (
        "This audience needs an action list, not a strategy deck. "
        "Be specific: which product, which customer, which shipment. "
        "Give two tasks the team can complete today. "
        "No strategic framing — just the facts and the next step."
    ),
}

_TIER_LABELS = {
    "c_suite":    "C-Suite / Board-level",
    "leadership": "Leadership / Cross-functional",
    "commercial": "Commercial / Sales",
    "operations": "Operations / Fulfilment",
    "management": "Management / Team",
}

_TIER_QUESTIONS = {
    "c_suite": (
        "• Is growth coming at the cost of margin — is this structurally sound?\n"
        "• Which market or segment is responsible, and is it one-off or systemic?\n"
        "• Do we need to reforecast, and what's the capital implication?\n"
        "• Are we winning the right customers, or buying volume with discounts?"
    ),
    "leadership": (
        "• Which function owns the fix — and is it already resourced?\n"
        "• Will this land on the C-Suite's desk if we don't act this week?\n"
        "• Is this a signal or noise — how confident are we in the data?\n"
        "• Which team needs unblocking and what exactly is the ask?"
    ),
    "commercial": (
        "• Which territory, rep, or segment is driving (or dragging) the number?\n"
        "• Is this a volume problem, a price problem, or a product-mix problem?\n"
        "• What does the pipeline look like for the next two weeks?\n"
        "• Are we losing deals on price, or simply not generating enough volume?"
    ),
    "operations": (
        "• Which Ship Mode or fulfilment node is the bottleneck?\n"
        "• What's the SLA impact — are we at risk of customer churn?\n"
        "• Is this a demand spike or a capacity/process failure?\n"
        "• What's the 48-hour fix, and who makes the call?"
    ),
    "management": (
        "• Who on the team is responsible for the drag dimension?\n"
        "• What can we close or fix today — no multi-week projects?\n"
        "• What does the team need from leadership to unblock this?\n"
        "• Are there quick wins we can bank before end of week?"
    ),
}


def _build_system_prompt(role_config: dict) -> str:
    role_title = role_config.get("title", "Leadership")
    tier = role_config.get("tier") or _tier(role_title)
    return _BASE_SYSTEM.format(
        role_title    = role_title,
        role_tone     = role_config.get("tone", "Executive and strategic.").strip(),
        role_kpis     = ", ".join(role_config.get("kpis", ["Sales", "Profit", "Orders", "Margin%"])),
        primary_kpi   = role_config.get("primary_kpi", "Sales"),
        tier_label    = _TIER_LABELS.get(tier, "Leadership"),
        tier_guidance = _TIER_GUIDANCE.get(tier, _TIER_GUIDANCE["leadership"]),
        tier_questions= _TIER_QUESTIONS.get(tier, _TIER_QUESTIONS["leadership"]),
    )


# --------------------------------------------------------------------------- #
# NarrativeResult
# --------------------------------------------------------------------------- #

@dataclass
class NarrativeResult:
    headline: str
    exec_summary: str
    kpi_table_md: str
    anomaly: str
    recommended_action: str
    speaker_notes: str
    drivers_md: str
    model: str
    role: str = "General"
    tier: str = "leadership"        # c_suite | leadership | commercial | operations | management

    # optional field set by the caller
    reference_date: str = ""

    def to_markdown(self) -> str:
        """Render the card as a single markdown document."""
        return f"""# ⚡ {self.headline}
*Role: {self.role}*

## 📄 Executive Summary
{self.exec_summary}

## 📊 Key Performance Indicators
{self.kpi_table_md}

## 🔎 What Drove the Move
{self.drivers_md}

## ⚠️ Anomaly & Watch-out
{self.anomaly}

## ➡️ Recommended Action
{self.recommended_action}

## 🎙️ Speaker Notes
{self.speaker_notes}
"""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fmt_delta(pct):
    if pct is None:
        return "—"
    arrow = "▲" if pct >= 0 else "▼"
    return f"{arrow} {pct*100:+.1f}%"


def _safe_parse_json(text: str) -> dict:
    """LLMs sometimes wrap JSON in ```json fences — strip them."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    return json.loads(t)


# --------------------------------------------------------------------------- #
# Provider: Gemini
# --------------------------------------------------------------------------- #

def _generate_gemini(payload: dict, cfg: dict, role_config: dict) -> NarrativeResult:
    import time as _time
    from google import genai
    from google.genai import types
    from google.genai.errors import ServerError

    # Support per-test-case key rotation via gemini_api_key_env in llm config
    _key_env_var = cfg.get("gemini_api_key_env", "GEMINI_API_KEY")
    api_key = os.getenv(_key_env_var) or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"Gemini API key not set. Expected env var: {_key_env_var} "
            "(or fallback GEMINI_API_KEY). Add it to your .env or GitHub Secrets."
        )

    client     = genai.Client(api_key=api_key)
    model_name = cfg.get("model", "gemini-2.5-flash")
    system_prompt = _build_system_prompt(role_config)

    role_title = role_config.get("title", "Leadership")
    user_prompt = (
        f"Draft today's briefing card for {role_title} from this data.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )

    gen_cfg = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=cfg.get("temperature", 0.4),
        max_output_tokens=cfg.get("max_output_tokens", 8192),
        response_mime_type="application/json",
    )

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=gen_cfg,
            )
            data = _safe_parse_json(response.text or "")

            _STR_FIELDS = [
                "headline", "exec_summary", "kpi_table_md", "anomaly",
                "recommended_action", "speaker_notes", "drivers_md",
            ]
            for _f in _STR_FIELDS:
                if _f in data and isinstance(data[_f], list):
                    data[_f] = "\n".join(str(item) for item in data[_f])

            return NarrativeResult(
                model=model_name,
                role=role_config.get("title", "General"),
                tier=_tier(role_config.get("title", "")),
                **data,
            )
        except ServerError as exc:
            if attempt < max_attempts:
                wait = 3 * attempt  # 3s then 6s — fast fail instead of 15s/30s
                log.warning(
                    "Gemini 503 (attempt %d/%d) — retrying in %d s: %s",
                    attempt, max_attempts, wait, exc,
                )
                _time.sleep(wait)
            else:
                raise


# --------------------------------------------------------------------------- #
# Intelligence helpers — momentum + compound signals
# --------------------------------------------------------------------------- #

def _analyze_momentum(trend_series: list) -> str:
    """
    Read the last 30 daily values and return a one-word momentum label.
    Uses the last 7 points for recent direction and the prior 7 for acceleration.
    """
    if not trend_series or len(trend_series) < 7:
        return ""
    recent  = trend_series[-7:]
    prior   = trend_series[-14:-7] if len(trend_series) >= 14 else trend_series[:7]
    r_avg   = sum(recent) / len(recent)
    p_avg   = sum(prior)  / len(prior)
    r_slope = recent[-1] - recent[0]   # direction of last 7 days
    p_slope = prior[-1]  - prior[0]    # direction of prior 7 days

    if r_avg > p_avg * 1.03 and r_slope > 0 and p_slope > 0:
        return "accelerating"
    if r_avg < p_avg * 0.97 and r_slope < 0 and p_slope < 0:
        return "decelerating"
    if p_slope < 0 and r_slope > 0 and r_avg > prior[-1]:
        return "recovering"
    if abs(r_avg - p_avg) / (p_avg or 1) < 0.03:
        return "plateauing"
    if r_slope > 0:
        return "gaining"
    return "softening"


def _compound_signals(kpis: dict) -> list[str]:
    """
    Detect compound KPI signals that indicate a strategic tension.
    Returns a list of plain-English signal strings.
    """
    signals = []
    sales_dod  = (kpis.get("Sales",  {}).get("dod_pct") or 0)
    profit_dod = (kpis.get("Profit", {}).get("dod_pct") or 0)
    orders_dod = (kpis.get("Orders", {}).get("dod_pct") or 0)
    aov_dod    = (kpis.get("AOV",    {}).get("dod_pct") or 0)
    margin_dod = (kpis.get("Margin%",{}).get("dod_pct") or 0)

    THRESHOLD = 0.01   # 1% minimum to call a signal

    if sales_dod > THRESHOLD and margin_dod < -THRESHOLD:
        signals.append("volume growth at the cost of margin — discount dependency signal")
    if orders_dod > THRESHOLD and aov_dod < -THRESHOLD:
        signals.append("order count rising but basket size shrinking — mix or discount pressure")
    if sales_dod > THRESHOLD and orders_dod < -THRESHOLD:
        signals.append("fewer deals but bigger tickets — high-value mix shift")
    if profit_dod < -THRESHOLD and sales_dod > THRESHOLD:
        signals.append("revenue expanding while profit contracts — cost pressure widening")
    if sales_dod > THRESHOLD and profit_dod > THRESHOLD and orders_dod > THRESHOLD:
        signals.append("broad-based momentum — Sales, Profit, and Orders all advancing")
    return signals


# --------------------------------------------------------------------------- #
# Provider: Stub — role-tier-aware, deterministic
# --------------------------------------------------------------------------- #

def _generate_stub(payload: dict, cfg: dict, role_config: dict) -> NarrativeResult:
    ref           = payload.get("reference_date") or str(_date_type.today())
    kpis          = payload.get("kpis") or {}
    anomalies     = payload.get("anomalies", [])

    primary_kpi_name = role_config.get("primary_kpi", "Sales")
    role_title       = role_config.get("title", "Leadership")
    role_kpi_list    = role_config.get("kpis", ["Sales", "Profit", "Orders", "Margin%"])
    # Prefer explicit tier from roles.yaml; infer from title as fallback
    tier             = role_config.get("tier") or _tier(role_title)
    vocab            = _TIER_VOCAB[tier]

    def _get_margin(kpis: dict) -> dict:
        """Return the margin KPI dict regardless of exact name (handles 'Margin%' vs 'Margin %')."""
        for key in ("Margin%", "Margin %", "Profit Margin %", "Gross Margin %"):
            if key in kpis:
                return kpis[key]
        return {}

    # Prefer role's primary KPI; fall back to Sales
    primary = kpis.get(primary_kpi_name) or kpis.get("Sales", {})
    sales   = kpis.get("Sales",   {})
    profit  = kpis.get("Profit",  {})
    orders  = kpis.get("Orders",  {})

    primary_yoy = primary.get("yoy_pct") or 0
    primary_dod = primary.get("dod_pct") or 0
    primary_mom = primary.get("mom_pct") or 0

    driver_focus  = role_config.get("driver_focus", ["Category", "Region"])
    trend_series  = payload.get("trend_series", [])
    momentum      = _analyze_momentum(trend_series)
    comp_signals  = _compound_signals(kpis)
    top_signal    = comp_signals[0] if comp_signals else None

    all_drivers  = (
        payload.get("drivers", {}).get(primary_kpi_name)
        or payload.get("drivers", {}).get("Sales", [])
    )
    drivers_sorted = sorted(all_drivers, key=lambda d: abs(d.get("delta", 0)), reverse=True)
    top_driver = next((d for d in drivers_sorted if d.get("delta", 0) != 0), None)
    top_lift   = next((d for d in drivers_sorted if d.get("delta", 0) > 0), None)
    top_drag   = next((d for d in drivers_sorted if d.get("delta", 0) < 0), None)

    # ── Headline ─────────────────────────────────────────────────────────── #
    def _cap(v):
        return max(-2.0, min(2.0, v)) if v else 0

    candidates = {
        f"{primary_kpi_name} YoY": _cap(primary_yoy),
        f"{primary_kpi_name} MoM": _cap(primary_mom),
        f"{primary_kpi_name} DoD": _cap(primary_dod),
    }
    lead_label, lead_value = max(candidates.items(), key=lambda kv: abs(kv[1]))
    period = lead_label.split()[-1]  # "YoY", "MoM", "DoD"

    # Secondary KPI — the LAST role KPI that differs from primary and exists in data.
    # Using the last (rather than first) unique KPI ensures leadership roles with a
    # shared #2 KPI (e.g. VP & Director both have Profit second) still diverge.
    secondary_kpi_name = next(
        (k for k in reversed(role_kpi_list) if k != primary_kpi_name and k in kpis), None
    )
    secondary = kpis.get(secondary_kpi_name, {}) if secondary_kpi_name else {}

    # Badge short prefix — used in headlines where two roles share identical KPI configs
    # e.g. COO badge "COO  ·  OPERATIONS BRIEFING" → "COO"
    badge_prefix = role_config.get("badge", role_title).split("·")[0].strip()

    # Tier-adapted headline framing
    if tier == "c_suite":
        _margin_kpi = _get_margin(kpis)
        if lead_value > 0.05:
            headline = (
                f"{primary.get('value_fmt','—')} {primary_kpi_name} — margin held at "
                f"{_margin_kpi.get('value_fmt','—')}, "
                f"{period} gap +{lead_value*100:.1f}%"
            )
        else:
            headline = (
                f"{primary_kpi_name} {period} gap at {lead_value*100:+.1f}% — "
                f"margin {_margin_kpi.get('value_fmt','—')}, board alert"
            )
    elif tier == "commercial":
        direction_word = "up" if lead_value >= 0 else "down"
        headline = (
            f"{primary.get('value_fmt','—')} {primary_kpi_name} — "
            f"{direction_word} {abs(lead_value)*100:.1f}% {period}, "
            f"{top_driver['dimension'] + ' → ' + top_driver['member'] if top_driver else 'mixed drivers'}"
        )
    elif tier == "operations":
        headline = (
            f"{badge_prefix} · {primary.get('value_fmt','—')} {primary_kpi_name} "
            f"{vocab['momentum_verb'] if primary_yoy>=0 else vocab['drag_verb']} — "
            f"{period} {lead_value*100:+.1f}%"
        )
    elif tier == "management":
        sec_clause = (
            f" · {secondary.get('value_fmt','—')} {secondary_kpi_name}" if secondary_kpi_name else ""
        )
        # role_title suffix guarantees uniqueness when two management roles share identical KPI lists
        headline = (
            f"{primary.get('value_fmt','—')} {primary_kpi_name}{sec_clause} — "
            f"{lead_value*100:+.1f}% {period} — "
            f"{role_title}: {'action needed' if lead_value < 0 else 'keep pushing'}"
        )
    else:  # leadership
        # Include secondary KPI to differentiate VP / Director / Associate Director
        sec_clause = (
            f" · {secondary.get('value_fmt','—')} {secondary_kpi_name}" if secondary_kpi_name else ""
        )
        headline = (
            f"{primary.get('value_fmt','—')} {primary_kpi_name}{sec_clause} — "
            f"{lead_value*100:+.1f}% {period}, "
            f"{'momentum intact' if lead_value >= 0 else 'recovery plan needed'}"
        )

    # ── Exec Summary — three-act: Fact → Cause → Implication ────────────── #
    # Build target achievement clause first — must be defined before act1 uses it
    targets = payload.get("targets", {})
    _t_primary = targets.get(primary_kpi_name, {})
    _p_ach = _t_primary.get("achievement_pct")
    _p_tgt = _t_primary.get("target_fmt", "")
    if _p_ach is not None:
        _target_clause = (
            f" Target: {_p_tgt} — achieved {_p_ach:.0f}%."
            if _p_tgt else f" Target achievement: {_p_ach:.0f}%."
        )
    else:
        _target_clause = ""

    # ACT 1: The fact (with optional target achievement)
    momentum_clause = f", {momentum}" if momentum else ""
    act1 = (
        f"{primary_kpi_name} closed at {primary.get('value_fmt','—')} "
        f"({_fmt_delta(primary.get('yoy_pct'))} YoY, "
        f"{_fmt_delta(primary.get('mom_pct'))} MoM{momentum_clause}).{_target_clause}"
    )

    # ACT 2: The cause
    if top_driver:
        mv_verb = vocab["momentum_verb"] if top_driver["delta"] > 0 else vocab["drag_verb"]
        act2 = (
            f"{top_driver['dimension']} → {top_driver['member']} "
            f"{mv_verb} {primary_kpi_name} by ${abs(top_driver['delta']):,.0f} "
            f"({top_driver.get('delta_pct', 0)*100:+.1f}% vs prior)."
            if top_driver.get("delta_pct") is not None else
            f"{top_driver['dimension']} → {top_driver['member']} "
            f"{mv_verb} {primary_kpi_name} by ${abs(top_driver['delta']):,.0f} vs prior."
        )
    elif top_signal:
        act2 = f"No single driver dominates — broad signal: {top_signal}."
    else:
        act2 = "The shift is broad-based across dimensions — no single member dominates."

    # ACT 3: The implication for this role
    if tier == "c_suite":
        margin_val = profit.get("value", 0) / sales.get("value", 1) if sales.get("value") else 0
        _margin_fmt = _get_margin(kpis).get('value_fmt', '—')
        if top_signal:
            act3 = f"Watch-out: {top_signal} — review capital allocation before the next board touchpoint."
        elif margin_val > 0.15:
            act3 = f"Margin at {_margin_fmt} — growth quality intact, no immediate reforecast needed."
        else:
            act3 = f"Margin at {_margin_fmt} — below threshold, worth a capital-allocation call this week."
    elif tier == "commercial":
        if top_signal:
            act3 = f"Commercial signal: {top_signal} — act on mix or pricing before end of day."
        elif primary_dod >= 0:
            act3 = f"AOV at {kpis.get('AOV',{}).get('value_fmt','—')} with {orders.get('value_fmt','—')} orders — conversion holding, sustain the pace."
        else:
            act3 = f"AOV at {kpis.get('AOV',{}).get('value_fmt','—')} — conversion softening, push pipeline now before the week closes."
    elif tier == "operations":
        if primary_dod < 0:
            act3 = f"Throughput at {orders.get('value_fmt','—')} orders — dipping, check the fulfilment queue for bottlenecks before the next cycle."
        else:
            act3 = f"{orders.get('value_fmt','—')} orders processed — throughput stable, no SLA escalation required today."
    elif tier == "management":
        focus_dim = driver_focus[0] if driver_focus else "the top dimension"
        if primary_dod < 0:
            act3 = f"Team priority today: address the drag in {focus_dim} — assign owner and set a same-day check-in."
        else:
            act3 = f"Team priority today: lock in the lift in {focus_dim} and flag it upward as a repeat opportunity."
    else:  # leadership
        if top_signal:
            act3 = f"Cross-functional watch-out: {top_signal} — name which function owns the response."
        elif primary_yoy < -0.05:
            act3 = f"Trend at risk — flag to C-Suite if the {period} gap doesn't close by end of week."
        else:
            act3 = f"No immediate escalation required — monitor {primary_kpi_name} DoD for two more sessions before calling it a trend."

    exec_summary = f"{act1} {act2} {act3}"

    # ── KPI table — role-filtered + optional achievement % ─────────────── #
    targets = payload.get("targets", {})

    def row(name, k):
        t = targets.get(name, {})
        ach = t.get("achievement_pct")
        ach_col = f" {ach:.0f}% of target |" if ach is not None else " — |"
        return (
            f"| {name} | {k.get('value_fmt','—')} | "
            f"{_fmt_delta(k.get('dod_pct'))} | "
            f"{_fmt_delta(k.get('wow_pct'))} | "
            f"{_fmt_delta(k.get('mom_pct'))} | "
            f"{_fmt_delta(k.get('yoy_pct'))} |"
            f"{ach_col}"
        )
    has_targets = bool(targets)
    hdr_suffix  = " vs Target |" if has_targets else ""
    sep_suffix  = "---|" if has_targets else ""
    kpi_rows = "\n".join(row(n, kpis[n]) for n in role_kpi_list if n in kpis)
    kpi_table_md = (
        f"| Metric | Value | DoD | WoW | MoM | YoY |{hdr_suffix}\n"
        f"|---|---|---|---|---|---|{sep_suffix}\n" + kpi_rows
    )

    # ── Driver bullets — tier-adapted verbs + compound signal ───────────── #
    driver_bullets = []
    for d in drivers_sorted[:5]:
        if d.get("delta", 0) == 0:
            continue
        verb = vocab["momentum_verb"] if d["delta"] > 0 else vocab["drag_verb"]
        pct  = d.get("delta_pct")
        pct_txt = f" ({pct*100:+.1f}% vs prior)" if pct is not None else ""
        driver_bullets.append(
            f"- **{d['dimension']} → {d['member']}** {verb} "
            f"{primary_kpi_name} by ${abs(d['delta']):,.0f}{pct_txt}."
        )
    if top_signal:
        driver_bullets.append(f"- **⚡ Compound signal:** {top_signal}.")
    drivers_md = (
        "\n".join(driver_bullets)
        or "- Driver breakdown unavailable for this date range."
    )

    # ── Anomaly ──────────────────────────────────────────────────────────── #
    if anomalies:
        a = max(anomalies, key=lambda x: abs(x["zscore"]))
        z_verb = "spiked" if a["direction"] == "spike" else "dropped"
        # Tier-specific anomaly framing
        if tier == "c_suite":
            tail = f"This {vocab['urgency_phrase']} — review before the next board touchpoint."
        elif tier == "commercial":
            tail = f"This {vocab['urgency_phrase']} — pull the segment detail now."
        elif tier == "operations":
            tail = f"This {vocab['urgency_phrase']} — check the fulfilment log."
        elif tier == "management":
            tail = f"This {vocab['urgency_phrase']} — assign to someone on the team today."
        else:
            tail = f"This {vocab['urgency_phrase']} — escalate if unresolved by EOD."

        anomaly = (
            f"{a['metric']} {z_verb} to {a['value']:,.0f} against an "
            f"expected {a['expected']:,.0f} (z-score {a['zscore']}). {tail}"
        )
    else:
        anomaly = "No anomalies on the 90-day window. All metrics inside normal variance."

    # ── Recommended action — verb · owner · deadline · success metric ───── #
    if tier == "c_suite":
        if top_drag:
            recommended_action = (
                f"Align on {top_drag['dimension']} → {top_drag['member']} recovery strategy. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: recover ~${abs(top_drag['delta'])*0.5:,.0f} "
                f"in {primary_kpi_name} within 30 days."
            )
        elif top_lift:
            recommended_action = (
                f"Protect {top_lift['dimension']} → {top_lift['member']} momentum with committed budget. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: sustain ${top_lift['delta']:,.0f} lift into next month."
            )
        else:
            recommended_action = (
                f"Hold position — no material delta requires C-Suite action today. "
                f"Owner: Analytics. Deadline: weekly leadership sync. "
                f"Success metric: {primary_kpi_name} stays within ±5% of current run rate."
            )

    elif tier == "commercial":
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} {top_drag['dimension']} → {top_drag['member']} — "
                f"this {vocab['urgency_phrase']}. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: recover ${abs(top_drag['delta'])*0.6:,.0f} in revenue this week."
            )
        elif top_lift:
            recommended_action = (
                f"{vocab['action_verb']} harder on {top_lift['dimension']} → {top_lift['member']}. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: add ${top_lift['delta']*0.3:,.0f} in additional revenue "
                f"by sustaining this week's pace."
            )
        else:
            recommended_action = (
                f"Run pipeline review across all regions. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: identify at least one territory gap with a clear close path."
            )

    elif tier == "operations":
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} bottleneck at {top_drag['dimension']} → {top_drag['member']}. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: return ~{max(1, int(abs(top_drag['delta'])/50)):,} orders "
                f"to on-time fulfilment within 48 hours."
            )
        else:
            recommended_action = (
                f"Audit Ship Mode split and flag any nodes below SLA. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: fulfilment rate holds above prior 7-day baseline."
            )

    elif tier == "management":
        task1 = (
            f"(1) Pull {top_drag['dimension']} → {top_drag['member']} detail and assign a fix owner today."
            if top_drag else
            "(1) Review yesterday's order log and flag any exceptions to the line manager."
        )
        task2 = (
            f"(2) Flag {top_lift['dimension']} → {top_lift['member']} lift to manager as a repeat opportunity."
            if top_lift else
            "(2) Check pending shipments for delays and confirm ETAs before close of business."
        )
        recommended_action = (
            f"{task1} {task2} "
            f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
            f"Success metric: both tasks closed with a named owner by end of day."
        )

    else:  # leadership
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} cross-functional fix for "
                f"{top_drag['dimension']} → {top_drag['member']}. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: recover ~${abs(top_drag['delta'])*0.5:,.0f} "
                f"in {primary_kpi_name} and close the escalation risk within 5 business days."
            )
        elif top_lift:
            recommended_action = (
                f"{vocab['action_verb']} resources behind "
                f"{top_lift['dimension']} → {top_lift['member']}. "
                f"Owner: {vocab['call_owner']}. Deadline: {vocab['time_box']}. "
                f"Success metric: lock in the ${top_lift['delta']:,.0f} lift before momentum fades."
            )
        else:
            recommended_action = (
                f"Hold position — no single dimension warrants escalation. "
                f"Owner: Analytics. Deadline: weekly sync. "
                f"Success metric: {primary_kpi_name} trend stays positive for 3 consecutive sessions."
            )

    # ── Speaker notes — anticipate the first question + bright spot ─────── #
    bright_line = (
        f"{vocab['bright_prefix']} {top_lift['dimension']} → {top_lift['member']} "
        f"added ${top_lift['delta']:,.0f} — name it before they ask."
        if top_lift
        else "No single bright spot today — acknowledge the headwinds directly and pivot to the action."
    )

    signal_note = (
        f" Watch-out: {top_signal}."
        if top_signal else ""
    )

    if tier == "c_suite":
        _margin_fmt_sn = _get_margin(kpis).get('value_fmt', '—')
        first_q = (
            "They will ask whether this growth is margin-accretive — answer: "
            f"margin sits at {_margin_fmt_sn}, "
            f"{'within acceptable range' if (profit.get('value',0) / sales.get('value',1) if sales.get('value') else 0) > 0.12 else 'below threshold — have the capital-allocation slide ready'}."
        )
        speaker_notes = f"{first_q}{signal_note} {bright_line}"

    elif tier == "commercial":
        first_q = (
            "They will ask which territory or rep owns the number — have the region-level breakdown "
            "ready and name the top performer and the biggest gap in the same breath."
        )
        speaker_notes = (
            f"{first_q}{signal_note} {bright_line} "
            f"If asked about forecast, cite the {'positive' if primary_dod >= 0 else 'negative'} "
            f"DoD trend and AOV at {kpis.get('AOV',{}).get('value_fmt','—')}, not gut feel."
        )

    elif tier == "operations":
        first_q = (
            "They will ask about SLA impact — have the Ship Mode split and on-time rate ready "
            "before the question lands; lead with data, not with 'we're looking into it'."
        )
        speaker_notes = (
            f"{first_q}{signal_note} {bright_line} "
            f"If pressed on root cause, lead with the data-quality check result before citing demand shift."
        )

    elif tier == "management":
        first_q = (
            f"The team will ask 'what do we do first' — answer with exactly two tasks: "
            f"{'fix ' + top_drag['dimension'] + ' → ' + top_drag['member'] if top_drag else 'review the order exception log'} "
            f"and {'push ' + top_lift['dimension'] + ' → ' + top_lift['member'] if top_lift else 'confirm pending shipment ETAs'}."
        )
        speaker_notes = f"{first_q}{signal_note} {bright_line}"

    else:  # leadership
        escalation_risk = (
            "flag this to C-Suite if the gap doesn't close in two sessions"
            if primary_yoy < -0.05 else
            "no escalation required unless the DoD trend reverses for three consecutive days"
        )
        first_q = (
            f"They will ask which function owns the fix — name {vocab['call_owner']} immediately "
            f"and confirm the timeline; {escalation_risk}."
        )
        speaker_notes = f"{first_q}{signal_note} {bright_line}"

    return NarrativeResult(
        model              = "stub-deterministic",
        role               = role_title,
        tier               = tier,
        headline           = headline,
        exec_summary       = exec_summary,
        kpi_table_md       = kpi_table_md,
        anomaly            = anomaly,
        recommended_action = recommended_action,
        speaker_notes      = speaker_notes,
        drivers_md         = drivers_md,
    )


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #

def generate_narrative(
    payload: dict,
    config: dict,
    role_config: Optional[dict] = None,
) -> NarrativeResult:
    """
    Generate a narrative for the given payload.

    role_config: a single role dict from roles.yaml (e.g. roles["CEO"]).
                 If None, falls back to a generic executive profile.
    """
    if role_config is None:
        role_config = {
            "title":       "Leadership",
            "badge":       "EXECUTIVE BRIEFING",
            "primary_kpi": "Sales",
            "kpis":        ["Sales", "Profit", "Orders", "Margin%"],
            "accent_color":"#F59E0B",
            "driver_focus":["Category", "Region"],
            "tone":        "Executive and strategic. Big-picture growth and profitability.",
        }

    llm_cfg  = config.get("llm", {})
    provider = (llm_cfg.get("provider") or "stub").lower()
    log.info("Generating narrative — role=%s tier=%s provider=%s",
             role_config.get("title"), _tier(role_config.get("title", "")), provider)

    if provider == "gemini":
        try:
            return _generate_gemini(payload, llm_cfg, role_config)
        except Exception as exc:
            log.exception("Gemini call failed — falling back to stub: %s", exc)
            return _generate_stub(payload, llm_cfg, role_config)

    return _generate_stub(payload, llm_cfg, role_config)
