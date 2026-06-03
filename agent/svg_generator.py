"""
svg_generator.py
----------------
Generates role-aware ARIA briefing cards as SVG strings.

5 Templates:
  1. editorial      — Dark Three-Act (Act I: KPIs + sparkline, Act II: drivers, Act III: action)
  2. scorecard      — Executive KPI grid + donut chart
  3. story_arc      — Narrative-first pull quote + treemap chart
  4. ops_dashboard  — Traffic-light tiles + heatmap + movers list
  5. board_pack     — Formal board slide, works beautifully on beige/light backgrounds

4 Visual Styles (palettes):
  dark  · navy  · grey  · beige

Template and style are read from role_config["card_template"] and role_config["card_style"].
All content is fully dynamic — driven by payload and role_config. Zero hardcoded KPI names.

Chart types used:
  - Multi-layer donut  (scorecard, board_pack)
  - Treemap            (story_arc)
  - Heat map           (ops_dashboard)
  - Sparkline polyline (editorial)
"""
from __future__ import annotations

import base64
import html
import io
import re
from datetime import date
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


# ══════════════════════════════════════════════════════════════════════════════════
# STYLE PALETTES  (4 visual backgrounds)
# ══════════════════════════════════════════════════════════════════════════════════

STYLE_PALETTES: dict[str, dict] = {
    "dark": {
        "bg": "#0B1220", "surface": "#111827", "surface2": "#1F2937",
        "text": "#F8FAFC", "subtext": "#CBD5E1", "muted": "#64748B",
        "border": "#1F2937", "footer_bg": "#111827",
    },
    "navy": {
        "bg": "#1B3B6F", "surface": "#1E4A8A", "surface2": "#2558A0",
        "text": "#F0F6FF", "subtext": "#A8C4E0", "muted": "#7BA8D0",
        "border": "#2558A0", "footer_bg": "#152E58",
    },
    "grey": {
        "bg": "#D8DEE9", "surface": "#C8D0E0", "surface2": "#B8C2D8",
        "text": "#1A1F2E", "subtext": "#3A4560", "muted": "#6B7280",
        "border": "#B8C2D8", "footer_bg": "#C0C8D8",
    },
    "beige": {
        "bg": "#F5F0E8", "surface": "#EDE8DF", "surface2": "#E0DAD0",
        "text": "#1A1410", "subtext": "#4A3F35", "muted": "#8B7D70",
        "border": "#D0C8BC", "footer_bg": "#EDE8DF",
    },
}

_DEFAULT_ROLE: dict = {
    "title": "Leadership",
    "badge": "ARIA  ·  EXECUTIVE BRIEFING",
    "primary_kpi": None,
    "kpis": [],
    "accent_color": "#F59E0B",
    "driver_focus": [],
    "card_template": "editorial",
    "card_style": "dark",
}


# ══════════════════════════════════════════════════════════════════════════════════
# TEXT HELPERS
# ══════════════════════════════════════════════════════════════════════════════════

def _e(text) -> str:
    """XML-escape for SVG text content."""
    return html.escape(str(text))

def _strip_md(text: str) -> str:
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*•]\s+", "", text, flags=re.MULTILINE)
    return text.strip()

def _wrap(text: str, max_chars: int) -> list[str]:
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
        return str(ref_date).upper()

def _fmt_currency(val: float) -> str:
    av = abs(val)
    if av >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if av >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:,.0f}"

def _fmt_pct(val: Optional[float]) -> tuple[str, str]:
    if val is None:
        return "—", "#94A3B8"
    sign  = "▲" if val >= 0 else "▼"
    color = "#34D399" if val >= 0 else "#F87171"
    return f"{sign} {abs(val * 100):.1f}%", color

def _sparkline_pts(values: list[float], x0: int, y0: int, w: int, h: int) -> str:
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


# ══════════════════════════════════════════════════════════════════════════════════
# PAYLOAD RESOLUTION HELPERS
# ══════════════════════════════════════════════════════════════════════════════════

def _resolve_kpis(payload: dict, role_config: dict):
    """Return (primary_kpi_name, kpis_dict, all_drivers_list) — fully dynamic."""
    kpis = payload.get("kpis", {})
    prim = role_config.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), None) or "—"

    # Drivers: try primary KPI first, then any available key
    all_drivers = payload.get("drivers", {}).get(prim) or []
    if not all_drivers:
        for _v in payload.get("drivers", {}).values():
            if _v:
                all_drivers = _v
                break

    return prim, kpis, all_drivers


def _rag_color(mom_pct, yoy_pct) -> tuple[str, str]:
    """Red-Amber-Green status colour + symbol."""
    v = yoy_pct if yoy_pct is not None else mom_pct
    if v is None:
        return "#94A3B8", "●"
    if v >= 0.05:
        return "#34D399", "▲"
    if v >= 0:
        return "#FBBF24", "→"
    return "#F87171", "▼"


# ══════════════════════════════════════════════════════════════════════════════════
# CHART GENERATORS  (matplotlib → base64 PNG → embedded in SVG <image>)
# ══════════════════════════════════════════════════════════════════════════════════

def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                transparent=True, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))


def _make_donut_b64(labels: list[str], values: list[float],
                    accent: str, bg: str, text_color: str,
                    title: str = "") -> str:
    """Multi-segment donut chart. Returns base64 PNG or empty string on failure."""
    if not values or sum(abs(v) for v in values) == 0:
        return ""
    try:
        palette = [accent, "#34D399", "#60A5FA", "#F87171", "#A78BFA", "#FBBF24"]
        colors  = palette[:len(values)]

        fig, ax = plt.subplots(figsize=(3.2, 3.2), facecolor="none")
        ax.set_facecolor("none")

        wedges, _ = ax.pie(
            [abs(v) for v in values],
            colors=colors,
            startangle=90,
            wedgeprops={"width": 0.52, "edgecolor": bg, "linewidth": 2},
            counterclock=False,
        )

        # Centre label
        if labels:
            short = labels[0][:14]
            ax.text(0, 0.08, short, ha="center", va="center",
                    fontsize=6.5, color=text_color, fontweight="bold",
                    fontfamily="sans-serif")
            ax.text(0, -0.14, "TOP", ha="center", va="center",
                    fontsize=5, color=text_color, alpha=0.55,
                    fontfamily="sans-serif")

        if title:
            ax.set_title(title, fontsize=7, color=text_color, pad=3,
                         fontfamily="sans-serif")

        ax.axis("equal")
        fig.tight_layout(pad=0)
        return _fig_to_b64(fig)
    except Exception:
        return ""


def _make_treemap_b64(labels: list[str], values: list[float],
                      accent: str, bg: str, text_color: str) -> str:
    """Proportional treemap. Returns base64 PNG or empty string on failure."""
    if not values or sum(abs(v) for v in values) == 0:
        return ""
    try:
        abs_vals = [abs(v) for v in values]
        total    = sum(abs_vals) or 1
        colors   = [accent if v >= 0 else "#F87171" for v in values]

        fig, ax = plt.subplots(figsize=(4.2, 2.8), facecolor="none")
        ax.set_facecolor("none")

        x = 0.0
        for lbl, aval, col in zip(labels, abs_vals, colors):
            w = aval / total
            rect = mpatches.FancyBboxPatch(
                (x + 0.006, 0.06), w - 0.012, 0.88,
                boxstyle="round,pad=0.01",
                facecolor=col, edgecolor=bg, linewidth=2,
                alpha=0.88,
            )
            ax.add_patch(rect)
            if w > 0.08:
                short = lbl[:13] + ("…" if len(lbl) > 13 else "")
                ax.text(x + w / 2, 0.58, short, ha="center", va="center",
                        fontsize=6.5, color="#FFFFFF", fontweight="bold",
                        fontfamily="sans-serif", clip_on=True)
                ax.text(x + w / 2, 0.33, f"{aval/total*100:.0f}%",
                        ha="center", va="center",
                        fontsize=5.5, color="#FFFFFF", alpha=0.75,
                        fontfamily="sans-serif", clip_on=True)
            x += w

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        fig.tight_layout(pad=0)
        return _fig_to_b64(fig)
    except Exception:
        return ""


def _make_heatmap_b64(matrix: list[list[float]],
                      row_labels: list[str], col_labels: list[str],
                      accent: str, bg: str, text_color: str) -> str:
    """Dimension x member heat map. Returns base64 PNG or empty string on failure."""
    if not matrix or not row_labels or not col_labels:
        return ""
    try:
        from matplotlib.colors import LinearSegmentedColormap

        data = np.array(matrix, dtype=float)
        row_max = data.max(axis=1, keepdims=True)
        row_max[row_max == 0] = 1
        data_norm = data / row_max

        try:
            a_rgb = _hex_to_rgb(accent)
        except Exception:
            a_rgb = (0.96, 0.62, 0.04)

        bg_rgb = _hex_to_rgb(bg) if bg.startswith("#") and len(bg) == 7 else (0.07, 0.1, 0.16)
        cmap = LinearSegmentedColormap.from_list("aria", [bg_rgb, a_rgb])

        fig, ax = plt.subplots(
            figsize=(4.8, max(1.6, 0.7 * len(row_labels))),
            facecolor="none",
        )
        ax.set_facecolor("none")

        ax.imshow(data_norm, cmap=cmap, aspect="auto", vmin=0, vmax=1)

        ax.set_xticks(range(len(col_labels)))
        ax.set_xticklabels(
            [c[:8] for c in col_labels],
            fontsize=5.5, color=text_color,
            fontfamily="sans-serif", rotation=30, ha="right",
        )
        ax.set_yticks(range(len(row_labels)))
        ax.set_yticklabels(
            [r[:16] for r in row_labels],
            fontsize=5.5, color=text_color,
            fontfamily="sans-serif",
        )
        ax.tick_params(length=0, colors=text_color)
        for spine in ax.spines.values():
            spine.set_visible(False)

        fig.tight_layout(pad=0.3)
        return _fig_to_b64(fig)
    except Exception:
        return ""


def _build_heatmap_data(payload: dict, role_config: dict):
    """Build matrix (dimension x member) from driver data for the heatmap."""
    _, _, all_drivers = _resolve_kpis(payload, role_config)

    dim_groups: dict[str, dict[str, float]] = {}
    for d in all_drivers[:16]:
        dim   = d.get("dimension", "Other")
        mem   = d.get("member", "?")
        delta = abs(d.get("delta", 0))
        dim_groups.setdefault(dim, {})[mem] = delta

    if not dim_groups:
        return [], [], []

    dims = list(dim_groups.keys())[:3]
    all_members: list[str] = []
    seen: set[str] = set()
    for dim in dims:
        for m in list(dim_groups[dim].keys())[:5]:
            if m not in seen:
                all_members.append(m)
                seen.add(m)
    all_members = all_members[:6]

    matrix = [
        [dim_groups[dim].get(m, 0) for m in all_members]
        for dim in dims
    ]
    return matrix, dims, all_members


# ══════════════════════════════════════════════════════════════════════════════════
# SVG CANVAS HELPERS
# ══════════════════════════════════════════════════════════════════════════════════

def _svg_open(p: list, w: int = 800, h: int = 480, bg: str = "#0B1220"):
    p.append(
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'Inter\', Arial, sans-serif">'
    )
    p.append(f'<rect width="{w}" height="{h}" fill="{bg}"/>')


def _svg_close(p: list):
    p.append("</svg>")


def _fmt_date_short(d_str: str) -> str:
    """Format date as 'Dec 31' for compact range display."""
    try:
        d = date.fromisoformat(d_str)
        return d.strftime("%b %d")
    except Exception:
        return d_str


def _masthead(p: list, badge: str, ref_date: str,
              accent: str, text: str, muted: str, border: str, w: int = 800,
              window_start: str = "", timeframe: str = "1d"):
    p.append(f'<text x="30" y="26" fill="{accent}" font-size="9" letter-spacing="4" font-weight="700">ARIA</text>')
    p.append(
        f'<text x="{w//2}" y="26" fill="{text}" font-size="8" letter-spacing="3" '
        f'text-anchor="middle" opacity="0.7">{_e(badge)}</text>'
    )
    # Top-right: reference date
    p.append(
        f'<text x="{w-30}" y="22" fill="{muted}" font-size="9" letter-spacing="2" '
        f'text-anchor="end">{_e(_fmt_date(ref_date))}</text>'
    )
    # Below date: show window range for multi-day timeframes
    if timeframe != "1d" and window_start and window_start != ref_date:
        range_label = f'{_fmt_date_short(window_start)} → {_fmt_date_short(ref_date)}'
        p.append(
            f'<text x="{w-30}" y="33" fill="{muted}" font-size="7" letter-spacing="1" '
            f'text-anchor="end" opacity="0.7">{_e(range_label)}</text>'
        )
    p.append(f'<rect x="30" y="40" width="{w-60}" height="0.5" fill="{border}"/>')


def _footer(p: list, speaker_line: str,
            accent: str, footer_bg: str, subtext: str,
            w: int = 800, h: int = 480, fh: int = 54):
    fy = h - fh
    p.append(f'<rect x="0" y="{fy}" width="{w}" height="{fh}" fill="{footer_bg}"/>')
    p.append(f'<rect x="0" y="{fy}" width="{w}" height="0.5" fill="{footer_bg}"/>')
    p.append(
        f'<text x="30" y="{fy+20}" fill="{accent}" font-size="7" '
        f'letter-spacing="4" font-weight="700">'
        f'SPEAKER NOTES  ·  WHAT THE BOARD WILL ASK</text>'
    )
    line = speaker_line[:145] + ("…" if len(speaker_line) > 145 else "")
    p.append(
        f'<text x="30" y="{fy+38}" fill="{subtext}" font-size="10" '
        f'font-style="italic" font-family="Georgia, serif" opacity="0.85">'
        f'{_e(line)}</text>'
    )


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE 1 — EDITORIAL  (Three-Act: KPIs + sparkline | drivers | action)
# ══════════════════════════════════════════════════════════════════════════════════

def _tmpl_editorial(narrative, payload: dict, _cfg: dict,
                    role: dict, pal: dict) -> str:
    accent   = role.get("accent_color", "#F59E0B")
    badge    = role.get("badge", "ARIA  ·  EXECUTIVE BRIEFING")
    ref_date = payload.get("reference_date", "")

    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    pk       = kpis.get(prim, {})
    pval     = pk.get("value_fmt", "—")
    mom_s, mom_c = _fmt_pct(pk.get("mom_pct"))
    yoy_s, _     = _fmt_pct(pk.get("yoy_pct"))

    role_kpis = role.get("kpis", list(kpis.keys()))
    sec       = [k for k in role_kpis if k != prim and k in kpis][:2]
    sec1      = sec[0] if len(sec) > 0 else None
    sec2      = sec[1] if len(sec) > 1 else None

    hl_lines  = _wrap(_strip_md(narrative.headline), 70)[:2]
    exec_first = (_strip_md(narrative.exec_summary or "").split(".")[0].strip() + ".")
    act_lines  = _wrap(_strip_md(narrative.recommended_action or ""), 30)[:3]
    spk_raw    = _strip_md(narrative.speaker_notes or "")

    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:3]
    drags = sorted([d for d in all_drivers if d.get("delta", 0) < 0],
                   key=lambda d: d["delta"])[:2]
    max_abs = max([abs(d.get("delta", 0)) for d in lifts + drags] or [1])

    def bw(delta: float, mw: int = 208) -> int:
        return max(4, int(abs(delta) / max_abs * mw))

    series = payload.get("daily_sales_30d", [])

    hl_y1  = 62
    hl_y2  = hl_y1 + 22
    sub_y  = (hl_y2 if len(hl_lines) > 1 else hl_y1) + 18
    rule_y = sub_y + 8

    p: list[str] = []
    _svg_open(p, 800, 480, pal["bg"])
    _masthead(p, badge, ref_date, accent, pal["text"], pal["muted"], pal["border"],
              window_start=payload.get("window_start", ""),
              timeframe=payload.get("timeframe", "1d"))

    p.append(f'<text x="30" y="{hl_y1}" fill="{pal["text"]}" font-size="18" '
             f'font-weight="700" font-family="Georgia, serif">'
             f'{_e(hl_lines[0] if hl_lines else "")}</text>')
    if len(hl_lines) > 1:
        p.append(f'<text x="30" y="{hl_y2}" fill="{pal["text"]}" font-size="18" '
                 f'font-weight="700" font-family="Georgia, serif">'
                 f'{_e(hl_lines[1])}</text>')
    p.append(f'<text x="30" y="{sub_y}" fill="{pal["muted"]}" font-size="11" '
             f'font-style="italic">{_e(exec_first)}</text>')
    p.append(f'<rect x="30" y="{rule_y}" width="44" height="2" fill="{accent}"/>')

    p.append(f'<rect x="288" y="106" width="0.5" height="316" fill="{pal["border"]}"/>')
    p.append(f'<rect x="546" y="106" width="0.5" height="316" fill="{pal["border"]}"/>')

    # ACT I
    p.append(f'<text x="30" y="120" fill="{accent}" font-size="8" letter-spacing="4" '
             f'font-weight="700">ACT I  ·  WHERE WE ARE</text>')
    p.append(f'<text x="30" y="136" fill="{pal["muted"]}" font-size="8" letter-spacing="2">'
             f'{_e(prim.upper())}</text>')
    p.append(f'<text x="30" y="174" fill="{pal["text"]}" font-size="46" font-weight="700" '
             f'font-family="Georgia, serif">{_e(pval)}</text>')
    p.append(f'<text x="30" y="196" fill="{mom_c}" font-size="11" font-weight="600">'
             f'{_e(mom_s)} MoM   ·   {_e(yoy_s)} YoY</text>')
    if sec1:
        p.append(f'<text x="30" y="222" fill="{pal["subtext"]}" font-size="10" '
                 f'font-style="italic" font-family="Georgia, serif">'
                 f'{_e(sec1)}: {_e(kpis[sec1].get("value_fmt","—"))}</text>')
    if sec2:
        p.append(f'<text x="30" y="238" fill="{pal["subtext"]}" font-size="10" '
                 f'font-style="italic" font-family="Georgia, serif">'
                 f'{_e(sec2)}: {_e(kpis[sec2].get("value_fmt","—"))}</text>')

    if len(series) >= 4:
        pts    = _sparkline_pts(series, 30, 298, 240, 48)
        mn, mx = min(series), max(series)
        r      = mx - mn or 1
        last_x = 270
        last_y = 298 + 48 - int((series[-1] - mn) / r * 48)
        p.append(f'<text x="30" y="290" fill="{pal["muted"]}" font-size="7" '
                 f'letter-spacing="3" font-weight="600">'
                 f'30-DAY {_e(prim.upper())} TREND</text>')
        p.append(f'<polyline points="{pts}" stroke="{accent}" stroke-width="1.5" '
                 f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
        p.append(f'<circle cx="{last_x}" cy="{last_y}" r="3.5" fill="{accent}"/>')

    # ACT II
    p.append(f'<text x="308" y="120" fill="{accent}" font-size="8" letter-spacing="4" '
             f'font-weight="700">ACT II  ·  WHAT MOVED IT</text>')
    p.append(f'<text x="308" y="138" fill="{pal["subtext"]}" font-size="10" '
             f'font-style="italic" font-family="Georgia, serif">Top drivers vs prior period.</text>')

    row_y = 156
    for d in lifts:
        lbl  = f"{d.get('dimension','')} → {d.get('member','')}"[:32]
        dfmt = _fmt_currency(abs(d.get("delta", 0)))
        w    = bw(d["delta"])
        p.append(f'<text x="308" y="{row_y}" fill="{pal["text"]}" font-size="10" '
                 f'font-weight="600">{_e(lbl)}</text>')
        p.append(f'<rect x="308" y="{row_y+4}" width="210" height="13" '
                 f'fill="{pal["surface2"]}" rx="3"/>')
        p.append(f'<rect x="308" y="{row_y+4}" width="{w}" height="13" '
                 f'fill="#34D399" rx="3"/>')
        p.append(f'<text x="{314+w}" y="{row_y+15}" fill="#34D399" font-size="9" '
                 f'font-weight="700">+{_e(dfmt)}</text>')
        row_y += 36

    if drags:
        row_y += 4
    for d in drags:
        lbl  = f"{d.get('dimension','')} → {d.get('member','')}"[:32]
        dfmt = _fmt_currency(abs(d.get("delta", 0)))
        w    = bw(d["delta"])
        p.append(f'<text x="308" y="{row_y}" fill="{pal["text"]}" font-size="10" '
                 f'font-weight="600">{_e(lbl)}</text>')
        p.append(f'<rect x="308" y="{row_y+4}" width="210" height="13" '
                 f'fill="{pal["surface2"]}" rx="3"/>')
        p.append(f'<rect x="308" y="{row_y+4}" width="{w}" height="13" '
                 f'fill="#F87171" rx="3"/>')
        p.append(f'<text x="{314+w}" y="{row_y+15}" fill="#F87171" font-size="9" '
                 f'font-weight="700">−{_e(dfmt)}</text>')
        row_y += 36

    # ACT III
    box_h = max(120, 40 + len(act_lines) * 18 + 40)
    p.append(f'<text x="566" y="120" fill="{accent}" font-size="8" letter-spacing="4" '
             f'font-weight="700">ACT III  ·  THE CALL</text>')
    p.append(f'<rect x="566" y="130" width="210" height="{box_h}" rx="6" '
             f'fill="{accent}" fill-opacity="0.10" stroke="{accent}" stroke-width="1"/>')
    p.append(f'<text x="580" y="150" fill="{accent}" font-size="7" letter-spacing="4" '
             f'font-weight="700">THE ONE ACTION</text>')
    for i, line in enumerate(act_lines):
        p.append(f'<text x="580" y="{172+i*18}" fill="{pal["text"]}" font-size="12" '
                 f'font-weight="700">{_e(line)}</text>')
    ny = 172 + len(act_lines) * 18
    p.append(f'<text x="580" y="{ny+14}" fill="{pal["muted"]}" font-size="9">'
             f'Owner: {_e(role.get("title","Team"))}  ·  By EOW</text>')
    p.append(f'<text x="580" y="{ny+30}" fill="{accent}" font-size="10" '
             f'font-weight="600">Impact: sustain daily lift</text>')

    _footer(p, spk_raw, accent, pal["footer_bg"], pal["subtext"])
    _svg_close(p)
    return "\n".join(p)


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE 2 — SCORECARD  (KPI grid tiles + donut chart)
# ══════════════════════════════════════════════════════════════════════════════════

def _tmpl_scorecard(narrative, payload: dict, _cfg: dict,
                    role: dict, pal: dict) -> str:
    accent   = role.get("accent_color", "#F59E0B")
    badge    = role.get("badge", "ARIA  ·  EXECUTIVE BRIEFING")
    ref_date = payload.get("reference_date", "")

    prim, kpis, all_drivers = _resolve_kpis(payload, role)

    hl_lines  = _wrap(_strip_md(narrative.headline), 65)[:2]
    act_lines = _wrap(_strip_md(narrative.recommended_action or ""), 40)[:3]
    spk_raw   = _strip_md(narrative.speaker_notes or "")

    role_kpis = role.get("kpis", list(kpis.keys()))
    show_kpis = [k for k in role_kpis if k in kpis][:6]
    if not show_kpis:
        show_kpis = list(kpis.keys())[:6]

    p: list[str] = []
    _svg_open(p, 800, 480, pal["bg"])
    _masthead(p, badge, ref_date, accent, pal["text"], pal["muted"], pal["border"],
              window_start=payload.get("window_start", ""),
              timeframe=payload.get("timeframe", "1d"))

    hl_y = 56
    p.append(f'<text x="30" y="{hl_y}" fill="{pal["text"]}" font-size="16" '
             f'font-weight="700" font-family="Georgia, serif">'
             f'{_e(hl_lines[0] if hl_lines else "")}</text>')
    if len(hl_lines) > 1:
        p.append(f'<text x="30" y="{hl_y+20}" fill="{pal["text"]}" font-size="16" '
                 f'font-weight="700" font-family="Georgia, serif">'
                 f'{_e(hl_lines[1])}</text>')
    p.append(f'<rect x="30" y="{hl_y+26}" width="40" height="2" fill="{accent}"/>')

    grid_y = 108
    kpi_w, kpi_h, gap = 148, 80, 8
    cols_n = 3

    for i, kname in enumerate(show_kpis):
        kd    = kpis[kname]
        ci    = i % cols_n
        ri    = i // cols_n
        gx    = 30 + ci * (kpi_w + gap)
        gy    = grid_y + ri * (kpi_h + gap)
        rag_c, _ = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        val   = kd.get("value_fmt", "—")
        ms, mc = _fmt_pct(kd.get("mom_pct"))

        p.append(f'<rect x="{gx}" y="{gy}" width="{kpi_w}" height="{kpi_h}" rx="8" '
                 f'fill="{pal["surface"]}" stroke="{pal["border"]}" stroke-width="1"/>')
        p.append(f'<circle cx="{gx+kpi_w-14}" cy="{gy+14}" r="5" fill="{rag_c}"/>')
        p.append(f'<text x="{gx+10}" y="{gy+20}" fill="{pal["muted"]}" font-size="8" '
                 f'letter-spacing="1.5" font-weight="600">'
                 f'{_e(kname[:18].upper())}</text>')
        val_fs = 22 if len(val) <= 8 else 17
        p.append(f'<text x="{gx+10}" y="{gy+50}" fill="{pal["text"]}" '
                 f'font-size="{val_fs}" font-weight="700" font-family="Georgia, serif">'
                 f'{_e(val)}</text>')
        p.append(f'<text x="{gx+10}" y="{gy+68}" fill="{mc}" font-size="9" '
                 f'font-weight="600">{_e(ms)} MoM</text>')

    donut_x, donut_y, donut_w, donut_h = 498, 108, 272, 170
    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:5]
    # Fallback: if no positive drivers, use all drivers sorted by absolute value
    if not lifts and all_drivers:
        lifts = sorted(all_drivers, key=lambda d: abs(d.get("delta", 0)), reverse=True)[:5]
    if lifts:
        d_labels = [f"{d.get('dimension','?')}: {d.get('member','?')}"[:20] for d in lifts]
        d_values = [abs(d["delta"]) for d in lifts]
        b64 = _make_donut_b64(d_labels, d_values, accent, pal["bg"], pal["text"],
                              title=f"{prim} Mix")
        if b64:
            p.append(
                f'<text x="{donut_x + donut_w//2}" y="{donut_y-8}" fill="{accent}" '
                f'font-size="8" letter-spacing="3" font-weight="700" '
                f'text-anchor="middle">CONTRIBUTION MIX</text>'
            )
            p.append(
                f'<image x="{donut_x}" y="{donut_y}" width="{donut_w}" '
                f'height="{donut_h}" href="data:image/png;base64,{b64}"/>'
            )

    ax_x = 498
    ax_y = donut_y + donut_h + 8
    box_h = 36 + len(act_lines) * 18 + 14
    p.append(f'<rect x="{ax_x}" y="{ax_y}" width="272" height="{box_h}" rx="8" '
             f'fill="{accent}" fill-opacity="0.10" stroke="{accent}" stroke-width="1"/>')
    p.append(f'<text x="{ax_x+12}" y="{ax_y+18}" fill="{accent}" font-size="7" '
             f'letter-spacing="4" font-weight="700">RECOMMENDED ACTION</text>')
    for i, line in enumerate(act_lines):
        p.append(f'<text x="{ax_x+12}" y="{ax_y+36+i*18}" fill="{pal["text"]}" '
                 f'font-size="11" font-weight="700">{_e(line)}</text>')
    ow_y = ax_y + 36 + len(act_lines) * 18
    p.append(f'<text x="{ax_x+12}" y="{ow_y+8}" fill="{pal["muted"]}" font-size="9">'
             f'Owner: {_e(role.get("title","Team"))}  ·  By EOW</text>')

    _footer(p, spk_raw, accent, pal["footer_bg"], pal["subtext"])
    _svg_close(p)
    return "\n".join(p)


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE 3 — STORY ARC  (narrative pull-quote + treemap)
# ══════════════════════════════════════════════════════════════════════════════════

def _tmpl_story_arc(narrative, payload: dict, _cfg: dict,
                    role: dict, pal: dict) -> str:
    accent   = role.get("accent_color", "#F59E0B")
    badge    = role.get("badge", "ARIA  ·  EXECUTIVE BRIEFING")
    ref_date = payload.get("reference_date", "")

    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    pk        = kpis.get(prim, {})
    pval      = pk.get("value_fmt", "—")
    mom_s, mom_c = _fmt_pct(pk.get("mom_pct"))
    yoy_s, _     = _fmt_pct(pk.get("yoy_pct"))

    hl_lines  = _wrap(_strip_md(narrative.headline), 60)[:2]
    exec_sents = _strip_md(narrative.exec_summary or "").split(".")
    exec1 = (exec_sents[0].strip() + ".") if exec_sents else ""
    exec2_raw = (exec_sents[1].strip() + ".") if len(exec_sents) > 1 else ""
    exec2 = exec2_raw[:90] + ("…" if len(exec2_raw) > 90 else "")
    act_lines = _wrap(_strip_md(narrative.recommended_action or ""), 35)[:2]
    spk_raw   = _strip_md(narrative.speaker_notes or "")

    role_kpis = role.get("kpis", list(kpis.keys()))
    side_kpis = [k for k in role_kpis if k != prim and k in kpis][:3]

    p: list[str] = []
    _svg_open(p, 800, 480, pal["bg"])
    _masthead(p, badge, ref_date, accent, pal["text"], pal["muted"], pal["border"],
              window_start=payload.get("window_start", ""),
              timeframe=payload.get("timeframe", "1d"))

    bar_h = 14 + len(hl_lines) * 26
    p.append(f'<rect x="30" y="42" width="5" height="{bar_h}" fill="{accent}"/>')
    for i, line in enumerate(hl_lines):
        p.append(f'<text x="44" y="{64+i*26}" fill="{pal["text"]}" font-size="20" '
                 f'font-weight="700" font-family="Georgia, serif">{_e(line)}</text>')

    sep_y = 64 + len(hl_lines) * 26 + 6
    p.append(f'<text x="44" y="{sep_y}" fill="{pal["muted"]}" font-size="10" '
             f'font-style="italic">{_e(exec1)}</text>')
    if exec2:
        p.append(f'<text x="44" y="{sep_y+15}" fill="{pal["muted"]}" font-size="10" '
                 f'font-style="italic">{_e(exec2)}</text>')

    rule_y = sep_y + (28 if exec2 else 16)
    p.append(f'<rect x="30" y="{rule_y}" width="740" height="0.5" fill="{pal["border"]}"/>')

    tree_y = rule_y + 14
    tree_h = 200

    tree_data = [d for d in all_drivers if d.get("delta") is not None][:8]
    if tree_data:
        t_labels = [f"{d.get('dimension','?')} {d.get('member','?')}"[:18]
                    for d in tree_data]
        t_values = [d["delta"] for d in tree_data]
        b64 = _make_treemap_b64(t_labels, t_values, accent, pal["bg"], pal["text"])
        if b64:
            p.append(f'<text x="30" y="{tree_y+12}" fill="{accent}" font-size="8" '
                     f'letter-spacing="3" font-weight="700">DRIVER BREAKDOWN</text>')
            p.append(f'<image x="30" y="{tree_y+18}" width="460" height="{tree_h-22}" '
                     f'href="data:image/png;base64,{b64}"/>')

    rx = 518
    p.append(f'<text x="{rx}" y="{tree_y+12}" fill="{accent}" font-size="8" '
             f'letter-spacing="3" font-weight="700">KEY METRICS</text>')
    p.append(f'<text x="{rx}" y="{tree_y+52}" fill="{pal["text"]}" font-size="38" '
             f'font-weight="700" font-family="Georgia, serif">{_e(pval)}</text>')
    p.append(f'<text x="{rx}" y="{tree_y+68}" fill="{pal["muted"]}" font-size="9" '
             f'letter-spacing="2">{_e(prim.upper())}</text>')
    p.append(f'<text x="{rx}" y="{tree_y+84}" fill="{mom_c}" font-size="10" '
             f'font-weight="600">{_e(mom_s)} MoM  ·  {_e(yoy_s)} YoY</text>')

    for i, k in enumerate(side_kpis):
        ky   = tree_y + 104 + i * 36
        kd   = kpis[k]
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        p.append(f'<rect x="{rx}" y="{ky}" width="252" height="30" rx="6" '
                 f'fill="{pal["surface"]}" stroke="{pal["border"]}" stroke-width="1"/>')
        p.append(f'<text x="{rx+10}" y="{ky+13}" fill="{pal["muted"]}" font-size="8" '
                 f'letter-spacing="1">{_e(k.upper())}</text>')
        p.append(f'<text x="{rx+10}" y="{ky+26}" fill="{pal["text"]}" font-size="13" '
                 f'font-weight="700">{_e(kd.get("value_fmt","—"))}</text>')
        p.append(f'<text x="{rx+190}" y="{ky+26}" fill="{mc}" font-size="9" '
                 f'font-weight="600">{_e(ms)}</text>')

    act_y = tree_y + tree_h + 14
    p.append(f'<rect x="30" y="{act_y}" width="740" height="0.5" fill="{pal["border"]}"/>')
    p.append(f'<text x="30" y="{act_y+18}" fill="{accent}" font-size="8" '
             f'letter-spacing="4" font-weight="700">THE CALL  ·  </text>')
    for i, line in enumerate(act_lines):
        p.append(f'<text x="140" y="{act_y+18+i*16}" fill="{pal["text"]}" '
                 f'font-size="11" font-weight="700">{_e(line)}</text>')

    _footer(p, spk_raw, accent, pal["footer_bg"], pal["subtext"])
    _svg_close(p)
    return "\n".join(p)


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE 4 — OPS DASHBOARD  (traffic lights + heatmap + movers)
# ══════════════════════════════════════════════════════════════════════════════════

def _tmpl_ops_dashboard(narrative, payload: dict, _cfg: dict,
                        role: dict, pal: dict) -> str:
    accent   = role.get("accent_color", "#F59E0B")
    badge    = role.get("badge", "ARIA  ·  OPERATIONS BRIEFING")
    ref_date = payload.get("reference_date", "")

    prim, kpis, all_drivers = _resolve_kpis(payload, role)

    hl_lines  = _wrap(_strip_md(narrative.headline), 55)[:1]
    act_lines = _wrap(_strip_md(narrative.recommended_action or ""), 36)[:3]
    spk_raw   = _strip_md(narrative.speaker_notes or "")

    role_kpis = role.get("kpis", list(kpis.keys()))
    show_kpis = [k for k in role_kpis if k in kpis][:4]
    if not show_kpis:
        show_kpis = list(kpis.keys())[:4]

    p: list[str] = []
    _svg_open(p, 800, 480, pal["bg"])
    _masthead(p, badge, ref_date, accent, pal["text"], pal["muted"], pal["border"],
              window_start=payload.get("window_start", ""),
              timeframe=payload.get("timeframe", "1d"))

    p.append(f'<text x="30" y="58" fill="{pal["text"]}" font-size="15" '
             f'font-weight="700" font-family="Georgia, serif">'
             f'{_e(hl_lines[0] if hl_lines else "")}</text>')
    p.append(f'<rect x="30" y="66" width="740" height="0.5" fill="{pal["border"]}"/>')

    tile_y = 76
    tile_w = 168
    tile_h = 72
    gap    = 8

    for i, kname in enumerate(show_kpis):
        kd  = kpis[kname]
        tx  = 30 + i * (tile_w + gap)
        rc, rs = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        val = kd.get("value_fmt", "—")
        ms, mc = _fmt_pct(kd.get("mom_pct"))

        p.append(f'<rect x="{tx}" y="{tile_y}" width="{tile_w}" height="{tile_h}" rx="6" '
                 f'fill="{pal["surface"]}" stroke="{pal["border"]}" stroke-width="1"/>')
        p.append(f'<rect x="{tx}" y="{tile_y}" width="6" height="{tile_h}" '
                 f'rx="3" fill="{rc}"/>')
        p.append(f'<text x="{tx+14}" y="{tile_y+16}" fill="{pal["muted"]}" '
                 f'font-size="7" letter-spacing="2" font-weight="600">'
                 f'{_e(kname[:20].upper())}</text>')
        val_fs = 22 if len(val) <= 8 else 17
        p.append(f'<text x="{tx+14}" y="{tile_y+44}" fill="{pal["text"]}" '
                 f'font-size="{val_fs}" font-weight="700" font-family="Georgia, serif">'
                 f'{_e(val)}</text>')
        p.append(f'<text x="{tx+14}" y="{tile_y+62}" fill="{mc}" font-size="9" '
                 f'font-weight="600">{rs} {_e(ms)} MoM</text>')

    hm_y = tile_y + tile_h + 14
    hm_h = 176

    matrix, row_labels, col_labels = _build_heatmap_data(payload, role)
    if matrix:
        b64 = _make_heatmap_b64(matrix, row_labels, col_labels,
                                accent, pal["bg"], pal["text"])
        if b64:
            p.append(f'<text x="30" y="{hm_y+10}" fill="{accent}" font-size="8" '
                     f'letter-spacing="3" font-weight="700">DIMENSION HEAT MAP</text>')
            p.append(f'<image x="30" y="{hm_y+16}" width="460" height="{hm_h}" '
                     f'href="data:image/png;base64,{b64}"/>')

    dr_x = 510
    p.append(f'<text x="{dr_x}" y="{hm_y+10}" fill="{accent}" font-size="8" '
             f'letter-spacing="3" font-weight="700">TOP MOVERS</text>')

    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:4]
    drags = sorted([d for d in all_drivers if d.get("delta", 0) < 0],
                   key=lambda d: d["delta"])[:2]

    dr_y = hm_y + 24
    for d in (lifts + drags):
        col  = "#34D399" if d.get("delta", 0) > 0 else "#F87171"
        sign = "+" if d.get("delta", 0) > 0 else "−"
        lbl  = f"{d.get('dimension','')} {d.get('member','')}"[:22]
        dfmt = _fmt_currency(abs(d.get("delta", 0)))
        p.append(f'<rect x="{dr_x}" y="{dr_y}" width="262" height="26" rx="4" '
                 f'fill="{pal["surface"]}" stroke="{pal["border"]}" stroke-width="0.5"/>')
        p.append(f'<rect x="{dr_x}" y="{dr_y}" width="4" height="26" '
                 f'rx="2" fill="{col}"/>')
        p.append(f'<text x="{dr_x+10}" y="{dr_y+17}" fill="{pal["text"]}" '
                 f'font-size="9" font-weight="600">{_e(lbl)}</text>')
        p.append(f'<text x="{dr_x+250}" y="{dr_y+17}" fill="{col}" font-size="9" '
                 f'font-weight="700" text-anchor="end">{sign}{_e(dfmt)}</text>')
        dr_y += 30

    act_y = hm_y + hm_h + 20
    strip_h = 20 + len(act_lines) * 16 + 10
    p.append(f'<rect x="30" y="{act_y}" width="740" height="{strip_h}" rx="6" '
             f'fill="{accent}" fill-opacity="0.08" stroke="{accent}" stroke-width="0.8"/>')
    p.append(f'<text x="44" y="{act_y+16}" fill="{accent}" font-size="8" '
             f'letter-spacing="3" font-weight="700">ACTION  ·  </text>')
    for i, line in enumerate(act_lines):
        p.append(f'<text x="140" y="{act_y+16+i*15}" fill="{pal["text"]}" '
                 f'font-size="10" font-weight="600">{_e(line)}</text>')

    _footer(p, spk_raw, accent, pal["footer_bg"], pal["subtext"])
    _svg_close(p)
    return "\n".join(p)


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE 5 — BOARD PACK  (formal slide, great on beige/light)
# ══════════════════════════════════════════════════════════════════════════════════

def _tmpl_board_pack(narrative, payload: dict, _cfg: dict,
                     role: dict, pal: dict) -> str:
    raw_accent = role.get("accent_color", "#F59E0B")
    badge      = role.get("badge", "ARIA  ·  BOARD BRIEFING")
    ref_date   = payload.get("reference_date", "")

    # On beige, use dark navy for readability; on dark palettes, keep role accent
    accent_eff = "#1E3A5F" if pal["bg"] == "#F5F0E8" else raw_accent
    text_col   = pal["text"]

    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    pk        = kpis.get(prim, {})
    pval      = pk.get("value_fmt", "—")
    mom_s, mom_c = _fmt_pct(pk.get("mom_pct"))
    yoy_s, _     = _fmt_pct(pk.get("yoy_pct"))

    hl_lines  = _wrap(_strip_md(narrative.headline), 58)[:2]
    exec_text = _strip_md(narrative.exec_summary or "")[:200]
    act_lines = _wrap(_strip_md(narrative.recommended_action or ""), 40)[:3]
    spk_raw   = _strip_md(narrative.speaker_notes or "")

    role_kpis = role.get("kpis", list(kpis.keys()))
    show_kpis = [k for k in role_kpis if k in kpis][:5]
    if not show_kpis:
        show_kpis = list(kpis.keys())[:5]

    p: list[str] = []
    _svg_open(p, 800, 480, pal["bg"])

    # Formal top bar
    p.append(f'<rect x="0" y="0" width="800" height="46" fill="{accent_eff}"/>')
    p.append(f'<text x="28" y="17" fill="#FFFFFF" font-size="10" font-weight="700" '
             f'letter-spacing="5">ARIA</text>')
    p.append(f'<text x="28" y="34" fill="#FFFFFF" font-size="8" letter-spacing="3" '
             f'opacity="0.75">{_e(badge)}</text>')
    p.append(f'<text x="772" y="22" fill="#FFFFFF" font-size="9" letter-spacing="2" '
             f'text-anchor="end" opacity="0.8">{_e(_fmt_date(ref_date))}</text>')
    _ws = payload.get("window_start", "")
    _tf = payload.get("timeframe", "1d")
    if _tf != "1d" and _ws and _ws != ref_date:
        _rl = f'{_fmt_date_short(_ws)} → {_fmt_date_short(ref_date)}'
        p.append(f'<text x="772" y="34" fill="#FFFFFF" font-size="7" letter-spacing="1" '
                 f'text-anchor="end" opacity="0.65">{_e(_rl)}</text>')

    hl_y = 68
    for i, line in enumerate(hl_lines):
        p.append(f'<text x="28" y="{hl_y+i*24}" fill="{text_col}" font-size="18" '
                 f'font-weight="700" font-family="Georgia, serif">{_e(line)}</text>')
    sub_y = hl_y + len(hl_lines) * 24 + 4
    exec_wrapped = _wrap(exec_text, 100)[:2]
    for i, line in enumerate(exec_wrapped):
        p.append(f'<text x="28" y="{sub_y+i*14}" fill="{pal["muted"]}" font-size="9.5" '
                 f'font-style="italic" font-family="Georgia, serif">{_e(line)}</text>')

    div1_y = sub_y + len(exec_wrapped) * 14 + 10
    p.append(f'<rect x="28" y="{div1_y}" width="744" height="1" '
             f'fill="{accent_eff}" opacity="0.2"/>')

    kpi_y = div1_y + 14
    n = len(show_kpis)
    kpi_w = max(120, min(140, (744 - (n - 1) * 8) // n))
    for i, kname in enumerate(show_kpis):
        kd  = kpis[kname]
        kx  = 28 + i * (kpi_w + 8)
        val = kd.get("value_fmt", "—")
        ms, mc = _fmt_pct(kd.get("mom_pct"))

        p.append(f'<rect x="{kx}" y="{kpi_y}" width="{kpi_w}" height="58" rx="4" '
                 f'fill="{pal["surface"]}" stroke="{pal["border"]}" stroke-width="1"/>')
        p.append(f'<text x="{kx+8}" y="{kpi_y+14}" fill="{pal["muted"]}" '
                 f'font-size="7" letter-spacing="1.5" font-weight="600">'
                 f'{_e(kname[:16].upper())}</text>')
        val_fs = 20 if len(val) <= 7 else 16
        p.append(f'<text x="{kx+8}" y="{kpi_y+40}" fill="{text_col}" '
                 f'font-size="{val_fs}" font-weight="700" font-family="Georgia, serif">'
                 f'{_e(val)}</text>')
        p.append(f'<text x="{kx+8}" y="{kpi_y+54}" fill="{mc}" font-size="8.5" '
                 f'font-weight="600">{_e(ms)}</text>')

    div2_y = kpi_y + 68
    p.append(f'<rect x="28" y="{div2_y}" width="744" height="1" '
             f'fill="{accent_eff}" opacity="0.2"/>')

    donut_y = div2_y + 12
    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:5]
    if lifts:
        d_labels = [f"{d.get('member','?')}"[:15] for d in lifts]
        d_values = [abs(d["delta"]) for d in lifts]
        b64 = _make_donut_b64(d_labels, d_values, accent_eff, pal["bg"],
                              text_col, title="")
        if b64:
            p.append(f'<text x="28" y="{donut_y+10}" fill="{accent_eff}" '
                     f'font-size="8" letter-spacing="3" font-weight="700">'
                     f'CONTRIBUTION MIX</text>')
            p.append(f'<image x="28" y="{donut_y+14}" width="190" height="130" '
                     f'href="data:image/png;base64,{b64}"/>')

    rec_x = 234
    rec_y = donut_y + 12
    rec_h = 22 + len(act_lines) * 22 + 16
    p.append(f'<text x="{rec_x}" y="{rec_y+10}" fill="{accent_eff}" font-size="8" '
             f'letter-spacing="3" font-weight="700">BOARD RECOMMENDATION</text>')
    p.append(f'<rect x="{rec_x}" y="{rec_y+16}" width="538" height="{rec_h}" rx="4" '
             f'fill="{pal["surface"]}" stroke="{accent_eff}" stroke-width="1"/>')
    for i, line in enumerate(act_lines):
        p.append(f'<text x="{rec_x+14}" y="{rec_y+38+i*22}" fill="{text_col}" '
                 f'font-size="12" font-weight="700" font-family="Georgia, serif">'
                 f'{_e(line)}</text>')
    ow_y = rec_y + 38 + len(act_lines) * 22
    p.append(f'<text x="{rec_x+14}" y="{ow_y+4}" fill="{pal["muted"]}" font-size="9">'
             f'Owner: {_e(role.get("title","Team"))}  ·  By EOW</text>')

    sn_y = donut_y + 152
    p.append(f'<rect x="28" y="{sn_y}" width="744" height="1" '
             f'fill="{accent_eff}" opacity="0.15"/>')
    p.append(f'<text x="28" y="{sn_y+13}" fill="{accent_eff}" font-size="7" '
             f'letter-spacing="3" font-weight="700">ANTICIPATED QUESTIONS</text>')
    p.append(f'<text x="28" y="{sn_y+26}" fill="{pal["muted"]}" font-size="9" '
             f'font-style="italic" font-family="Georgia, serif">'
             f'{_e(spk_raw[:160])}</text>')

    _svg_close(p)
    return "\n".join(p)


# ══════════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY + DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════════

TEMPLATES: dict = {
    "editorial":     _tmpl_editorial,
    "scorecard":     _tmpl_scorecard,
    "story_arc":     _tmpl_story_arc,
    "ops_dashboard": _tmpl_ops_dashboard,
    "board_pack":    _tmpl_board_pack,
}


def generate_svg(narrative, payload: dict, _config: dict,
                 role_config: Optional[dict] = None) -> str:
    """
    Main entry point. Dispatches to the correct template based on
    role_config['card_template'], applies the palette from role_config['card_style'].
    Falls back to editorial + dark if keys are missing.
    """
    role = dict(role_config or _DEFAULT_ROLE)

    # Resolve primary KPI dynamically — never assume a name exists in payload
    kpis = payload.get("kpis", {})
    prim = role.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), None) or "—"
    role["primary_kpi"] = prim

    # Template
    tmpl_key = role.get("card_template", "editorial")
    tmpl_fn  = TEMPLATES.get(tmpl_key, _tmpl_editorial)

    # Style palette
    style_key = role.get("card_style", "dark")
    pal = dict(STYLE_PALETTES.get(style_key, STYLE_PALETTES["dark"]))

    return tmpl_fn(narrative, payload, _config, role, pal)
