"""
html_generator.py
-----------------
Generates ARIA briefing cards as self-contained HTML strings.

5 Templates:
  1. editorial      — Three-Act: KPIs + sparkline | drivers | action
  2. scorecard      — KPI grid tiles + donut chart
  3. story_arc      — Narrative-first + treemap
  4. ops_dashboard  — Traffic-light tiles + heatmap + movers
  5. board_pack     — Formal board slide

Charts via Chart.js (CDN):
  - Line / sparkline
  - Donut / pie
  - Horizontal bar (drivers)
  - Radar
  - Mixed (bar + line for vs-target)

PNG conversion via Playwright (headless Chromium).

Both the wizard preview (st.components.v1.html) and the agent
(Playwright → PNG → Slack) call generate_html_card() with the
same payload — guaranteed content parity.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

# ── CDN scripts (loaded inside each card) ────────────────────────────────── #
_CHARTJS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
_D3      = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"

# ── Style palettes ────────────────────────────────────────────────────────── #
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
    "driver_focus": [], "card_template": "editorial", "card_style": "dark",
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
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

def _resolve_kpis(payload: dict, role: dict):
    kpis = payload.get("kpis", {})
    prim = role.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), "—")
    drivers_dict = payload.get("drivers", {})
    all_drivers  = drivers_dict.get(prim) or []
    if not all_drivers:
        for v in drivers_dict.values():
            if v:
                all_drivers = v
                break
    return prim, kpis, all_drivers

def _rag_color(mom_pct, yoy_pct) -> str:
    v = yoy_pct if yoy_pct is not None else mom_pct
    if v is None: return "#94A3B8"
    if v >= 0.05: return "#34D399"
    if v >= 0:    return "#FBBF24"
    return "#F87171"

def _driver_chart_data(all_drivers: list, top_n: int = 6) -> tuple[list, list, list]:
    """Return (labels, values, colors) for top movers bar chart."""
    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:top_n//2+1]
    drags = sorted([d for d in all_drivers if d.get("delta", 0) < 0],
                   key=lambda d: d["delta"])[:top_n//2]
    items  = lifts + drags
    labels = [f"{d.get('dimension','')} · {d.get('member','')}"[:28] for d in items]
    values = [d.get("delta", 0) for d in items]
    colors = ["#34D399" if v >= 0 else "#F87171" for v in values]
    return labels, values, colors

def _donut_data(all_drivers: list, top_n: int = 5) -> tuple[list, list]:
    items = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:top_n]
    if not items:
        items = sorted(all_drivers, key=lambda d: abs(d.get("delta", 0)),
                       reverse=True)[:top_n]
    labels = [f"{d.get('member','?')}"[:18] for d in items]
    values = [abs(d.get("delta", 0)) for d in items]
    return labels, values


# ══════════════════════════════════════════════════════════════════════════════
# BASE CSS  (shared across all templates)
# ══════════════════════════════════════════════════════════════════════════════

def _base_css(pal: dict, accent: str) -> str:
    return f"""
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{
        background:{pal['bg']}; color:{pal['text']};
        font-family:-apple-system,BlinkMacSystemFont,'Inter',Arial,sans-serif;
        font-size:13px; line-height:1.4;
        width:900px; min-height:500px;
        overflow:hidden;
    }}
    .card {{ width:900px; min-height:500px; background:{pal['bg']};
             display:flex; flex-direction:column; padding:20px 28px 0; }}

    /* Masthead */
    .masthead {{ display:flex; justify-content:space-between; align-items:flex-start;
                 border-bottom:1px solid {pal['border']}; padding-bottom:10px; margin-bottom:14px; }}
    .masthead-left .aria-logo {{ color:{accent}; font-size:10px; font-weight:800;
                                  letter-spacing:4px; }}
    .masthead-center {{ text-align:center; color:{pal['muted']}; font-size:8px;
                        letter-spacing:3px; font-weight:600; }}
    .masthead-right {{ text-align:right; }}
    .date-main {{ color:{pal['muted']}; font-size:9px; letter-spacing:2px; font-weight:600; }}
    .date-range {{ color:{pal['muted']}; font-size:8px; opacity:0.7; margin-top:2px; }}

    /* Headline */
    .headline {{ font-size:17px; font-weight:700; color:{pal['text']};
                 font-family:Georgia,serif; margin-bottom:6px; line-height:1.3; }}
    .subheadline {{ font-size:11px; color:{pal['muted']}; font-style:italic;
                    margin-bottom:12px; }}
    .accent-rule {{ width:44px; height:2px; background:{accent}; margin:8px 0 14px; }}

    /* KPI tiles */
    .kpi-grid {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }}
    .kpi-tile {{ flex:1 1 160px; background:{pal['surface']}; border:1px solid {pal['border']};
                 border-radius:8px; padding:12px 14px; min-width:140px; max-width:240px;
                 position:relative; }}
    .kpi-tile.large {{ flex:1 1 220px; }}
    .kpi-rag {{ position:absolute; top:10px; right:10px; width:9px; height:9px;
                border-radius:50%; }}
    .kpi-name {{ font-size:8px; font-weight:700; letter-spacing:1.5px;
                 color:{pal['muted']}; margin-bottom:6px; text-transform:uppercase; }}
    .kpi-value {{ font-size:22px; font-weight:700; color:{pal['text']};
                  font-family:Georgia,serif; margin-bottom:4px; }}
    .kpi-value.small {{ font-size:17px; }}
    .kpi-delta {{ font-size:10px; font-weight:600; }}

    /* Section labels */
    .section-label {{ font-size:8px; font-weight:700; letter-spacing:3px;
                      color:{accent}; margin-bottom:8px; text-transform:uppercase; }}

    /* Action box */
    .action-box {{ background:{accent}1A; border:1px solid {accent}40;
                   border-radius:8px; padding:14px 16px; }}
    .action-label {{ font-size:7px; font-weight:700; letter-spacing:4px;
                     color:{accent}; margin-bottom:8px; }}
    .action-text {{ font-size:12px; font-weight:700; color:{pal['text']};
                    line-height:1.4; margin-bottom:6px; }}
    .action-meta {{ font-size:9px; color:{pal['muted']}; }}

    /* Footer / speaker notes */
    .footer {{ background:{pal['footer_bg']}; margin:14px -28px 0;
               padding:12px 28px; border-top:1px solid {pal['border']}; }}
    .footer-label {{ font-size:7px; font-weight:700; letter-spacing:4px;
                     color:{accent}; margin-bottom:6px; }}
    .footer-text {{ font-size:10px; color:{pal['subtext']}; font-style:italic;
                    font-family:Georgia,serif; opacity:0.85; }}

    /* Top movers list */
    .mover-row {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
    .mover-bar-wrap {{ flex:1; background:{pal['surface2']}; border-radius:3px; height:12px; }}
    .mover-bar {{ height:12px; border-radius:3px; min-width:4px; }}
    .mover-label {{ font-size:9px; font-weight:600; color:{pal['text']}; width:160px;
                    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .mover-value {{ font-size:9px; font-weight:700; width:60px; text-align:right; }}

    /* Chart containers */
    .chart-wrap {{ position:relative; }}
    canvas {{ display:block; }}
    """


# ══════════════════════════════════════════════════════════════════════════════
# SHARED BLOCK RENDERERS
# ══════════════════════════════════════════════════════════════════════════════

def _block_masthead(payload: dict, role: dict, pal: dict, accent: str) -> str:
    badge    = _e(role.get("badge", "ARIA · BRIEFING"))
    ref_date = _fmt_date(payload.get("reference_date", ""))
    ws       = payload.get("window_start", "")
    tf       = payload.get("timeframe", "1d")
    range_html = ""
    if tf != "1d" and ws and ws != payload.get("reference_date", ""):
        range_html = (f'<div class="date-range">'
                      f'{_e(_fmt_date_short(ws))} → {_e(_fmt_date_short(payload.get("reference_date","")))} '
                      f'</div>')
    return f"""
    <div class="masthead">
        <div class="masthead-left"><div class="aria-logo">ARIA</div></div>
        <div class="masthead-center">{badge}</div>
        <div class="masthead-right">
            <div class="date-main">{ref_date}</div>
            {range_html}
        </div>
    </div>"""


def _block_headline(narrative, pal: dict) -> str:
    hl  = _e(_strip_md(getattr(narrative, "headline", "") or ""))
    sub = _strip_md(getattr(narrative, "exec_summary", "") or "")
    sub = _e((sub.split(".")[0] + ".") if "." in sub else sub[:120])
    return f"""
    <div class="headline">{hl}</div>
    <div class="subheadline">{sub}</div>
    <div class="accent-rule"></div>"""


def _block_kpi_tiles(kpis: dict, role_kpi_names: list, pal: dict,
                      accent: str, large: bool = False) -> str:
    show = [k for k in role_kpi_names if k in kpis] or list(kpis.keys())[:6]
    tiles = ""
    for name in show:
        kd   = kpis[name]
        val  = _e(kd.get("value_fmt", "—"))
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        rag  = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        size_cls = "large" if large else ""
        val_cls  = "small" if len(str(val)) > 8 else ""
        tiles += f"""
        <div class="kpi-tile {size_cls}">
            <div class="kpi-rag" style="background:{rag}"></div>
            <div class="kpi-name">{_e(name)}</div>
            <div class="kpi-value {val_cls}">{val}</div>
            <div class="kpi-delta" style="color:{mc}">{_e(ms)} MoM</div>
        </div>"""
    return f'<div class="kpi-grid">{tiles}</div>'


def _block_action(narrative, role: dict, pal: dict, accent: str) -> str:
    act  = _strip_md(getattr(narrative, "recommended_action", "") or "")
    act  = _e(act[:200])
    owner = _e(role.get("title", "Team"))
    return f"""
    <div class="action-box">
        <div class="action-label">Recommended Action</div>
        <div class="action-text">{act}</div>
        <div class="action-meta">Owner: {owner} · By EOW</div>
    </div>"""


def _block_footer(narrative, accent: str, pal: dict) -> str:
    notes = _strip_md(getattr(narrative, "speaker_notes", "") or "")
    notes = _e(notes[:180] + ("…" if len(notes) > 180 else ""))
    return f"""
    <div class="footer">
        <div class="footer-label">Speaker Notes · What the Board Will Ask</div>
        <div class="footer-text">{notes}</div>
    </div>"""


def _block_top_movers(all_drivers: list, pal: dict, accent: str,
                       max_rows: int = 5) -> str:
    lifts = sorted([d for d in all_drivers if d.get("delta", 0) > 0],
                   key=lambda d: d["delta"], reverse=True)[:max_rows]
    drags = sorted([d for d in all_drivers if d.get("delta", 0) < 0],
                   key=lambda d: d["delta"])[:2]
    all_items = (lifts + drags)[:max_rows]
    if not all_items:
        return '<div style="color:#64748B;font-size:10px;padding:8px 0">No driver data available</div>'
    max_abs = max(abs(d.get("delta", 0)) for d in all_items) or 1
    rows = ""
    for d in all_items:
        delta = d.get("delta", 0)
        col   = "#34D399" if delta >= 0 else "#F87171"
        sign  = "+" if delta >= 0 else "−"
        lbl   = f"{d.get('dimension','')} {d.get('member','')}"[:26]
        pct   = int(abs(delta) / max_abs * 100)
        # format delta
        av = abs(delta)
        if av >= 1_000_000: dfmt = f"${delta/1_000_000:.1f}M"
        elif av >= 1_000:   dfmt = f"${delta/1_000:.1f}K"
        else:               dfmt = f"${delta:,.0f}"
        rows += f"""
        <div class="mover-row">
            <div class="mover-label">{_e(lbl)}</div>
            <div class="mover-bar-wrap">
                <div class="mover-bar" style="width:{pct}%;background:{col}"></div>
            </div>
            <div class="mover-value" style="color:{col}">{sign}{_e(dfmt)}</div>
        </div>"""
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# CHART SCRIPTS
# ══════════════════════════════════════════════════════════════════════════════

def _chart_donut(canvas_id: str, labels: list, values: list,
                 accent: str, center_text: str = "") -> str:
    palette = [accent, "#34D399", "#60A5FA", "#F87171", "#A78BFA",
               "#FBBF24", "#F97316", "#14B8A6"]
    colors  = (palette * 3)[:len(values)]
    lj = json.dumps(labels)
    vj = json.dumps(values)
    cj = json.dumps(colors)
    return f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'doughnut',
        data: {{ labels: {lj}, datasets: [{{
            data: {vj}, backgroundColor: {cj},
            borderWidth: 2, borderColor: 'transparent',
            hoverOffset: 4
        }}] }},
        options: {{
            cutout: '60%', responsive:true, maintainAspectRatio:false,
            plugins: {{
                legend: {{ display:false }},
                tooltip: {{ callbacks: {{
                    label: ctx => ' ' + ctx.label + ': ' +
                        (ctx.raw >= 1000000
                            ? '$' + (ctx.raw/1000000).toFixed(1) + 'M'
                            : ctx.raw >= 1000
                                ? '$' + (ctx.raw/1000).toFixed(1) + 'K'
                                : '$' + ctx.raw.toFixed(0))
                }}}}
            }},
            animation: {{ duration: 0 }}
        }}
    }});"""


def _chart_sparkline(canvas_id: str, values: list, accent: str) -> str:
    vj = json.dumps(values)
    lj = json.dumps([str(i) for i in range(len(values))])
    return f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{ labels: {lj}, datasets: [{{
            data: {vj}, borderColor: '{accent}', borderWidth: 2,
            pointRadius: 0, fill: true,
            backgroundColor: '{accent}22',
            tension: 0.4
        }}] }},
        options: {{
            responsive:true, maintainAspectRatio:false,
            plugins: {{ legend:{{display:false}}, tooltip:{{enabled:false}} }},
            scales: {{
                x: {{ display:false }},
                y: {{ display:false }}
            }},
            animation: {{ duration:0 }}
        }}
    }});"""


def _chart_bar_horizontal(canvas_id: str, labels: list, values: list,
                           colors: list, pal: dict) -> str:
    lj = json.dumps(labels)
    vj = json.dumps(values)
    cj = json.dumps(colors)
    tc = pal['muted']
    return f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'bar',
        data: {{ labels: {lj}, datasets: [{{
            data: {vj}, backgroundColor: {cj},
            borderRadius: 3, borderWidth: 0
        }}] }},
        options: {{
            indexAxis: 'y', responsive:true, maintainAspectRatio:false,
            plugins: {{ legend:{{display:false}},
                tooltip:{{ callbacks:{{ label: ctx => ' $' +
                    (Math.abs(ctx.raw)>=1000000
                        ? (ctx.raw/1000000).toFixed(1)+'M'
                        : Math.abs(ctx.raw)>=1000
                            ? (ctx.raw/1000).toFixed(1)+'K'
                            : ctx.raw.toFixed(0)) }} }}
            }},
            scales: {{
                x: {{ display:false, grid:{{display:false}} }},
                y: {{ ticks:{{ color:'{tc}', font:{{size:9}} }},
                      grid:{{display:false}}, border:{{display:false}} }}
            }},
            animation: {{ duration:0 }}
        }}
    }});"""


def _chart_line_vs_target(canvas_id: str, actuals: list, targets: list,
                           accent: str, pal: dict) -> str:
    lj  = json.dumps([str(i) for i in range(len(actuals))])
    aj  = json.dumps(actuals)
    tj  = json.dumps(targets)
    tc  = pal['muted']
    return f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'bar',
        data: {{
            labels: {lj},
            datasets: [
                {{ type:'bar', label:'Actual', data:{aj},
                   backgroundColor:'{accent}88', borderRadius:3, borderWidth:0 }},
                {{ type:'line', label:'Target', data:{tj},
                   borderColor:'#F87171', borderWidth:2,
                   pointRadius:0, fill:false, tension:0.3 }}
            ]
        }},
        options: {{
            responsive:true, maintainAspectRatio:false,
            plugins: {{ legend:{{ labels:{{ color:'{tc}', font:{{size:9}} }} }} }},
            scales: {{
                x: {{ display:false }},
                y: {{ ticks:{{ color:'{tc}', font:{{size:8}} }},
                      grid:{{ color:'{pal["border"]}' }},
                      border:{{ display:false }} }}
            }},
            animation: {{ duration:0 }}
        }}
    }});"""


def _chart_radar(canvas_id: str, labels: list, values: list,
                 accent: str, pal: dict) -> str:
    lj = json.dumps(labels)
    vj = json.dumps(values)
    tc = pal['muted']
    return f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'radar',
        data: {{ labels: {lj}, datasets: [{{
            data: {vj}, borderColor:'{accent}', borderWidth:2,
            backgroundColor:'{accent}33', pointBackgroundColor:'{accent}',
            pointRadius:3
        }}] }},
        options: {{
            responsive:true, maintainAspectRatio:false,
            plugins: {{ legend:{{display:false}} }},
            scales: {{ r: {{
                ticks:{{ display:false }},
                grid:{{ color:'{pal["border"]}' }},
                pointLabels:{{ color:'{tc}', font:{{size:8}} }},
                angleLines:{{ color:'{pal["border"]}' }}
            }} }},
            animation: {{ duration:0 }}
        }}
    }});"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 1 — EDITORIAL  (Three-Act)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_editorial(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent       = role.get("accent_color", "#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis    = role.get("kpis", list(kpis.keys()))
    sparkline    = payload.get("daily_sales_30d", [])

    # Secondary KPIs for Act I sidebar
    sec_kpis = [k for k in role_kpis if k != prim and k in kpis][:2]
    pk       = kpis.get(prim, {})
    pval     = _e(pk.get("value_fmt", "—"))
    ms, mc   = _fmt_pct(pk.get("mom_pct"))
    ys, _    = _fmt_pct(pk.get("yoy_pct"))

    # Drivers chart data
    d_labels, d_values, d_colors = _driver_chart_data(all_drivers, 6)
    has_drivers = bool(d_labels)

    sec_html = ""
    for sk in sec_kpis:
        sd = kpis[sk]
        sm, smc = _fmt_pct(sd.get("mom_pct"))
        sec_html += f"""
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid {pal['border']}">
            <div style="font-size:8px;color:{pal['muted']};letter-spacing:1px;
                        text-transform:uppercase;margin-bottom:3px">{_e(sk)}</div>
            <div style="font-size:14px;font-weight:700;color:{pal['text']};
                        font-family:Georgia,serif">{_e(sd.get('value_fmt','—'))}</div>
            <div style="font-size:9px;font-weight:600;color:{smc}">{_e(sm)} MoM</div>
        </div>"""

    act1_trend = ""
    if len(sparkline) >= 4:
        act1_trend = f"""
        <div style="margin-top:12px">
            <div class="section-label">30-Day {_e(prim)} Trend</div>
            <div class="chart-wrap" style="height:48px">
                <canvas id="spark1"></canvas>
            </div>
        </div>"""

    drivers_html = ""
    if has_drivers:
        drivers_html = f"""
        <div class="chart-wrap" style="height:{min(30*len(d_labels)+20, 180)}px">
            <canvas id="drvchart"></canvas>
        </div>"""
    else:
        drivers_html = '<div style="color:#64748B;font-size:10px;padding-top:8px">No driver data</div>'

    return f"""
    <div class="card">
        {_block_masthead(payload, role, pal, accent)}
        {_block_headline(narrative, pal)}
        <div style="display:flex;gap:16px;flex:1">
            <!-- Act I: KPI + sparkline -->
            <div style="flex:0 0 230px;border-right:1px solid {pal['border']};padding-right:16px">
                <div class="section-label">Act I · Where We Are</div>
                <div style="font-size:8px;color:{pal['muted']};letter-spacing:1px;
                            text-transform:uppercase;margin-bottom:4px">{_e(prim)}</div>
                <div style="font-size:40px;font-weight:700;color:{pal['text']};
                            font-family:Georgia,serif;line-height:1.1">{pval}</div>
                <div style="font-size:11px;font-weight:600;color:{mc};margin-top:4px">
                    {_e(ms)} MoM &nbsp;·&nbsp; {_e(ys)} YoY
                </div>
                {sec_html}
                {act1_trend}
            </div>
            <!-- Act II: Drivers -->
            <div style="flex:1;border-right:1px solid {pal['border']};padding-right:16px">
                <div class="section-label">Act II · What Moved It</div>
                <div style="font-size:9px;color:{pal['subtext']};font-style:italic;
                            margin-bottom:10px">Top drivers vs prior period</div>
                {drivers_html}
            </div>
            <!-- Act III: Action -->
            <div style="flex:0 0 200px">
                <div class="section-label">Act III · The Call</div>
                {_block_action(narrative, role, pal, accent)}
            </div>
        </div>
        {_block_footer(narrative, accent, pal)}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 2 — SCORECARD  (KPI grid + donut)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_scorecard(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color", "#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    show_kpis   = [k for k in role_kpis if k in kpis] or list(kpis.keys())[:6]

    d_labels, d_values = _donut_data(all_drivers)
    has_donut = bool(d_values)

    return f"""
    <div class="card">
        {_block_masthead(payload, role, pal, accent)}
        {_block_headline(narrative, pal)}
        <div style="display:flex;gap:16px;flex:1">
            <!-- KPI grid -->
            <div style="flex:1">
                {_block_kpi_tiles(kpis, show_kpis, pal, accent)}
                {_block_action(narrative, role, pal, accent)}
            </div>
            <!-- Donut chart -->
            <div style="flex:0 0 220px">
                <div class="section-label">Contribution Mix</div>
                {'<div class="chart-wrap" style="height:180px"><canvas id="donut1"></canvas></div>' if has_donut else '<div style="color:#64748B;font-size:10px">No driver data</div>'}
                <!-- Legend -->
                {''.join(f'<div style="display:flex;align-items:center;gap:6px;margin-top:6px"><div style="width:8px;height:8px;border-radius:50%;background:{[accent,"#34D399","#60A5FA","#F87171","#A78BFA"][i%5]}"></div><div style="font-size:9px;color:{pal["subtext"]};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px">{_e(l)}</div></div>' for i,l in enumerate(d_labels)) if has_donut else ''}
            </div>
        </div>
        {_block_footer(narrative, accent, pal)}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 3 — STORY ARC  (Narrative-first + bar chart)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_story_arc(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color", "#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    side_kpis   = [k for k in role_kpis if k != prim and k in kpis][:4]
    pk          = kpis.get(prim, {})
    pval        = _e(pk.get("value_fmt", "—"))
    ms, mc      = _fmt_pct(pk.get("mom_pct"))
    ys, _       = _fmt_pct(pk.get("yoy_pct"))

    d_labels, d_values, d_colors = _driver_chart_data(all_drivers, 8)

    side_html = ""
    for sk in side_kpis:
        sd = kpis[sk]
        sm, smc = _fmt_pct(sd.get("mom_pct"))
        side_html += f"""
        <div style="background:{pal['surface']};border:1px solid {pal['border']};
                    border-radius:6px;padding:10px 12px;margin-bottom:8px">
            <div style="font-size:8px;color:{pal['muted']};letter-spacing:1px;
                        text-transform:uppercase;margin-bottom:3px">{_e(sk)}</div>
            <div style="font-size:16px;font-weight:700;color:{pal['text']};
                        font-family:Georgia,serif">{_e(sd.get('value_fmt','—'))}</div>
            <div style="font-size:9px;font-weight:600;color:{smc};margin-top:2px">{_e(sm)} MoM</div>
        </div>"""

    return f"""
    <div class="card">
        {_block_masthead(payload, role, pal, accent)}
        <div style="border-left:4px solid {accent};padding-left:12px;margin-bottom:12px">
            {_block_headline(narrative, pal)}
        </div>
        <div style="display:flex;gap:16px;flex:1">
            <!-- Driver breakdown chart -->
            <div style="flex:1">
                <div class="section-label">Driver Breakdown</div>
                {'<div class="chart-wrap" style="height:180px"><canvas id="drvbar"></canvas></div>' if d_labels else '<div style="color:#64748B;font-size:10px">No driver data</div>'}
            </div>
            <!-- Right panel: hero KPI + secondary -->
            <div style="flex:0 0 210px">
                <div class="section-label">Key Metrics</div>
                <div style="font-size:34px;font-weight:700;color:{pal['text']};
                            font-family:Georgia,serif;line-height:1.1">{pval}</div>
                <div style="font-size:8px;color:{pal['muted']};letter-spacing:2px;
                            text-transform:uppercase;margin:4px 0">{_e(prim)}</div>
                <div style="font-size:10px;font-weight:600;color:{mc};margin-bottom:10px">
                    {_e(ms)} MoM · {_e(ys)} YoY</div>
                {side_html}
            </div>
        </div>
        <div style="margin-top:10px">
            {_block_action(narrative, role, pal, accent)}
        </div>
        {_block_footer(narrative, accent, pal)}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 4 — OPS DASHBOARD  (Traffic lights + movers + chart)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_ops_dashboard(narrative, payload: dict, role: dict, pal: dict) -> str:
    accent      = role.get("accent_color", "#F59E0B")
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    show_kpis   = [k for k in role_kpis if k in kpis][:4] or list(kpis.keys())[:4]

    d_labels, d_values, d_colors = _driver_chart_data(all_drivers, 6)

    tiles_html = ""
    for kname in show_kpis:
        kd  = kpis[kname]
        rc  = _rag_color(kd.get("mom_pct"), kd.get("yoy_pct"))
        val = _e(kd.get("value_fmt", "—"))
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        val_cls = "small" if len(kd.get("value_fmt","—")) > 8 else ""
        tiles_html += f"""
        <div class="kpi-tile" style="border-left:5px solid {rc};padding-left:10px;
                                      flex:1 1 180px">
            <div class="kpi-name">{_e(kname)}</div>
            <div class="kpi-value {val_cls}">{val}</div>
            <div class="kpi-delta" style="color:{mc}">{_e(ms)} MoM</div>
        </div>"""

    return f"""
    <div class="card">
        {_block_masthead(payload, role, pal, accent)}
        <div style="font-size:14px;font-weight:700;color:{pal['text']};
                    font-family:Georgia,serif;margin-bottom:8px;
                    border-bottom:1px solid {pal['border']};padding-bottom:8px">
            {_e(_strip_md(getattr(narrative,'headline',''))[:80])}
        </div>
        <!-- Traffic-light KPI tiles -->
        <div class="kpi-grid" style="margin-bottom:12px">{tiles_html}</div>
        <!-- Bottom: chart + movers -->
        <div style="display:flex;gap:16px;flex:1">
            <div style="flex:1">
                <div class="section-label">Driver Chart</div>
                {'<div class="chart-wrap" style="height:160px"><canvas id="opschart"></canvas></div>' if d_labels else '<div style="color:#64748B;font-size:10px">No driver data</div>'}
            </div>
            <div style="flex:0 0 240px">
                <div class="section-label">Top Movers</div>
                {_block_top_movers(all_drivers, pal, accent, 6)}
            </div>
        </div>
        <!-- Action strip -->
        <div style="margin-top:10px;background:{accent}0D;border:1px solid {accent}30;
                    border-radius:6px;padding:10px 14px;display:flex;align-items:center;gap:12px">
            <div style="font-size:8px;font-weight:700;letter-spacing:3px;
                        color:{accent};white-space:nowrap">ACTION ·</div>
            <div style="font-size:10px;font-weight:600;color:{pal['text']}">
                {_e(_strip_md(getattr(narrative,'recommended_action',''))[:160])}
            </div>
        </div>
        {_block_footer(narrative, accent, pal)}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE 5 — BOARD PACK  (Formal slide, beige/light)
# ══════════════════════════════════════════════════════════════════════════════

def _tmpl_board_pack(narrative, payload: dict, role: dict, pal: dict) -> str:
    raw_accent  = role.get("accent_color", "#F59E0B")
    accent_eff  = "#1E3A5F" if pal["bg"] in ("#F5F0E8", "#FFFFFF", "#E8ECF4") else raw_accent
    prim, kpis, all_drivers = _resolve_kpis(payload, role)
    role_kpis   = role.get("kpis", list(kpis.keys()))
    show_kpis   = [k for k in role_kpis if k in kpis][:5] or list(kpis.keys())[:5]
    badge       = _e(role.get("badge", "ARIA · BOARD BRIEFING"))
    ref_date    = _fmt_date(payload.get("reference_date", ""))

    ws = payload.get("window_start", "")
    tf = payload.get("timeframe", "1d")
    range_line = ""
    if tf != "1d" and ws and ws != payload.get("reference_date",""):
        range_line = f'{_fmt_date_short(ws)} → {_fmt_date_short(payload.get("reference_date",""))}'

    kpi_tiles = ""
    n = len(show_kpis)
    w = max(120, min(150, (840 - (n-1)*8) // n))
    for kname in show_kpis:
        kd  = kpis[kname]
        val = _e(kd.get("value_fmt","—"))
        ms, mc = _fmt_pct(kd.get("mom_pct"))
        val_size = "18px" if len(kd.get("value_fmt","—")) <= 7 else "14px"
        kpi_tiles += f"""
        <div style="background:{pal['surface']};border:1px solid {pal['border']};
                    border-radius:4px;padding:10px 12px;min-width:{w}px">
            <div style="font-size:7px;color:{pal['muted']};letter-spacing:1.5px;
                        font-weight:600;text-transform:uppercase;margin-bottom:6px">{_e(kname[:16])}</div>
            <div style="font-size:{val_size};font-weight:700;color:{pal['text']};
                        font-family:Georgia,serif;margin-bottom:4px">{val}</div>
            <div style="font-size:8.5px;font-weight:600;color:{mc}">{_e(ms)}</div>
        </div>"""

    d_labels, d_values = _donut_data(all_drivers)
    has_donut = bool(d_values)

    exec_text = _e(_strip_md(getattr(narrative, "exec_summary", "") or "")[:220])
    act_text  = _e(_strip_md(getattr(narrative, "recommended_action", "") or "")[:200])
    spk_text  = _e(_strip_md(getattr(narrative, "speaker_notes", "") or "")[:180])

    return f"""
    <div style="width:900px;min-height:500px;background:{pal['bg']};
                font-family:-apple-system,BlinkMacSystemFont,'Inter',Arial,sans-serif">
        <!-- Formal top bar -->
        <div style="background:{accent_eff};padding:12px 28px;display:flex;
                    justify-content:space-between;align-items:center">
            <div>
                <div style="color:#FFF;font-size:11px;font-weight:800;letter-spacing:5px">ARIA</div>
                <div style="color:#FFFFFF99;font-size:8px;letter-spacing:3px;margin-top:2px">{badge}</div>
            </div>
            <div style="text-align:right">
                <div style="color:#FFFFFF99;font-size:9px;letter-spacing:2px">{ref_date}</div>
                {f'<div style="color:#FFFFFF66;font-size:8px;margin-top:2px">{_e(range_line)}</div>' if range_line else ''}
            </div>
        </div>
        <div style="padding:18px 28px">
            <!-- Headline -->
            <div style="font-size:17px;font-weight:700;color:{pal['text']};
                        font-family:Georgia,serif;margin-bottom:6px;line-height:1.3">
                {_e(_strip_md(getattr(narrative,'headline','')))}
            </div>
            <div style="font-size:10px;color:{pal['muted']};font-style:italic;
                        margin-bottom:12px">{exec_text}</div>
            <div style="height:1px;background:{accent_eff};opacity:0.2;margin-bottom:12px"></div>
            <!-- KPI row -->
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">{kpi_tiles}</div>
            <div style="height:1px;background:{accent_eff};opacity:0.2;margin-bottom:12px"></div>
            <!-- Donut + recommendation -->
            <div style="display:flex;gap:20px">
                {f'<div><div style="font-size:8px;font-weight:700;letter-spacing:3px;color:{accent_eff};margin-bottom:8px">CONTRIBUTION MIX</div><div style="width:160px;height:130px"><canvas id="bpdonut"></canvas></div></div>' if has_donut else ''}
                <div style="flex:1">
                    <div style="font-size:8px;font-weight:700;letter-spacing:3px;
                                color:{accent_eff};margin-bottom:8px">BOARD RECOMMENDATION</div>
                    <div style="background:{pal['surface']};border:1px solid {accent_eff}30;
                                border-radius:4px;padding:12px 14px">
                        <div style="font-size:12px;font-weight:700;color:{pal['text']};
                                    font-family:Georgia,serif;line-height:1.4">{act_text}</div>
                        <div style="font-size:9px;color:{pal['muted']};margin-top:6px">
                            Owner: {_e(role.get('title','Team'))} · By EOW</div>
                    </div>
                </div>
            </div>
            <div style="height:1px;background:{accent_eff};opacity:0.15;margin:12px 0 8px"></div>
            <div style="font-size:8px;font-weight:700;letter-spacing:3px;
                        color:{accent_eff};margin-bottom:6px">ANTICIPATED QUESTIONS</div>
            <div style="font-size:9px;color:{pal['muted']};font-style:italic;
                        font-family:Georgia,serif">{spk_text}</div>
        </div>
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "editorial":     _tmpl_editorial,
    "scorecard":     _tmpl_scorecard,
    "story_arc":     _tmpl_story_arc,
    "ops_dashboard": _tmpl_ops_dashboard,
    "board_pack":    _tmpl_board_pack,
}


def generate_html_card(narrative, payload: dict, _config: dict,
                       role_config: Optional[dict] = None) -> str:
    """
    Main entry point — identical signature to generate_svg() for drop-in use.
    Returns a self-contained HTML string renderable in browser or Playwright.
    """
    role     = dict(role_config or _DEFAULT_ROLE)
    kpis     = payload.get("kpis", {})
    prim     = role.get("primary_kpi")
    if not prim or prim not in kpis:
        prim = next(iter(kpis), "—")
    role["primary_kpi"] = prim

    tmpl_key = role.get("card_template", "editorial")
    tmpl_fn  = TEMPLATES.get(tmpl_key, _tmpl_editorial)

    style_key = role.get("card_style", "dark")
    pal = dict(PALETTES.get(style_key, PALETTES["dark"]))
    accent = role.get("accent_color", "#F59E0B")

    # Build chart scripts
    prim_real, _, all_drivers = _resolve_kpis(payload, role)
    sparkline  = payload.get("daily_sales_30d", [])
    chart_js   = ""

    if tmpl_key == "editorial":
        if len(sparkline) >= 4:
            chart_js += _chart_sparkline("spark1", sparkline, accent)
        d_labels, d_values, d_colors = _driver_chart_data(all_drivers)
        if d_labels:
            chart_js += _chart_bar_horizontal("drvchart", d_labels, d_values, d_colors, pal)

    elif tmpl_key == "scorecard":
        d_labels, d_values = _donut_data(all_drivers)
        if d_values:
            chart_js += _chart_donut("donut1", d_labels, d_values, accent, prim_real)

    elif tmpl_key == "story_arc":
        d_labels, d_values, d_colors = _driver_chart_data(all_drivers, 8)
        if d_labels:
            chart_js += _chart_bar_horizontal("drvbar", d_labels, d_values, d_colors, pal)

    elif tmpl_key == "ops_dashboard":
        d_labels, d_values, d_colors = _driver_chart_data(all_drivers, 6)
        if d_labels:
            chart_js += _chart_bar_horizontal("opschart", d_labels, d_values, d_colors, pal)

    elif tmpl_key == "board_pack":
        d_labels, d_values = _donut_data(all_drivers)
        if d_values:
            chart_js += _chart_donut("bpdonut", d_labels, d_values, accent)

    body = tmpl_fn(narrative, payload, role, pal)

    return f"""<!DOCTYPE html>
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
    {chart_js}
}});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PNG CONVERSION VIA PLAYWRIGHT
# ══════════════════════════════════════════════════════════════════════════════

def html_to_png(html_str: str, width: int = 900, height: int = 520,
                wait_ms: int = 1500) -> bytes:
    """
    Convert HTML card to PNG bytes using Playwright headless Chromium.
    Waits for Chart.js to finish rendering before screenshot.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && "
            "playwright install chromium --with-deps"
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page    = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html_str, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)   # let Chart.js animate + render
        png = page.screenshot(
            clip={"x": 0, "y": 0, "width": width, "height": height},
            type="png",
        )
        browser.close()
    return png
