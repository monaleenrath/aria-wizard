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
Your audience has 90 seconds. They are smart, impatient, and allergic to
corporate filler.

ROLE CONTEXT:
{role_tone}

AUDIENCE TIER: {tier_label}
{tier_guidance}

YOUR VOICE (always):
  • Confident, conversational, never preachy.
  • Lead every section with a number, a name, or a verb — never with "Overall".
  • Banned phrases: "significantly", "leverage", "going forward",
    "robust", "drive synergies", "exciting opportunity", "deep dive".
  • Quantify everything. "+11.4%" not "strong growth".
  • Name names. "Furniture in the West" not "underperforming products".
  • One adjective per sentence, max.

KPI FOCUS for this role: {role_kpis}
Lead the narrative with these metrics. De-emphasise others.

You will receive a JSON payload with KPIs (value, DoD/WoW/MoM/YoY),
flagged z-score anomalies, and ranked drivers across dimensions.

Return a JSON object with EXACTLY these keys:

{{
  "headline":          "≤14 words. Lead with the number that matters most to {role_title}.",
  "exec_summary":      "2–3 sentences. What happened, what caused it, what it means for {role_title}.",
  "kpi_table_md":      "Markdown table | Metric | Value | DoD | WoW | MoM | YoY |",
  "anomaly":           "One sharp paragraph naming the most interesting outlier OR 'No anomalies on the 90-day window.'",
  "recommended_action":"One specific, owned, time-boxed action relevant to {role_title}. Format: '<Verb> <object>. Owner: <function>. By <date>. Expected impact: <number or qualitative>.'",
  "speaker_notes":     "2–3 sentences anticipating what {role_title} will ask, plus one bright spot.",
  "drivers_md":        "3–5 markdown bullets explaining WHY. Each: '**<Dimension> → <Member>** <verb> {primary_kpi} by $X (Y%).'"
}}

Return ONLY the JSON, no preamble, no markdown fences."""

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


def _build_system_prompt(role_config: dict) -> str:
    tier = _tier(role_config.get("title", ""))
    return _BASE_SYSTEM.format(
        role_title   = role_config.get("title", "Leadership"),
        role_tone    = role_config.get("tone", "Executive and strategic.").strip(),
        role_kpis    = ", ".join(role_config.get("kpis", ["Sales", "Profit", "Orders", "Margin%"])),
        primary_kpi  = role_config.get("primary_kpi", "Sales"),
        tier_label   = _TIER_LABELS.get(tier, "Leadership"),
        tier_guidance= _TIER_GUIDANCE.get(tier, _TIER_GUIDANCE["leadership"]),
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

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key (no credit card) at "
            "https://aistudio.google.com/app/apikey and add it to your "
            ".env or GitHub Secrets."
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
                wait = 15 * attempt
                log.warning(
                    "Gemini 503 (attempt %d/%d) — retrying in %d s: %s",
                    attempt, max_attempts, wait, exc,
                )
                _time.sleep(wait)
            else:
                raise


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

    # Prefer role's primary KPI; fall back to Sales
    primary = kpis.get(primary_kpi_name) or kpis.get("Sales", {})
    sales   = kpis.get("Sales",   {})
    profit  = kpis.get("Profit",  {})
    orders  = kpis.get("Orders",  {})

    primary_yoy = primary.get("yoy_pct") or 0
    primary_dod = primary.get("dod_pct") or 0
    primary_mom = primary.get("mom_pct") or 0

    driver_focus = role_config.get("driver_focus", ["Category", "Region"])
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
        if lead_value > 0.05:
            headline = (
                f"{primary.get('value_fmt','—')} {primary_kpi_name} — margin held at "
                f"{kpis.get('Margin%',{}).get('value_fmt','—')}, "
                f"{period} gap +{lead_value*100:.1f}%"
            )
        else:
            headline = (
                f"{primary_kpi_name} {period} gap at {lead_value*100:+.1f}% — "
                f"margin {kpis.get('Margin%',{}).get('value_fmt','—')}, board alert"
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
            f"{badge_prefix} · {orders.get('value_fmt','—')} orders "
            f"{vocab['momentum_verb'] if primary_yoy>=0 else vocab['drag_verb']} — "
            f"{primary_kpi_name} {period} {lead_value*100:+.1f}%"
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

    # ── Exec Summary ─────────────────────────────────────────────────────── #
    # Why sentence — driver-specific
    if top_driver:
        mv_verb = vocab["momentum_verb"] if top_driver["delta"] > 0 else vocab["drag_verb"]
        why = (
            f"{top_driver['dimension']} → {top_driver['member']} "
            f"{mv_verb} {primary_kpi_name} by ${abs(top_driver['delta']):,.0f} "
            f"versus the prior year."
        )
    else:
        why = "The shift is broad-based — no single dimension dominates the move."

    # Tier-specific closing line
    if tier == "c_suite":
        closing = (
            f"Margin at {kpis.get('Margin%',{}).get('value_fmt','—')} — "
            f"{'within target range' if (profit.get('value',0)/sales.get('value',1) if sales.get('value') else 0) > 0.1 else 'below target, worth a capital review'}."
        )
    elif tier == "commercial":
        closing = (
            f"AOV at {kpis.get('AOV',{}).get('value_fmt','—')} with "
            f"{orders.get('value_fmt','—')} orders — "
            f"{'conversion holding' if primary_dod >= 0 else 'conversion softening, push pipeline'}."
        )
    elif tier == "operations":
        closing = (
            f"{orders.get('value_fmt','—')} orders processed — "
            f"{'throughput stable' if primary_dod >= 0 else 'throughput dipping, check fulfilment queue'}."
        )
    elif tier == "management":
        closing = (
            f"Team priority: {'lock in the lift' if primary_dod >= 0 else 'address the drag'} "
            f"in {driver_focus[0] if driver_focus else 'the top dimension'} today."
        )
    else:
        closing = (
            f"Margin at {kpis.get('Margin%',{}).get('value_fmt','—')} — "
            f"{'no immediate escalation required' if primary_yoy >= -0.05 else 'flag to C-Suite if trend continues'}."
        )

    exec_summary = (
        f"{primary_kpi_name} closed at {primary.get('value_fmt','—')} "
        f"({_fmt_delta(primary.get('yoy_pct'))} YoY, "
        f"{_fmt_delta(primary.get('mom_pct'))} MoM). "
        f"{why} {closing}"
    )

    # ── KPI table — role-filtered ────────────────────────────────────────── #
    def row(name, k):
        return (
            f"| {name} | {k.get('value_fmt','—')} | "
            f"{_fmt_delta(k.get('dod_pct'))} | "
            f"{_fmt_delta(k.get('wow_pct'))} | "
            f"{_fmt_delta(k.get('mom_pct'))} | "
            f"{_fmt_delta(k.get('yoy_pct'))} |"
        )
    kpi_rows = "\n".join(row(n, kpis[n]) for n in role_kpi_list if n in kpis)
    kpi_table_md = (
        "| Metric | Value | DoD | WoW | MoM | YoY |\n"
        "|---|---|---|---|---|---|\n" + kpi_rows
    )

    # ── Driver bullets — tier-adapted verbs ─────────────────────────────── #
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

    # ── Recommended action — tier-specific ──────────────────────────────── #
    if tier == "c_suite":
        if top_drag:
            recommended_action = (
                f"Align on {top_drag['dimension']} → {top_drag['member']} recovery strategy. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"{vocab['impact_prefix']} ~${abs(top_drag['delta'])*0.5:,.0f} "
                f"in the trailing 30-day window."
            )
        else:
            recommended_action = (
                f"Sustain {top_lift['dimension']} → {top_lift['member']} momentum. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"Lock in the ${top_lift['delta']:,.0f} lift with committed budget line."
            ) if top_lift else (
                "Hold position. No material delta requires C-Suite action today. "
                f"Owner: Analytics. Review at weekly leadership sync."
            )

    elif tier == "commercial":
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} {top_drag['dimension']} → {top_drag['member']} — "
                f"this {vocab['urgency_phrase']}. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"{vocab['impact_prefix']} ${abs(top_drag['delta'])*0.6:,.0f} this week."
            )
        elif top_lift:
            recommended_action = (
                f"{vocab['action_verb']} harder on {top_lift['dimension']} → {top_lift['member']}. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"{vocab['impact_prefix']} ${top_lift['delta']*0.3:,.0f} additional "
                f"if we sustain this week's pace."
            )
        else:
            recommended_action = (
                f"Run pipeline review across all regions. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}."
            )

    elif tier == "operations":
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} bottleneck at {top_drag['dimension']} → {top_drag['member']}. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"{vocab['impact_prefix']} {int(abs(top_drag['delta'])/50):,} orders "
                f"back on schedule."
            )
        else:
            recommended_action = (
                f"Audit Ship Mode split for next 48 hours. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"Goal: keep fulfilment rate above baseline."
            )

    elif tier == "management":
        task1 = (
            f"(1) Pull {top_drag['dimension']} → {top_drag['member']} detail — "
            f"assign fix to a team member today."
            if top_drag else
            "(1) Review yesterday's order log for any exceptions."
        )
        task2 = (
            f"(2) Amplify {top_lift['dimension']} → {top_lift['member']} — "
            f"flag to manager as a repeat opportunity."
            if top_lift else
            "(2) Check pending shipments for delays."
        )
        recommended_action = (
            f"{task1} {task2} "
            f"Owner: {vocab['call_owner']}. {vocab['time_box']}."
        )

    else:  # leadership
        if top_drag:
            recommended_action = (
                f"{vocab['action_verb']} cross-functional fix for "
                f"{top_drag['dimension']} → {top_drag['member']}. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"{vocab['impact_prefix']} ~${abs(top_drag['delta'])*0.5:,.0f}."
            )
        elif top_lift:
            recommended_action = (
                f"{vocab['action_verb']} resources behind "
                f"{top_lift['dimension']} → {top_lift['member']}. "
                f"Owner: {vocab['call_owner']}. {vocab['time_box']}. "
                f"Lock in the ${top_lift['delta']:,.0f} lift before momentum fades."
            )
        else:
            recommended_action = (
                "Hold position. No single dimension warrants escalation. "
                f"Owner: Analytics. Review at weekly sync."
            )

    # ── Speaker notes — tier-specific prep ──────────────────────────────── #
    bright_line = (
        f"{vocab['bright_prefix']} {top_lift['dimension']} → {top_lift['member']} "
        f"added ${top_lift['delta']:,.0f} — name it before they ask."
        if top_lift
        else "No standout bright spot today — acknowledge the headwinds directly."
    )

    if tier == "c_suite":
        speaker_notes = (
            f"{vocab['speaker_open']} — name the tension before they do. "
            f"{bright_line} "
            f"If pressed on the anomaly, anchor on the z-score and confirm the data-quality check ran."
        )
    elif tier == "commercial":
        speaker_notes = (
            f"{vocab['speaker_open']} — have the territory-level breakdown ready. "
            f"{bright_line} "
            f"If asked about forecast, cite the 30-day trend and AOV movement, not gut feel."
        )
    elif tier == "operations":
        speaker_notes = (
            f"{vocab['speaker_open']} — have the SLA report and Ship Mode split ready. "
            f"{bright_line} "
            f"If asked about the anomaly, lead with the data-quality check before citing demand shift."
        )
    elif tier == "management":
        speaker_notes = (
            f"{vocab['speaker_open']} — keep it to two slides and three actions. "
            f"{bright_line} "
            f"If the team asks 'why', point them to the dimension breakdown above."
        )
    else:
        speaker_notes = (
            f"{vocab['speaker_open']} — have the function-level owner name ready. "
            f"{bright_line} "
            f"If escalation is raised, present the recommended action and timeline immediately."
        )

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
