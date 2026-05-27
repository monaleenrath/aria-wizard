"""
svg_generator.py
----------------
Generates the Dark Editorial SVG card dynamically from narrative + payload.

Three-act layout (800 x 480):
  ACT I   · WHERE WE ARE   — headline KPI, MoM/YoY, bullets, sparkline
  ACT II  · WHAT MOVED IT  — proportional driver bar chart
  ACT III · THE CALL       — action box + owner/impact

Role-aware: accent colour, badge, and primary KPI hero number all adapt
to the role_config passed in from roles.yaml.

Returns an SVG string ready to be rasterised via cairosvg.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import Optional


# ── Text helpers ────────────────────────────────────────────────────────────

def _e(text: str) -> str:
    """XML-escape a value for SVG text content."""
    return html.escape(str(text))

def _strip_md(text: str) -> str:
    """Remove common markdown markers."""
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*•]\s+", "", text, flags=re.MULTILINE)
    return text.strip()

def _wrap(text: str, max_chars: int) -> list[str]:
    """Word-wrap text into lines of at most max_chars."""
    words = text.split()
    lines, current = [], ""
    for w in words:
        if len(current) + len(w) + 1 <= max_chars:
            current = (current + " " + w).strip()
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines

def _fmt_date(ref_date: str) -> str:
    try:
        d = date.fromisoformat(ref_date)
        return d.strftime("%a  %b %d  %Y").upper()
    except Exception:
        return ref_date.upper()

def _fmt_currency(val: float) -> str:
    av = abs(val)
    if av >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if av >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:,.0f}"

def _fmt_pct(val: Optional[float], accent: str) -> tuple[str, str]:
    """Return (formatted string, colour hex)."""
    if val is None:
        return "—", "#94A3B8"
    sign  = "▲" if val >= 0 else "▼"
    color = "#34D399" if val >= 0 else "#F87171"
    return f"{sign} {abs(val*100):.1f}%", color

def _sparkline_points(values: list[float],
                      x0: int, y0: int, w: int, h: int) -> str:
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    r = mx - mn or 1
    pts = []
    for i, v in enumerate(values):
        x = x0 + int(i * w / (len(values) - 1))
        y = y0 + h - int((v - mn) / r * h)
        pts.append(f"{x},{y}")
    return " ".join(pts)


# ── Default role config (used if none passed) ────────────────────────────────

_DEFAULT_ROLE = {
    "title": "Leadership",
    "badge": "ARIA  ·  EXECUTIVE BRIEFING",
    "primary_kpi": "Sales",
    "kpis": ["Sales", "Profit", "Orders", "Margin%"],
    "accent_color": "#F59E0B",
    "driver_focus": ["Category", "Region"],
}


# ── SVG builder ─────────────────────────────────────────────────────────────

def generate_svg(narrative, payload: dict, _config: dict,
                 role_config: Optional[dict] = None) -> str:

    role = role_config or _DEFAULT_ROLE
    accent   = role.get("accent_color", "#F59E0B")
    badge    = role.get("badge", "ARIA  ·  EXECUTIVE BRIEFING")
    prim_kpi = role.get("primary_kpi", "Sales")

    # Derive a slightly lighter shade for sparkline dot and impact text
    # (keeps card visually coherent without needing a full colour library)
    accent_light = accent  # reuse accent; works well enough for all 4 colours

    p: list[str] = []
    def t(s: str):
        p.append(s)

    # ── Data extraction ───────────────────────────────────────────────────── #
    ref_date = payload.get("reference_date", "")
    kpis     = payload.get("kpis", {})

    # Primary KPI — hero number (role-specific)
    primary_kpi_data = kpis.get(prim_kpi) or kpis.get("Sales", {})
    primary_val      = primary_kpi_data.get("value_fmt", "$0")
    mom_str, mom_col = _fmt_pct(primary_kpi_data.get("mom_pct"), accent)
    yoy_str, yoy_col = _fmt_pct(primary_kpi_data.get("yoy_pct"), accent)

    margin_fmt = kpis.get("Margin%", {}).get("value_fmt", "—")
    orders_fmt = kpis.get("Orders",  {}).get("value_fmt", "—")
    aov_fmt    = kpis.get("AOV",     {}).get("value_fmt", "—")

    headline_lines = _wrap(_strip_md(narrative.headline), 70)[:2]
    exec_first     = _strip_md(narrative.exec_summary or "").split(".")[0].strip() + "."

    all_drivers = payload.get("drivers", {}).get(prim_kpi) \
               or payload.get("drivers", {}).get("Sales", [])
    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:3]
    drags = sorted([d for d in all_drivers if d.get("delta", 0) < 0],
                   key=lambda d: d["delta"])[:2]

    max_abs = max([abs(d.get("delta", 0)) for d in lifts + drags] or [1])

    def bw(delta: float, max_w: int = 208) -> int:
        return max(4, int(abs(delta) / max_abs * max_w))

    daily_sales: list[float] = payload.get("daily_sales_30d", [])

    action_lines = _wrap(_strip_md(narrative.recommended_action or ""), 30)[:3]
    speaker_raw  = _strip_md(narrative.speaker_notes or "")
    speaker_line = speaker_raw[:145] + ("…" if len(speaker_raw) > 145 else "")

    # Headline Y positions
    hl_y1  = 62
    hl_y2  = hl_y1 + 22
    sub_y  = (hl_y2 if len(headline_lines) > 1 else hl_y1) + 18
    rule_y = sub_y + 8

    # ── SVG open ─────────────────────────────────────────────────────────── #
    t('<svg viewBox="0 0 800 480" xmlns="http://www.w3.org/2000/svg" '
      'font-family="-apple-system, BlinkMacSystemFont, \'Inter\', Arial, sans-serif">')
    t('<rect width="800" height="480" fill="#0B1220"/>')

    # ── Masthead ──────────────────────────────────────────────────────────── #
    # Left: ARIA brand in accent colour
    t(f'<text x="30" y="26" fill="{accent}" font-size="9" letter-spacing="4" font-weight="700">ARIA</text>')
    # Centre: role badge
    t(f'<text x="400" y="26" fill="#CBD5E1" font-size="8" letter-spacing="3" text-anchor="middle">{_e(badge)}</text>')
    # Right: date
    t(f'<text x="770" y="26" fill="#94A3B8" font-size="9" letter-spacing="2" text-anchor="end">{_e(_fmt_date(ref_date))}</text>')
    t('<rect x="30" y="34" width="740" height="0.5" fill="#1F2937"/>')

    # ── Headline ──────────────────────────────────────────────────────────── #
    t(f'<text x="30" y="{hl_y1}" fill="#F8FAFC" font-size="18" font-weight="700" '
      f'font-family="Georgia, serif">{_e(headline_lines[0] if headline_lines else "")}</text>')
    if len(headline_lines) > 1:
        t(f'<text x="30" y="{hl_y2}" fill="#F8FAFC" font-size="18" font-weight="700" '
          f'font-family="Georgia, serif">{_e(headline_lines[1])}</text>')
    t(f'<text x="30" y="{sub_y}" fill="#94A3B8" font-size="11" font-style="italic">{_e(exec_first)}</text>')
    # Accent rule — uses role colour
    t(f'<rect x="30" y="{rule_y}" width="44" height="2" fill="{accent}"/>')

    # ── Column dividers ───────────────────────────────────────────────────── #
    t('<rect x="288" y="106" width="0.5" height="316" fill="#1F2937"/>')
    t('<rect x="546" y="106" width="0.5" height="316" fill="#1F2937"/>')

    # ══════════════════════════════════════════════════════════════════════════
    # ACT I · WHERE WE ARE
    # ══════════════════════════════════════════════════════════════════════════
    t(f'<text x="30" y="120" fill="{accent}" font-size="8" letter-spacing="4" font-weight="700">ACT I  ·  WHERE WE ARE</text>')
    # Hero number — role's primary KPI
    t(f'<text x="30" y="174" fill="#F8FAFC" font-size="46" font-weight="700" font-family="Georgia, serif">{_e(primary_val)}</text>')
    # Small primary KPI label above the number
    t(f'<text x="30" y="136" fill="#64748B" font-size="8" letter-spacing="2">{_e(prim_kpi.upper())}</text>')
    t(f'<text x="30" y="196" fill="{mom_col}" font-size="11" font-weight="600">{_e(mom_str)} MoM   ·   {_e(yoy_str)} YoY</text>')
    t(f'<text x="30" y="222" fill="#CBD5E1" font-size="10" font-style="italic" font-family="Georgia, serif">Margin: {_e(margin_fmt)}</text>')
    t(f'<text x="30" y="238" fill="#CBD5E1" font-size="10" font-style="italic" font-family="Georgia, serif">Orders: {_e(orders_fmt)}   ·   AOV: {_e(aov_fmt)}</text>')

    # Sparkline
    if len(daily_sales) >= 4:
        pts    = _sparkline_points(daily_sales, 30, 298, 240, 48)
        mn, mx = min(daily_sales), max(daily_sales)
        r      = mx - mn or 1
        last_x = 270
        last_y = 298 + 48 - int((daily_sales[-1] - mn) / r * 48)
        t(f'<text x="30" y="290" fill="#64748B" font-size="7" letter-spacing="3" font-weight="600">30-DAY {_e(prim_kpi.upper())} TREND</text>')
        t(f'<polyline points="{pts}" stroke="{accent}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
        t(f'<circle cx="{last_x}" cy="{last_y}" r="3.5" fill="{accent_light}"/>')

    # ══════════════════════════════════════════════════════════════════════════
    # ACT II · WHAT MOVED IT
    # ══════════════════════════════════════════════════════════════════════════
    t(f'<text x="308" y="120" fill="{accent}" font-size="8" letter-spacing="4" font-weight="700">ACT II  ·  WHAT MOVED IT</text>')
    t(f'<text x="308" y="138" fill="#CBD5E1" font-size="10" font-style="italic" font-family="Georgia, serif">Top drivers vs prior year.</text>')

    row_y = 156
    for d in lifts:
        label     = f"{d.get('dimension', '')} → {d.get('member', '')}"[:32]
        delta_fmt = _fmt_currency(abs(d.get("delta", 0)))
        w = bw(d["delta"])
        t(f'<text x="308" y="{row_y}" fill="#F8FAFC" font-size="10" font-weight="600">{_e(label)}</text>')
        t(f'<rect x="308" y="{row_y+4}" width="210" height="13" fill="#1E2D40" rx="3"/>')
        t(f'<rect x="308" y="{row_y+4}" width="{w}" height="13" fill="#34D399" rx="3"/>')
        t(f'<text x="{314+w}" y="{row_y+15}" fill="#34D399" font-size="9" font-weight="700">+{_e(delta_fmt)}</text>')
        row_y += 36

    if drags:
        row_y += 4
    for d in drags:
        label     = f"{d.get('dimension', '')} → {d.get('member', '')}"[:32]
        delta_fmt = _fmt_currency(abs(d.get("delta", 0)))
        w = bw(d["delta"])
        t(f'<text x="308" y="{row_y}" fill="#F8FAFC" font-size="10" font-weight="600">{_e(label)}</text>')
        t(f'<rect x="308" y="{row_y+4}" width="210" height="13" fill="#1E2D40" rx="3"/>')
        t(f'<rect x="308" y="{row_y+4}" width="{w}" height="13" fill="#F87171" rx="3"/>')
        t(f'<text x="{314+w}" y="{row_y+15}" fill="#F87171" font-size="9" font-weight="700">−{_e(delta_fmt)}</text>')
        row_y += 36

    # ══════════════════════════════════════════════════════════════════════════
    # ACT III · THE CALL
    # ══════════════════════════════════════════════════════════════════════════
    box_h = max(120, 40 + len(action_lines) * 18 + 40)
    t(f'<text x="566" y="120" fill="{accent}" font-size="8" letter-spacing="4" font-weight="700">ACT III  ·  THE CALL</text>')
    t(f'<rect x="566" y="130" width="210" height="{box_h}" rx="6" fill="{accent}" fill-opacity="0.10" stroke="{accent}" stroke-width="1"/>')
    t(f'<text x="580" y="150" fill="{accent}" font-size="7" letter-spacing="4" font-weight="700">THE ONE ACTION</text>')

    for i, line in enumerate(action_lines):
        t(f'<text x="580" y="{172 + i*18}" fill="#F8FAFC" font-size="12" font-weight="700">{_e(line)}</text>')

    notes_y = 172 + len(action_lines) * 18
    t(f'<text x="580" y="{notes_y + 14}" fill="#94A3B8" font-size="9">Owner: {_e(role.get("title","Team"))}  ·  By EOW</text>')
    t(f'<text x="580" y="{notes_y + 30}" fill="{accent_light}" font-size="10" font-weight="600">Impact: sustain daily lift</text>')

    # ── Footer ────────────────────────────────────────────────────────────── #
    t('<rect x="0" y="426" width="800" height="54" fill="#111827"/>')
    t('<rect x="0" y="426" width="800" height="0.5" fill="#1F2937"/>')
    t(f'<text x="30" y="446" fill="{accent}" font-size="7" letter-spacing="4" font-weight="700">SPEAKER NOTES  ·  WHAT THE BOARD WILL ASK</text>')
    t(f'<text x="30" y="464" fill="#CBD5E1" font-size="10" font-style="italic" font-family="Georgia, serif">{_e(speaker_line)}</text>')

    t('</svg>')
    return "\n".join(p)
