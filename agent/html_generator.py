"""
html_generator.py  —  ARIA Briefing Cards  (v5: auto-detect dims when config dims missing from df)
ARIA_DEPLOY_VERSION = "2026-06-11-v5"   # bump this on every push so you can verify deployment
──────────────────────────────────────────────────────────────────────
3 Templates:
  1. editorial   — Newsletter / magazine.  Big headline, inline MOM/YOY/WOW
                   highlights in narrative paragraphs, one dimension bar chart.
                   No filters.  Tone differs by role tier.
  2. scorecard   — KPI grid.  Every role-specific KPI gets its own tile with
                   a sparkline, MOM/YOY deltas and target indicator.
                   Up to 4 "View by" dimension dropdowns that switch the
                   breakdown chart below the grid.
  3. dossier     — Full analytics deep-dive.  5 mini KPI boxes + 2×2 chart
                   grid (line trend | dimension bar | contribution donut |
                   period waterfall).  Up to 4 dimension filters.

All templates:
  • ARIA | <Company Logo> in top-left masthead
  • Role-tier tone: C-Suite / Leadership / Management (set by narrative_generator)
  • MOM, YOY, WOW + Target Δ surfaced per template
  • Dimension breakdown driven by driver data (no raw df required)

Charts via Chart.js 4.x CDN.
PNG conversion via Selenium headless Chrome (html_to_png).
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import date
from typing import Optional
from collections import defaultdict

log = logging.getLogger(__name__)

# ─── CDN ─────────────────────────────────────────────────────────────────── #
_CHARTJS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"

# ─── Palettes ─────────────────────────────────────────────────────────────── #
PALETTES = {
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
        "bg": "#E8ECF4", "surface": "#FFFFFF", "surface2": "#F1F4FB",
        "text": "#1A1F2E", "subtext": "#3A4560", "muted": "#6B7280",
        "border": "#D1D9E6", "footer_bg": "#F1F4FB",
    },
    "beige": {
        "bg": "#F5F0E8", "surface": "#FFFFFF", "surface2": "#EDE8DF",
        "text": "#1A1410", "subtext": "#4A3F35", "muted": "#8B7D70",
        "border": "#D0C8BC", "footer_bg": "#EDE8DF",
    },
}

_DEFAULT_ROLE = {
    "title": "Leadership", "badge": "ARIA  ·  EXECUTIVE BRIEFING",
    "primary_kpi": None, "kpis": [], "accent_color": "#F59E0B",
    "card_template": "editorial", "card_style": "dark",
    "company_name": "",
}

# ─── Domain icons (SVG snippets, 48×48 viewbox) ───────────────────────────── #
_ICONS = {
    "aviation": '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6 24L42 8L30 28L42 40L6 24Z" fill="ACCENT" opacity="0.9"/><path d="M30 28L24 36L20 30" fill="ACCENT" opacity="0.5"/></svg>',
    "retail":   '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="8" y="18" width="32" height="22" rx="2" fill="ACCENT" opacity="0.15" stroke="ACCENT" stroke-width="2"/><path d="M16 18V14a8 8 0 1116 0v4" stroke="ACCENT" stroke-width="2" stroke-linecap="round"/><circle cx="18" cy="30" r="2" fill="ACCENT"/><circle cx="30" cy="30" r="2" fill="ACCENT"/></svg>',
    "hr":       '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="24" cy="16" r="8" fill="ACCENT" opacity="0.2" stroke="ACCENT" stroke-width="2"/><path d="M8 40c0-8.837 7.163-16 16-16s16 7.163 16 16" stroke="ACCENT" stroke-width="2" stroke-linecap="round"/><path d="M30 26l4 4-4 4" stroke="ACCENT" stroke-width="1.5" stroke-linecap="round"/></svg>',
    "media":    '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="6" y="10" width="36" height="24" rx="3" fill="ACCENT" opacity="0.15" stroke="ACCENT" stroke-width="2"/><polygon points="20,16 20,28 32,22" fill="ACCENT"/><line x1="16" y1="38" x2="32" y2="38" stroke="ACCENT" stroke-width="2" stroke-linecap="round"/></svg>',
    "finance":  '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 36L18 24l8 6 14-16" stroke="ACCENT" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="40" cy="12" r="4" fill="ACCENT" opacity="0.3" stroke="ACCENT" stroke-width="1.5"/></svg>',
    "qsr":      '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10 20h28M10 26h28" stroke="ACCENT" stroke-width="3" stroke-linecap="round"/><rect x="8" y="30" width="32" height="8" rx="2" fill="ACCENT" opacity="0.2" stroke="ACCENT" stroke-width="1.5"/><path d="M16 20V14a8 4 0 0116 0v6" stroke="ACCENT" stroke-width="1.5"/></svg>',
    "analytics": '<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="8" y="28" width="8" height="12" rx="1" fill="ACCENT" opacity="0.5"/><rect x="20" y="20" width="8" height="20" rx="1" fill="ACCENT" opacity="0.75"/><rect x="32" y="12" width="8" height="28" rx="1" fill="ACCENT"/><path d="M8 8l32 0" stroke="ACCENT" stroke-width="1" opacity="0.3"/></svg>',
}


# ══════════════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _e(t) -> str:
    return _html.escape(str(t))

def _strip_md(text: str) -> str:
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*•]\s+", "", text, flags=re.MULTILINE)
    return text.strip()

def _fmt_date(ref: str) -> str:
    try:
        d = date.fromisoformat(ref)
        return d.strftime("%a %b %d %Y").upper()
    except Exception:
        return str(ref).upper()

def _fmt_date_short(d_str: str) -> str:
    try:
        d = date.fromisoformat(d_str)
        return d.strftime("%b %d")
    except Exception:
        return d_str

def _fmt_pct(val: Optional[float]) -> tuple[str, str]:
    if val is None:
        return "—", "#94A3B8"
    sign  = "▲" if val >= 0 else "▼"
    color = "#34D399" if val >= 0 else "#F87171"
    return f"{sign} {abs(val * 100):.1f}%", color

def _fmt_delta_short(val: Optional[float]) -> str:
    """Format a raw numeric delta (not pct) as short label."""
    if val is None:
        return "—"
    av = abs(val)
    s  = "+" if val >= 0 else "−"
    if av >= 1_000_000:
        return f"{s}${av/1_000_000:.1f}M"
    if av >= 1_000:
        return f"{s}${av/1_000:.0f}K"
    return f"{s}{av:.1f}"

def _rag_color(mom_pct, yoy_pct) -> str:
    v = yoy_pct if yoy_pct is not None else mom_pct
    if v is None: return "#94A3B8"
    if v >= 0.05: return "#34D399"
    if v >= 0:    return "#FBBF24"
    return "#F87171"


# ── KPI resolution ────────────────────────────────────────────────────────── #

def _resolve_kpis(payload: dict, role: dict):
    kpis = payload.get("kpis", {})
    prim = role.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), "—")

    # Try YoY drivers first; fall back to DoD drivers if YoY is empty.
    # This ensures the bar chart renders even when the YoY window has no
    # prior-year data (e.g. dataset only covers one year) or when all KPI
    # column names in config.yaml don't match the dataframe.
    for drivers_key in ("drivers", "drivers_dod"):
        drivers_dict = payload.get(drivers_key, {})
        all_drivers  = drivers_dict.get(prim) or []
        if not all_drivers:
            for v in drivers_dict.values():
                if v:
                    all_drivers = v
                    break
        if all_drivers:
            break

    return prim, kpis, all_drivers


# ── Role tier ─────────────────────────────────────────────────────────────── #

_C_SUITE_KW    = {"ceo","cfo","coo","cmo","cto","chro","chief"}
_LEADERSHIP_KW = {"vp","vice","director","head","president"}

def _role_tier(role: dict) -> str:
    """Returns 'c_suite', 'leadership', or 'management'."""
    tid = (role.get("tier") or role.get("title") or "").lower()
    if any(k in tid for k in _C_SUITE_KW):    return "c_suite"
    if any(k in tid for k in _LEADERSHIP_KW): return "leadership"
    return "management"


# ── Company logo ──────────────────────────────────────────────────────────── #

def _company_logo_html(company_name: str, height: int = 26) -> str:
    """<img> via Clearbit with Google-favicon fallback → text fallback."""
    if not company_name:
        return ""
    slug   = re.sub(r"\s+", "", company_name.lower())
    domain = f"{slug}.com"
    cb_url = f"https://logo.clearbit.com/{domain}"
    gf_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
    name_e = _e(company_name)
    return (
        f'<img src="{cb_url}" '
        f'onerror="this.onerror=null;this.src=\'{gf_url}\';" '
        f'style="height:{height}px;width:auto;object-fit:contain;'
        f'vertical-align:middle;border-radius:3px;margin-left:8px;opacity:0.9;" '
        f'alt="{name_e}" title="{name_e}">'
    )


# ── Domain icon ───────────────────────────────────────────────────────────── #

def _domain_icon_svg(role: dict, accent: str, size: int = 56) -> str:
    company = (role.get("company_name") or "").lower()
    badge   = (role.get("badge") or "").lower()
    title   = (role.get("title") or "").lower()

    if any(k in company for k in ("lufthansa","air","aviat","fly")):
        key = "aviation"
    elif any(k in company for k in ("sobey","grocery","walmart","superstore","retail","store")):
        key = "retail"
    elif any(k in company for k in ("hr","people","talent","human resource")):
        key = "hr"
    elif any(k in company for k in ("netflix","stream","media","entertain","disney")):
        key = "media"
    elif any(k in title+badge for k in ("cfo","finance","financial","treasury")):
        key = "finance"
    elif any(k in company for k in ("burger","mcdonald","kfc","pizza","qsr","restaur","food")):
        key = "qsr"
    else:
        key = "analytics"

    svg = _ICONS.get(key, _ICONS["analytics"]).replace("ACCENT", accent)
    return (
        f'<div style="width:{size}px;height:{size}px;flex-shrink:0;'
        f'display:flex;align-items:center;justify-content:center">'
        f'{svg}'
        f'</div>'
    )


# ── Driver grouping for dimension charts ──────────────────────────────────── #

def _dim_groups(all_drivers: list, max_dims: int = 4, max_members: int = 8) -> dict:
    """
    Returns {dimension: {labels:[...], values:[...]}} from driver list.
    Used for dimension filter dropdowns and bar charts.

    Priority for bar-chart value:
      1. d["value"]   – explicit value field (wizard path)
      2. d["current"] – current-period aggregate (agent/DriverItem path)
      3. abs(d["delta"]) – fallback change magnitude
    Condition guards only on dim/member being truthy — never on val,
    so zero-delta entries (stable data) still appear in the chart.
    """
    raw: dict[str, dict] = defaultdict(dict)
    for d in all_drivers:
        dim    = d.get("dimension", "")
        member = d.get("member", "")
        if not dim or not member:
            continue
        # Use current-period value preferentially so bars show magnitude,
        # not just change — avoids empty charts when YoY deltas are tiny.
        val = (d.get("value")
               or d.get("current")
               or abs(d.get("delta") or 0))
        if val:
            raw[dim][member] = max(raw[dim].get(member, 0), val)
        else:
            # Register the member with 0 so it still appears (will be sorted last)
            raw[dim].setdefault(member, 0)

    result = {}
    for dim in list(raw.keys())[:max_dims]:
        # Only include members with a positive value for meaningful bars
        items = sorted(
            [(m, v) for m, v in raw[dim].items() if v > 0],
            key=lambda x: x[1], reverse=True
        )[:max_members]
        if items:
            labels, values = zip(*items)
            result[dim] = {"labels": list(labels), "values": [round(v, 2) for v in values]}
    return result


# ── Waterfall data ────────────────────────────────────────────────────────── #

def _waterfall_data(kpis: dict, prim: str, all_drivers: list):
    """
    Build floating-bar waterfall: [Prior Period → lifts/drags → Current].
    Returns (labels, floats [[start,end]], colors, is_reference).
    """
    pk      = kpis.get(prim, {})
    current = pk.get("value", 0) or 0
    mom_pct = pk.get("mom_pct") or 0
    prior   = current / (1 + mom_pct) if (1 + mom_pct) != 0 else current

    lifts = sorted([d for d in all_drivers if d.get("delta",0)>0],
                   key=lambda x: x["delta"], reverse=True)[:2]
    drags = sorted([d for d in all_drivers if d.get("delta",0)<0],
                   key=lambda x: x["delta"])[:2]

    labels   = []
    floats   = []
    colors   = []
    is_ref   = []

    labels.append("Prior"); floats.append([0, round(prior, 2)]); colors.append("#60A5FA"); is_ref.append(True)
    cum = prior
    for d in lifts + drags:
        delta  = d.get("delta", 0)
        member = str(d.get("member","?"))[:12]
        labels.append(member)
        floats.append([round(cum, 2), round(cum + delta, 2)])
        colors.append("#34D399" if delta >= 0 else "#F87171")
        is_ref.append(False)
        cum += delta
    labels.append("Current"); floats.append([0, round(current, 2)]); colors.append("#60A5FA"); is_ref.append(True)

    return labels, floats, colors, is_ref


# ══════════════════════════════════════════════════════════════════════════════
# BASE CSS
# ══════════════════════════════════════════════════════════════════════════════

def _base_css(pal: dict, accent: str) -> str:
    is_light = pal["bg"] in ("#F5F0E8","#FFFFFF","#E8ECF4","#EDE8DF")
    input_bg  = pal["surface2"] if not is_light else "#FFFFFF"
    input_col = pal["text"]
    return f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ background:{pal['bg']}; }}
body {{
    background:{pal['bg']}; color:{pal['text']};
    font-family:-apple-system,BlinkMacSystemFont,'Inter',Arial,sans-serif;
    font-size:13px; line-height:1.5;
}}
.card {{
    width:100%; max-width:900px; background:{pal['bg']};
    margin:0 auto; display:flex; flex-direction:column;
    padding:20px 28px 0;
}}
/* ─ Masthead ──────────────────────────────── */
.mast {{
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid {pal['border']}; padding-bottom:10px; margin-bottom:14px;
    gap:8px; flex-wrap:wrap;
}}
.mast-left  {{ display:flex; align-items:center; gap:0; flex-shrink:0; }}
.mast-mid   {{ flex:1; text-align:center; color:{pal['muted']}; font-size:8px;
               letter-spacing:3px; font-weight:600; }}
.mast-right {{ display:flex; align-items:center; gap:8px; flex-shrink:0; }}
.aria-logo  {{ color:{accent}; font-size:11px; font-weight:800; letter-spacing:4px; }}
.date-lbl   {{ color:{pal['muted']}; font-size:9px; letter-spacing:2px; font-weight:600; }}
.date-range {{ color:{pal['muted']}; font-size:8px; opacity:0.7; }}

/* ─ Section labels ────────────────────────── */
.sec {{ font-size:8px; font-weight:700; letter-spacing:3px;
        color:{accent}; text-transform:uppercase; margin-bottom:7px; }}

/* ─ KPI tiles ────────────────────────────── */
.kpi-grid  {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
.kpi-tile  {{ flex:1 1 150px; min-width:130px; max-width:220px;
              background:{pal['surface']}; border:1px solid {pal['border']};
              border-radius:8px; padding:10px 12px; position:relative;
              overflow:hidden; }}
.kpi-rag   {{ position:absolute; top:9px; right:9px; width:8px; height:8px;
              border-radius:50%; }}
.kpi-name  {{ font-size:8px; font-weight:700; letter-spacing:1.5px;
              color:{pal['muted']}; text-transform:uppercase; margin-bottom:5px; }}
.kpi-val   {{ font-size:20px; font-weight:700; color:{pal['text']};
              font-family:Georgia,serif; line-height:1.1; margin-bottom:3px; }}
.kpi-val.sm{{ font-size:15px; }}
.kpi-d     {{ font-size:9px; font-weight:600; }}
.kpi-spark {{ height:32px; margin:4px 0 2px; }}

/* ─ Mini KPI row (Dossier top) ───────────── */
.mkpi-row  {{ display:flex; gap:8px; margin-bottom:14px; }}
.mkpi      {{ flex:1; background:{pal['surface']}; border:1px solid {pal['border']};
              border-radius:6px; padding:8px 10px; min-width:0; }}
.mkpi-name {{ font-size:7px; font-weight:700; letter-spacing:1.5px;
              color:{pal['muted']}; text-transform:uppercase; margin-bottom:3px; }}
.mkpi-val  {{ font-size:14px; font-weight:700; color:{pal['text']};
              font-family:Georgia,serif; white-space:nowrap;
              overflow:hidden; text-overflow:ellipsis; }}
.mkpi-d    {{ font-size:8px; font-weight:600; }}

/* ─ Charts ───────────────────────────────── */
.chart-box {{ position:relative; }}
canvas     {{ display:block; }}

/* ─ Narrative ────────────────────────────── */
.narr {{ font-size:11px; color:{pal['subtext']}; line-height:1.6; }}
.narr strong {{ color:{pal['text']}; }}
.stat-pill {{
    display:inline-block; border-radius:4px; padding:1px 7px; margin:1px 2px;
    font-size:9px; font-weight:700; vertical-align:middle;
    border:1px solid;
}}

/* ─ Filter panel ─────────────────────────── */
.filter-panel {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
.filter-lbl   {{ font-size:7px; font-weight:700; letter-spacing:3px;
                 color:{pal['muted']}; text-transform:uppercase; }}
.filter-sel   {{
    font-size:9px; background:{input_bg}; color:{input_col};
    border:1px solid {pal['border']}; border-radius:5px;
    padding:4px 8px; cursor:pointer; outline:none;
    font-family:inherit;
}}
.filter-chip  {{
    font-size:8px; font-weight:700; letter-spacing:1px;
    background:{accent}22; border:1px solid {accent}55;
    color:{accent}; border-radius:12px; padding:2px 9px;
    display:none;
}}

/* ─ Action box ───────────────────────────── */
.action-box  {{ background:{accent}15; border:1px solid {accent}40;
                border-radius:8px; padding:12px 14px; }}
.action-lbl  {{ font-size:7px; font-weight:700; letter-spacing:4px;
                color:{accent}; margin-bottom:6px; }}
.action-text {{ font-size:11px; font-weight:700; color:{pal['text']};
                line-height:1.4; margin-bottom:4px; }}
.action-meta {{ font-size:9px; color:{pal['muted']}; }}

/* ─ Footer ───────────────────────────────── */
.footer      {{ background:{pal['footer_bg']}; margin:14px -28px 0;
                padding:10px 28px; border-top:1px solid {pal['border']}; }}
.footer-lbl  {{ font-size:7px; font-weight:700; letter-spacing:4px;
                color:{accent}; margin-bottom:4px; }}
.footer-text {{ font-size:9px; color:{pal['subtext']}; font-style:italic;
                font-family:Georgia,serif; opacity:0.85; }}

/* ─ Editorial specific ───────────────────── */
.ed-hero     {{ display:flex; align-items:center; gap:18px;
                padding:14px 0 12px; border-bottom:1px solid {pal['border']};
                margin-bottom:14px; }}
.ed-headline {{ font-size:20px; font-weight:700; color:{pal['text']};
                font-family:Georgia,serif; line-height:1.25;
                flex:1; }}
.ed-sub      {{ font-size:11px; color:{pal['muted']}; font-style:italic;
                margin-top:5px; }}
.ed-col      {{ flex:1; }}
.ed-col + .ed-col {{ border-left:1px solid {pal['border']}; padding-left:16px; }}
.ed-col + .ed-col + .ed-col {{ border-left:1px solid {pal['border']}; padding-left:16px; }}
.ed-cols     {{ display:flex; gap:0; }}
.ed-body     {{ font-size:11px; color:{pal['subtext']}; line-height:1.65;
                font-family:Georgia,serif; }}
.ed-stat-row {{ display:flex; flex-wrap:wrap; gap:5px; margin:10px 0 6px; }}

/* ─ Editorial dimension pills ───────────────── */
.ed-dim-pill {{
    font-size:7px; font-weight:700; letter-spacing:1px;
    text-transform:uppercase; background:transparent;
    border:1px solid {pal['border']}; color:{pal['muted']};
    border-radius:10px; padding:2px 8px; cursor:pointer;
    font-family:inherit; transition:all 0.15s;
}}
.ed-dim-pill-active, .ed-dim-pill:hover {{
    background:{accent}20; border-color:{accent}80; color:{accent};
}}
"""


# ══════════════════════════════════════════════════════════════════════════════
# CHART SCRIPTS
# ══════════════════════════════════════════════════════════════════════════════

_PALETTE8 = ["#F59E0B","#34D399","#60A5FA","#F87171","#A78BFA",
              "#FBBF24","#F97316","#14B8A6"]

def _palette(accent: str, n: int) -> list:
    p = [accent] + [c for c in _PALETTE8 if c != accent]
    return (p * 4)[:n]


def _chart_sparkline(cid: str, values: list, accent: str) -> str:
    vj = json.dumps([round(v, 2) for v in values])
    lj = json.dumps([str(i) for i in range(len(values))])
    return f"""
    new Chart(document.getElementById('{cid}'), {{
        type:'line',
        data:{{ labels:{lj}, datasets:[{{ data:{vj},
            borderColor:'{accent}', borderWidth:1.5, pointRadius:0,
            fill:true, backgroundColor:'{accent}18', tension:0.4 }}] }},
        options:{{ responsive:true, maintainAspectRatio:false,
            plugins:{{ legend:{{display:false}}, tooltip:{{enabled:false}} }},
            scales:{{ x:{{display:false}}, y:{{display:false}} }},
            animation:{{duration:0}} }}
    }});"""


def _chart_donut(cid: str, labels: list, values: list, accent: str) -> str:
    colors = _palette(accent, len(values))
    lj = json.dumps(labels); vj = json.dumps(values); cj = json.dumps(colors)
    return f"""
    new Chart(document.getElementById('{cid}'), {{
        type:'doughnut',
        data:{{ labels:{lj}, datasets:[{{ data:{vj},
            backgroundColor:{cj}, borderWidth:2, borderColor:'transparent',
            hoverOffset:4 }}] }},
        options:{{ cutout:'62%', responsive:true, maintainAspectRatio:false,
            plugins:{{ legend:{{display:false}},
                tooltip:{{ callbacks:{{ label: ctx => ' '+ctx.label+': '+
                    (ctx.raw>=1e6?'$'+(ctx.raw/1e6).toFixed(1)+'M':
                     ctx.raw>=1e3?'$'+(ctx.raw/1e3).toFixed(1)+'K':
                     ctx.raw.toFixed(0)) }} }} }},
            animation:{{duration:0}} }}
    }});"""


def _chart_bar_h(cid: str, labels: list, values: list, accent: str, pal: dict) -> str:
    """Horizontal bar (driver breakdown / dimension)."""
    colors = ["#34D399" if v >= 0 else "#F87171" for v in values]
    lj = json.dumps(labels); vj = json.dumps(values); cj = json.dumps(colors)
    tc = pal["muted"]
    return f"""
    ARIA_CHARTS['{cid}'] = new Chart(document.getElementById('{cid}'), {{
        type:'bar',
        data:{{ labels:{lj}, datasets:[{{ data:{vj},
            backgroundColor:{cj}, borderRadius:3, borderWidth:0 }}] }},
        options:{{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
            plugins:{{ legend:{{display:false}},
                tooltip:{{ callbacks:{{ label: ctx =>
                    (Math.abs(ctx.raw)>=1e6
                     ? (ctx.raw>=0?'+':'')+( ctx.raw/1e6).toFixed(1)+'M'
                     : Math.abs(ctx.raw)>=1e3
                     ? (ctx.raw>=0?'+':'')+(ctx.raw/1e3).toFixed(1)+'K'
                     : ctx.raw.toFixed(0)) }} }} }},
            scales:{{
                x:{{ display:false, grid:{{display:false}} }},
                y:{{ ticks:{{ color:'{tc}', font:{{size:9}} }},
                     grid:{{display:false}}, border:{{display:false}} }}
            }},
            animation:{{duration:0}} }}
    }});"""


def _chart_bar_v(cid: str, labels: list, values: list, accent: str, pal: dict) -> str:
    """Vertical bar — dimension breakdown (filterable)."""
    colors = _palette(accent, len(values))
    lj = json.dumps(labels); vj = json.dumps(values); cj = json.dumps(colors)
    tc = pal["muted"]
    return f"""
    (function(){{
        var ctx = document.getElementById('{cid}');
        if(!ctx) return;
        ARIA_CHARTS['{cid}'] = new Chart(ctx, {{
            type:'bar',
            data:{{ labels:{lj}, datasets:[{{ data:{vj},
                backgroundColor:{cj}, borderRadius:4, borderWidth:0 }}] }},
            options:{{ responsive:true, maintainAspectRatio:false,
                plugins:{{ legend:{{display:false}},
                    tooltip:{{ callbacks:{{ label: ctx =>
                        (ctx.raw>=1e6?'$'+(ctx.raw/1e6).toFixed(1)+'M':
                         ctx.raw>=1e3?'$'+(ctx.raw/1e3).toFixed(1)+'K':
                         ctx.raw.toFixed(1)) }} }} }},
                scales:{{
                    x:{{ ticks:{{ color:'{tc}', font:{{size:9}}, maxRotation:30 }},
                         grid:{{display:false}}, border:{{display:false}} }},
                    y:{{ ticks:{{ color:'{tc}', font:{{size:8}} }},
                         grid:{{ color:'{pal["border"]}' }}, border:{{display:false}} }}
                }},
                animation:{{duration:300}} }}
        }});
    }})();"""


def _chart_line_trend(cid: str, values: list, accent: str, pal: dict,
                       labels: Optional[list] = None) -> str:
    """Line chart for primary KPI trend (Dossier top-left)."""
    if labels is None:
        labels = [str(i) for i in range(len(values))]
    vj = json.dumps([round(v, 2) for v in values])
    lj = json.dumps(labels)
    tc = pal["muted"]
    return f"""
    (function(){{
        var ctx = document.getElementById('{cid}');
        if(!ctx) return;
        ARIA_CHARTS['{cid}'] = new Chart(ctx, {{
            type:'line',
            data:{{ labels:{lj}, datasets:[{{
                data:{vj}, borderColor:'{accent}', borderWidth:2,
                pointRadius:0, fill:true,
                backgroundColor:'{accent}22', tension:0.35
            }}] }},
            options:{{ responsive:true, maintainAspectRatio:false,
                plugins:{{ legend:{{display:false}} }},
                scales:{{
                    x:{{ ticks:{{ color:'{tc}', font:{{size:8}}, maxTicksLimit:6 }},
                         grid:{{display:false}}, border:{{display:false}} }},
                    y:{{ ticks:{{ color:'{tc}', font:{{size:8}} }},
                         grid:{{ color:'{pal["border"]}' }}, border:{{display:false}} }}
                }},
                animation:{{duration:0}} }}
        }});
    }})();"""


def _chart_waterfall(cid: str, labels: list, floats: list,
                      colors: list, pal: dict) -> str:
    """Floating-bar waterfall chart (period-over-period)."""
    lj = json.dumps(labels)
    fj = json.dumps(floats)
    cj = json.dumps(colors)
    tc = pal["muted"]
    return f"""
    (function(){{
        var ctx = document.getElementById('{cid}');
        if(!ctx) return;
        new Chart(ctx, {{
            type:'bar',
            data:{{ labels:{lj}, datasets:[{{
                data:{fj},
                backgroundColor:{cj},
                borderRadius:3, borderWidth:0
            }}] }},
            options:{{ responsive:true, maintainAspectRatio:false,
                plugins:{{ legend:{{display:false}},
                    tooltip:{{ callbacks:{{
                        label: ctx => {{
                            var a=ctx.raw, v=Array.isArray(a)?a[1]-a[0]:a;
                            return (v>=0?'+':'')+
                                (Math.abs(v)>=1e6?(v/1e6).toFixed(1)+'M':
                                 Math.abs(v)>=1e3?(v/1e3).toFixed(0)+'K':
                                 v.toFixed(0));
                        }}
                    }} }} }},
                scales:{{
                    x:{{ ticks:{{ color:'{tc}', font:{{size:9}} }},
                         grid:{{display:false}}, border:{{display:false}} }},
                    y:{{ min:0,
                         ticks:{{ color:'{tc}', font:{{size:8}},
                             callback: v => v>=1e6?'$'+(v/1e6).toFixed(0)+'M':
                                          v>=1e3?'$'+(v/1e3).toFixed(0)+'K':v }},
                         grid:{{ color:'{pal["border"]}' }}, border:{{display:false}} }}
                }},
                animation:{{duration:0}} }}
        }});
    }})();"""


# ── KPI donut fallback ────────────────────────────────────────────────────── #

def _donut_data_from_drivers(all_drivers: list, top_n: int = 5):
    """
    Build donut chart data from driver list.

    For segment SIZE we use current-period value (shows proportion of the total)
    rather than delta magnitude, so the chart is readable even when YoY change
    is small.  Delta is only used as a last-resort fallback.
    """
    def _seg_val(d):
        return (d.get("value")
                or d.get("current")
                or abs(d.get("delta") or 0))

    # Filter to dimension members that have a meaningful value
    candidates = [d for d in all_drivers if _seg_val(d) > 0]

    # Prefer members with positive momentum for a "contribution" donut;
    # fall back to all candidates sorted by absolute size
    pos = [d for d in candidates if (d.get("delta") or 0) >= 0]
    items = sorted(pos or candidates, key=_seg_val, reverse=True)[:top_n]

    labels = [str(d.get("member", "?"))[:18] for d in items]
    values = [round(_seg_val(d), 2) for d in items]
    return labels, values


def _donut_from_kpis(kpis: dict, show_kpis: list):
    selected = [k for k in show_kpis if k in kpis][:6] or list(kpis.keys())[:6]
    pairs = []
    for k in selected:
        kd  = kpis[k]
        raw = kd.get("value", kd.get("value_fmt", 0)) or 0
        try:
            v = abs(float(str(raw).replace(",","").replace("$","").replace("%","").strip()))
        except (ValueError, TypeError):
            v = 0.0
        if v > 0:
            pairs.append((k[:20], v))
    if not pairs:
        return [], []
    labels, values = zip(*pairs)
    return list(labels), list(values)


# ══════════════════════════════════════════════════════════════════════════════
# SHARED BLOCKS
# ══════════════════════════════════════════════════════════════════════════════

def _masthead(payload: dict, role: dict, pal: dict, accent: str,
              filter_html: str = "") -> str:
    badge     = _e(role.get("badge","ARIA · BRIEFING"))
    ref_date  = _fmt_date(payload.get("reference_date",""))
    ws        = payload.get("window_start","")
    tf        = payload.get("timeframe","1d")
    company   = role.get("company_name","")
    range_lbl = ""
    if tf != "1d" and ws and ws != payload.get("reference_date",""):
        range_lbl = (f'<span class="date-range"> &nbsp;'
                     f'{_e(_fmt_date_short(ws))} → {_e(_fmt_date_short(payload.get("reference_date","")))} '
                     f'</span>')

    logo_html = _company_logo_html(company)

    return f"""
<div class="mast">
  <div class="mast-left">
    <span class="aria-logo">ARIA</span>{logo_html}
  </div>
  <div class="mast-mid">{badge}</div>
  <div class="mast-right">
    {filter_html}
    <div>
      <div class="date-lbl">{ref_date}</div>
      {range_lbl}
    </div>
  </div>
</div>"""


def _footer(narrative, accent: str, pal: dict) -> str:
    notes = _strip_md(getattr(narrative,"speaker_notes","") or "")
    notes = _e(notes[:200] + ("…" if len(notes)>200 else ""))
    return f"""
<div class="footer">
  <div class="footer-lbl">Speaker Notes · What the Board Will Ask</div>
  <div class="footer-text">{notes}</div>
</div>"""


def _action_box(narrative, role: dict, pal: dict, accent: str) -> str:
    act   = _strip_md(getattr(narrative,"recommended_action","") or "")
    act   = _e(act[:200])
    owner = _e(role.get("title","Team"))
    return f"""
<div class="action-box">
  <div class="action-lbl">Recommended Action</div>
  <div class="action-text">{act}</div>
  <div class="action-meta">Owner: {owner} · By EOW</div>
</div>"""


def _filter_panel(dim_groups: dict) -> str:
    """Dropdown selects for up to 4 dimensions."""
    if not dim_groups:
        return ""
    sels = ""
    dims = list(dim_groups.keys())[:4]
    for dim in dims:
        members = dim_groups[dim]["labels"]
        opts    = f'<option value="All">{_e(dim)}: All</option>'
        for m in members:
            opts += f'<option value="{_e(m)}">{_e(m)}</option>'
        sels += f'<select class="filter-sel" data-dim="{_e(dim)}" onchange="ariaFilter(this)">{opts}</select> '
    return f'<div class="filter-panel"><span class="filter-lbl">View by</span> {sels}</div>'


def _narrative_section(narrative, pal: dict, accent: str,
                        kpis: dict, role_kpis: list, tier: str) -> str:
    """3-panel narrative: What's Happening | Key Drivers | Action."""
    summary = _strip_md(getattr(narrative,"exec_summary","") or "")
    drivers = _strip_md(getattr(narrative,"drivers_md","") or "")
    action  = _strip_md(getattr(narrative,"recommended_action","") or "")

    # Tone label by tier
    tone_labels = {
        "c_suite":    ("Strategic Overview","Boardroom Signals","Executive Directive"),
        "leadership": ("Performance Review","Root-Cause Drivers","Leadership Priority"),
        "management": ("Operational Status","What Moved the Number","Team Action"),
    }
    wh_lbl, dr_lbl, ac_lbl = tone_labels.get(tier, tone_labels["leadership"])

    # Inline stat pills for "What's Happening"
    pills = ""
    for kn in [k for k in role_kpis if k in kpis][:4]:
        kd = kpis[kn]; ms, mc = _fmt_pct(kd.get("mom_pct")); ys, yc = _fmt_pct(kd.get("yoy_pct"))
        if ms != "—":
            pills += (f'<span class="stat-pill" style="background:{mc}22;border-color:{mc}66;color:{mc}">'
                      f'{_e(kn)}: {_e(ms)} MoM</span>')
        if ys != "—":
            pills += (f'<span class="stat-pill" style="background:{yc}22;border-color:{yc}66;color:{yc}">'
                      f'YoY {_e(ys)}</span>')

    # Driver bullets (from drivers_md or parsed list)
    drv_lines = [l.strip().lstrip("•-*123456789. ") for l in drivers.split("\n") if l.strip()][:4]
    drv_html  = "".join(f'<div style="margin-bottom:5px;font-size:10px;color:{pal["subtext"]};">'
                         f'<span style="color:{accent};margin-right:4px">▶</span>{_e(l)}</div>'
                         for l in drv_lines) or f'<div class="narr">{_e(summary[:120])}</div>'

    # Action lines
    act_lines = [l.strip().lstrip("•-*123456789. ") for l in action.split("\n") if l.strip()][:3]
    if not act_lines:
        act_lines = [action[:150]] if action else ["Review performance with team."]
    act_html  = "".join(f'<div style="margin-bottom:6px;font-size:10px;color:{pal["subtext"]};">'
                         f'<span style="color:{accent};font-weight:700;margin-right:5px">▶</span>{_e(l)}</div>'
                         for l in act_lines)

    border = pal["border"]
    return f"""
<div style="display:flex;gap:0;margin-top:14px;border-top:1px solid {border};padding-top:14px">
  <div style="flex:1;padding-right:14px;border-right:1px solid {border}">
    <div class="sec">{_e(wh_lbl)}</div>
    <div class="ed-stat-row">{pills}</div>
    <div class="narr">{_e(summary[:280])}</div>
  </div>
  <div style="flex:1;padding:0 14px;border-right:1px solid {border}">
    <div class="sec">{_e(dr_lbl)}</div>
    {drv_html}
  </div>
  <div style="flex:1;padding-left:14px">
    <div class="sec">{_e(ac_lbl)}</div>
    {act_html}
  </div>
</div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 1 — EDITORIAL  (Newsletter / Magazine)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_editorial(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color","#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    tier        = _role_tier(role)
    sparkline   = payload.get("daily_sales_30d",[])

    pk     = kpis.get(prim, {})
    pval   = _e(pk.get("value_fmt","—"))
    ms, mc = _fmt_pct(pk.get("mom_pct"))
    ys, yc = _fmt_pct(pk.get("yoy_pct"))
    ws, wc = _fmt_pct(pk.get("wow_pct"))

    headline = _strip_md(getattr(narrative,"headline","") or "")
    sub      = _strip_md(getattr(narrative,"exec_summary","") or "")
    sub_1st  = (sub.split(".")[0]+".") if "." in sub else sub[:120]

    # Domain icon
    icon_html = _domain_icon_svg(role, accent, 64)

    # Stat highlights for hero strip
    def _pill(label, val, color):
        if val == "—": return ""
        return (f'<div style="display:inline-flex;flex-direction:column;'
                f'align-items:center;background:{color}15;border:1px solid {color}50;'
                f'border-radius:6px;padding:6px 10px;margin-right:8px;">'
                f'<div style="font-size:7px;font-weight:700;letter-spacing:2px;'
                f'color:{pal["muted"]};text-transform:uppercase">{_e(label)}</div>'
                f'<div style="font-size:14px;font-weight:800;color:{color}">{_e(val)}</div>'
                f'</div>')

    stat_strip = (
        f'<div style="font-size:9px;font-weight:700;color:{pal["muted"]};letter-spacing:2px;'
        f'text-transform:uppercase;margin-bottom:6px">{_e(prim)}</div>'
        f'<div style="font-size:34px;font-weight:800;color:{pal["text"]};'
        f'font-family:Georgia,serif;line-height:1.0;margin-bottom:8px">{pval}</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:4px">'
        + _pill("MoM", ms, mc)
        + _pill("YoY", ys, yc)
        + (_pill("WoW", ws, wc) if ws != "—" else "")
        + '</div>'
    )

    # Secondary KPIs (small row under hero)
    sec_kpis = [k for k in role_kpis if k != prim and k in kpis][:4]
    sec_html = ""
    for sk in sec_kpis:
        sd  = kpis[sk]; sm, smc = _fmt_pct(sd.get("mom_pct"))
        sec_html += (f'<div style="border-left:3px solid {accent};padding-left:8px;'
                     f'margin-right:14px;">'
                     f'<div style="font-size:7px;color:{pal["muted"]};letter-spacing:1px;'
                     f'text-transform:uppercase">{_e(sk)}</div>'
                     f'<div style="font-size:12px;font-weight:700;color:{pal["text"]};'
                     f'font-family:Georgia,serif">{_e(sd.get("value_fmt","—"))}</div>'
                     f'<div style="font-size:8px;font-weight:600;color:{smc}">{_e(sm)} MoM</div>'
                     f'</div>')

    # Dimension bar chart — up to 4 switchable dimensions
    dim_gs    = _dim_groups(all_drivers, 4, 8)
    first_dim = next(iter(dim_gs), None)
    bar_html  = ""
    if first_dim:
        d = dim_gs[first_dim]
        n = len(d["labels"])
        h = min(max(n * 28 + 20, 80), 220)
        # Pill selector — one pill per detected dimension
        _accent = accent
        _pills = ""
        for _i, _dk in enumerate(dim_gs.keys()):
            _active = "ed-dim-pill-active" if _i == 0 else ""
            _pills += (f'<button class="ed-dim-pill {_active}" '
                       f'data-dim="{_e(_dk)}" '
                       f'onclick="ariaEdDim(this)" '
                       f'style="--ed-accent:{_accent}">'
                       f'{_e(_dk)}</button> ')
        bar_html = (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'flex-wrap:wrap;gap:4px;margin-top:10px;margin-bottom:6px">'
            f'<span id="ed-dim-title" class="sec" style="margin-bottom:0">'
            f'{_e(first_dim)} Breakdown</span>'
            f'<div style="display:flex;flex-wrap:wrap;gap:3px">{_pills}</div>'
            f'</div>'
            f'<div class="chart-box" style="height:{h}px">'
            f'<canvas id="ed_bar"></canvas></div>'
        )

    return f"""
<div class="card">
  {_masthead(payload, role, pal, accent)}
  <!-- Hero strip -->
  <div class="ed-hero">
    {icon_html}
    <div style="flex:1">
      <div class="ed-headline">{_e(headline)}</div>
      <div class="ed-sub">{_e(sub_1st)}</div>
    </div>
    <div style="flex:0 0 180px;padding-left:16px;border-left:1px solid {pal['border']}">
      {stat_strip}
    </div>
  </div>
  <!-- Secondary KPI row -->
  {f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid {pal["border"]}">{sec_html}</div>' if sec_html else ''}
  <!-- Narrative columns + bar chart -->
  <div style="display:flex;gap:0;margin-top:2px">
    <div style="flex:1;padding-right:14px;border-right:1px solid {pal['border']}">
      {_narrative_section(narrative, pal, accent, kpis, role_kpis, tier)}
    </div>
    <div style="flex:0 0 220px;padding-left:16px">
      {bar_html}
    </div>
  </div>
  {_footer(narrative, accent, pal)}
</div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 2 — SCORECARD  (KPI grid + sparklines + filters)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_scorecard(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color","#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    show_kpis   = ([k for k in role_kpis if k in kpis] or list(kpis.keys()))[:9]
    tier        = _role_tier(role)
    sparkline   = payload.get("daily_sales_30d",[])
    dim_gs      = _dim_groups(all_drivers, 4, 8)

    # KPI tiles with sparklines
    tiles_html = ""
    for i, kn in enumerate(show_kpis):
        kd   = kpis[kn]
        val  = _e(kd.get("value_fmt","—"))
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        ys, yc = _fmt_pct(kd.get("yoy_pct"))
        ws, wc = _fmt_pct(kd.get("wow_pct"))
        rag  = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        target_delta = kd.get("target_pct")
        target_html  = ""
        if target_delta is not None:
            ts, tc_ = _fmt_pct(target_delta)
            target_html = (f'<div class="kpi-d" style="color:{tc_};font-size:8px">'
                           f'vs Target {_e(ts)}</div>')
        val_cls = "sm" if len(str(val)) > 8 else ""
        has_spark = len(sparkline) >= 4
        spark_id  = f"sc_spark_{i}"
        tiles_html += f"""
<div class="kpi-tile">
  <div class="kpi-rag" style="background:{rag}"></div>
  <div class="kpi-name">{_e(kn)}</div>
  <div class="kpi-val {val_cls}">{val}</div>
  {f'<div class="kpi-spark"><canvas id="{spark_id}"></canvas></div>' if has_spark else ''}
  <div class="kpi-d" style="color:{mc}">{_e(ms)} MoM</div>
  <div class="kpi-d" style="color:{yc}">{_e(ys)} YoY</div>
  {f'<div class="kpi-d" style="color:{wc}">{_e(ws)} WoW</div>' if ws != "—" else ''}
  {target_html}
</div>"""

    # Dimension breakdown chart (switches on filter)
    first_dim = next(iter(dim_gs), None)
    dim_chart_html = ""
    if first_dim:
        d = dim_gs[first_dim]
        n = len(d["labels"])
        dim_chart_html = f"""
<div style="margin-top:14px;border-top:1px solid {pal['border']};padding-top:12px">
  <div id="sc-dim-title" class="sec">{_e(first_dim)} Breakdown</div>
  <div class="chart-box" style="height:{min(max(n*28+20,80),220)}px">
    <canvas id="sc_dimbar"></canvas>
  </div>
</div>"""

    return f"""
<div class="card">
  {_masthead(payload, role, pal, accent, filter_html=_filter_panel(dim_gs))}
  <div class="sec">Performance Scorecard</div>
  <div class="kpi-grid">{tiles_html}</div>
  {dim_chart_html}
  {_narrative_section(narrative, pal, accent, kpis, role_kpis, tier)}
  {_footer(narrative, accent, pal)}
</div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 3 — DOSSIER  (4-chart analytics deep dive)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_dossier(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color","#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    show_kpis   = ([k for k in role_kpis if k in kpis] or list(kpis.keys()))[:5]
    tier        = _role_tier(role)
    sparkline   = payload.get("daily_sales_30d",[])
    dim_gs      = _dim_groups(all_drivers, 4, 8)

    # ─ Mini KPI row ──────────────────────────────────────────────────────── #
    mini_tiles = ""
    for kn in show_kpis:
        kd   = kpis[kn]
        val  = _e(kd.get("value_fmt","—"))
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        ys, yc = _fmt_pct(kd.get("yoy_pct"))
        rag  = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        mini_tiles += f"""
<div class="mkpi" style="border-top:2px solid {rag}">
  <div class="mkpi-name">{_e(kn)}</div>
  <div class="mkpi-val">{val}</div>
  <div class="mkpi-d" style="color:{mc}">{_e(ms)} MoM</div>
  <div class="mkpi-d" style="color:{yc}">{_e(ys)} YoY</div>
</div>"""

    # ─ Chart grid ────────────────────────────────────────────────────────── #
    first_dim = next(iter(dim_gs), None)
    ch_h = 160  # chart height

    # Top-left: line trend
    trend_html = ""
    if len(sparkline) >= 4:
        trend_html = f"""
<div class="chart-box" style="height:{ch_h}px">
  <canvas id="ds_trend"></canvas>
</div>"""
    else:
        trend_html = f'<div style="height:{ch_h}px;display:flex;align-items:center;justify-content:center;color:{pal["muted"]};font-size:10px">No trend data</div>'

    # Top-right: dimension bar (filterable)
    dim_bar_html = ""
    if first_dim:
        d = dim_gs[first_dim]
        n = len(d["labels"])
        dim_bar_html = f'<div class="chart-box" style="height:{ch_h}px"><canvas id="ds_dimbar"></canvas></div>'
    else:
        dim_bar_html = f'<div style="height:{ch_h}px;display:flex;align-items:center;justify-content:center;color:{pal["muted"]};font-size:10px">No dimension data</div>'

    # Bottom-left: donut
    d_labels, d_values = _donut_data_from_drivers(all_drivers)
    if not d_values:
        d_labels, d_values = _donut_from_kpis(kpis, show_kpis)
    donut_html = ""
    if d_values:
        legend = "".join(
            f'<div style="display:flex;align-items:center;gap:5px;margin-bottom:3px">'
            f'<div style="width:7px;height:7px;border-radius:50%;background:{_palette(accent,len(d_labels))[i]};flex-shrink:0"></div>'
            f'<div style="font-size:8px;color:{pal["subtext"]};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100px">{_e(l)}</div></div>'
            for i, l in enumerate(d_labels)
        )
        donut_html = f"""
<div style="display:flex;gap:8px;align-items:flex-start">
  <div class="chart-box" style="height:{ch_h}px;flex:0 0 {ch_h}px">
    <canvas id="ds_donut"></canvas>
  </div>
  <div style="flex:1;overflow:hidden;padding-top:8px">{legend}</div>
</div>"""
    else:
        donut_html = f'<div style="height:{ch_h}px;display:flex;align-items:center;justify-content:center;color:{pal["muted"]};font-size:10px">No breakdown data</div>'

    # Bottom-right: waterfall
    wf_labels, wf_floats, wf_colors, _ = _waterfall_data(kpis, prim, all_drivers)
    waterfall_html = f'<div class="chart-box" style="height:{ch_h}px"><canvas id="ds_wfall"></canvas></div>'

    chart_grid = f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">
  <div style="background:{pal['surface']};border:1px solid {pal['border']};border-radius:8px;padding:12px">
    <div id="ds-trend-title" class="sec">Trend — {_e(prim)}</div>
    {trend_html}
  </div>
  <div style="background:{pal['surface']};border:1px solid {pal['border']};border-radius:8px;padding:12px">
    <div id="ds-dim-title" class="sec">{_e(f"{first_dim} Breakdown" if first_dim else "Dimension Breakdown")}</div>
    {dim_bar_html}
  </div>
  <div style="background:{pal['surface']};border:1px solid {pal['border']};border-radius:8px;padding:12px">
    <div class="sec">Contribution Mix</div>
    {donut_html}
  </div>
  <div style="background:{pal['surface']};border:1px solid {pal['border']};border-radius:8px;padding:12px">
    <div class="sec">Period-over-Period Waterfall</div>
    {waterfall_html}
  </div>
</div>"""

    return f"""
<div class="card">
  {_masthead(payload, role, pal, accent, filter_html=_filter_panel(dim_gs))}
  <div class="sec">Analytics Dossier</div>
  <div class="mkpi-row">{mini_tiles}</div>
  {chart_grid}
  {_narrative_section(narrative, pal, accent, kpis, role_kpis, tier)}
  {_footer(narrative, accent, pal)}
</div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "editorial": _tmpl_editorial,
    "scorecard": _tmpl_scorecard,
    "dossier":   _tmpl_dossier,
}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def generate_html_card(narrative, payload: dict, _config: dict,
                       role_config: Optional[dict] = None) -> str:
    """
    Returns a self-contained HTML string.  Same signature as the old
    generate_svg() so all call-sites work without changes.
    """
    role  = dict(role_config or _DEFAULT_ROLE)
    kpis  = payload.get("kpis", {})
    prim  = role.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), "—")
    role["primary_kpi"] = prim

    tmpl_key  = role.get("card_template","editorial")
    tmpl_fn   = TEMPLATES.get(tmpl_key, _tmpl_editorial)
    style_key = role.get("card_style","dark")
    pal       = dict(PALETTES.get(style_key, PALETTES["dark"]))
    accent    = role.get("accent_color","#F59E0B")

    prim_real, _, all_drivers = _resolve_kpis(payload, role)
    sparkline  = payload.get("daily_sales_30d",[])
    dim_gs     = _dim_groups(all_drivers, 4, 8)
    first_dim  = next(iter(dim_gs), None)

    # ── Build Chart.js init scripts ─────────────────────────────────────── #
    chart_js = "var ARIA_CHARTS = {};\n"

    if tmpl_key == "editorial":
        # Driver bar
        if first_dim:
            d = dim_gs[first_dim]
            chart_js += _chart_bar_h("ed_bar", d["labels"], d["values"], accent, pal)

    elif tmpl_key == "scorecard":
        # Sparklines per tile
        role_kpis = role.get("kpis", list(kpis.keys()))
        show_kpis = ([k for k in role_kpis if k in kpis] or list(kpis.keys()))[:9]
        if len(sparkline) >= 4:
            for i in range(len(show_kpis)):
                # Scale sparkline to this KPI's magnitude
                chart_js += _chart_sparkline(f"sc_spark_{i}", sparkline[-30:], accent)
        # Dimension bar (filterable)
        if first_dim:
            d = dim_gs[first_dim]
            chart_js += _chart_bar_v("sc_dimbar", d["labels"], d["values"], accent, pal)

    elif tmpl_key == "dossier":
        # Line trend
        if len(sparkline) >= 4:
            chart_js += _chart_line_trend("ds_trend", sparkline[-60:], accent, pal)
        # Dimension bar (filterable)
        if first_dim:
            d = dim_gs[first_dim]
            chart_js += _chart_bar_v("ds_dimbar", d["labels"], d["values"], accent, pal)
        # Donut
        d_labels, d_values = _donut_data_from_drivers(all_drivers)
        if not d_values:
            d_labels, d_values = _donut_from_kpis(kpis,
                ([k for k in role.get("kpis",[]) if k in kpis] or list(kpis.keys()))[:5])
        if d_values:
            chart_js += _chart_donut("ds_donut", d_labels, d_values, accent)
        # Waterfall
        wf_l, wf_f, wf_c, _ = _waterfall_data(kpis, prim_real, all_drivers)
        if wf_f:
            chart_js += _chart_waterfall("ds_wfall", wf_l, wf_f, wf_c, pal)

    # ── Interactive filter JS ────────────────────────────────────────────── #
    dim_data_json = json.dumps({d: dim_gs[d] for d in dim_gs})
    filter_js = ""
    if tmpl_key == "editorial" and first_dim:
        filter_js = f"""
var ARIA_DIM_DATA = {dim_data_json};
function ariaEdDim(pill) {{
    var dim = pill.getAttribute('data-dim');
    var d   = ARIA_DIM_DATA[dim];
    if (!d) return;
    var chart = ARIA_CHARTS['ed_bar'];
    if (!chart) return;
    chart.data.labels               = d.labels;
    chart.data.datasets[0].data     = d.values;
    chart.update('none');
    var t = document.getElementById('ed-dim-title');
    if (t) t.textContent = dim + ' Breakdown';
    document.querySelectorAll('.ed-dim-pill').forEach(function(p) {{
        p.classList.toggle('ed-dim-pill-active', p.getAttribute('data-dim') === dim);
    }});
}}"""
    elif tmpl_key in ("scorecard", "dossier"):
        bar_id    = "sc_dimbar" if tmpl_key == "scorecard" else "ds_dimbar"
        title_id  = "sc-dim-title" if tmpl_key == "scorecard" else "ds-dim-title"
        accent_e  = accent
        palette_js = json.dumps(_PALETTE8)
        filter_js = f"""
var ARIA_ACCENT    = '{accent_e}';
var ARIA_PALETTE   = {palette_js};
var ARIA_DIM_DATA  = {dim_data_json};
function ariaFilter(sel) {{
    var dim = sel.getAttribute('data-dim');
    var val = sel.value;
    var d   = ARIA_DIM_DATA[dim];
    if (!d) return;
    var chart = ARIA_CHARTS['{bar_id}'];
    if (chart) {{
        // Switch chart to the selected dimension
        chart.data.labels = d.labels;
        chart.data.datasets[0].data = d.values;

        // Highlight the selected member; dim the rest
        if (val === 'All') {{
            chart.data.datasets[0].backgroundColor =
                d.labels.map(function(l, i) {{ return ARIA_PALETTE[i % ARIA_PALETTE.length]; }});
        }} else {{
            chart.data.datasets[0].backgroundColor =
                d.labels.map(function(l) {{
                    return (l === val) ? ARIA_ACCENT : ARIA_ACCENT + '28';
                }});
        }}
        chart.update('none');

        var t = document.getElementById('{title_id}');
        if (t) t.textContent = dim + ' Breakdown' + (val !== 'All' ? ' • ' + val : '');
    }}

    // Update filter chip for THIS dropdown only (remove old, add new)
    var existingChip = sel.parentNode.querySelector('.filter-chip[data-dim="' + dim + '"]');
    if (existingChip) existingChip.remove();
    if (val !== 'All') {{
        var chip = document.createElement('span');
        chip.className = 'filter-chip';
        chip.setAttribute('data-dim', dim);
        chip.style.display = 'inline-block';
        chip.textContent = dim + ': ' + val;
        sel.parentNode.insertBefore(chip, sel.nextSibling);
    }}
}}
"""

    # ── Build full HTML ──────────────────────────────────────────────────── #
    body = tmpl_fn(narrative, payload, role, pal)

    # Diagnostic summary embedded as HTML comment for deployment verification
    _drv_yoy = payload.get("drivers", {})
    _drv_dod = payload.get("drivers_dod", {})
    _dbg     = payload.get("_aria_debug", {})
    _yd = _dbg.get("yoy_diag", {})
    _diag = (
        f"all_drivers={len(all_drivers)} "
        f"dim_gs_keys={list(dim_gs.keys())} "
        f"first_dim={first_dim} "
        f"tmpl={tmpl_key} | "
        f"yoy_keys={list(_drv_yoy.keys())} "
        f"yoy_lens={[len(v) for v in _drv_yoy.values()]} | "
        f"dod_keys={list(_drv_dod.keys())} "
        f"dod_lens={[len(v) for v in _drv_dod.values()]} | "
        f"da_ver={_dbg.get('da_ver','UNKNOWN')} | "
        f"cfg_dims={_dbg.get('cfg_dims',[])} | "
        f"df_shape={_dbg.get('df_shape',[])} | "
        f"kpi_map={_dbg.get('kpi_map',[])} | "
        f"yoy_summary={_dbg.get('yoy_summary',{})} | "
        f"yoy_diag.effective_dims={_yd.get('effective_dims','MISSING')} | "
        f"yoy_diag.kpis={_yd.get('kpis_to_decompose','MISSING')} | "
        f"yoy_diag.curr_start={_yd.get('curr_start','?')} "
        f"yoy_diag.prior_start={_yd.get('prior_start','?')} "
        f"yoy_diag.prior_end={_yd.get('prior_end','?')} | "
        f"yoy_diag.curr_df_len={_yd.get('curr_df_len','?')} "
        f"yoy_diag.prior_df_len={_yd.get('prior_df_len','?')} | "
        f"yoy_diag.spot_kpi={_yd.get('spot_kpi','?')} "
        f"yoy_diag.spot_dim={_yd.get('spot_dim','?')} "
        f"yoy_diag.spot_cfg={_yd.get('spot_cfg','?')} "
        f"yoy_diag.spot_agg_len={_yd.get('spot_agg_len','?')} "
        f"yoy_diag.spot_agg_sample={_yd.get('spot_agg_sample','?')} | "
        f"df_cat_cols={_dbg.get('df_cat_cols',{})}"
    )

    return f"""<!DOCTYPE html>
<!-- ARIA_DEPLOY_VERSION=2026-06-12-v12 | {_diag} -->
<html>
<head>
<meta charset="utf-8">
<style>
{_base_css(pal, accent)}
</style>
</head>
<body>
{body}
<script src="{_CHARTJS}"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{
    {filter_js}
    {chart_js}
}});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PNG CONVERSION  (Selenium headless Chrome — unchanged from v2)
# ══════════════════════════════════════════════════════════════════════════════

def html_to_png(html_str: str, width: int = 900, height: int = 520,
                wait_ms: int = 1500) -> bytes:
    import os, tempfile, time
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError:
        raise RuntimeError("selenium not installed — run: pip install selenium")

    opts = Options()
    for arg in ["--headless=new","--no-sandbox","--disable-dev-shm-usage",
                "--disable-gpu",f"--window-size={width},{height}",
                "--hide-scrollbars","--force-device-scale-factor=1"]:
        opts.add_argument(arg)

    svc = None
    for path in ("/usr/bin/chromedriver","/usr/local/bin/chromedriver","chromedriver"):
        if os.path.isfile(path):
            svc = Service(path)
            break

    driver = webdriver.Chrome(service=svc, options=opts) if svc else webdriver.Chrome(options=opts)
    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w",
                                         encoding="utf-8") as f:
            f.write(html_str)
            tmp = f.name
        driver.get(f"file://{tmp}")
        time.sleep(wait_ms / 1000)
        # Fit viewport to card content
        card_h = driver.execute_script(
            "var c=document.querySelector('.card'); return c?c.scrollHeight:document.body.scrollHeight;")
        driver.set_window_size(width, max(card_h + 40, 200))
        time.sleep(0.3)
        png = driver.get_screenshot_as_png()
    finally:
        driver.quit()
        try:
            os.unlink(tmp)
        except Exception:
            pass
    return png
