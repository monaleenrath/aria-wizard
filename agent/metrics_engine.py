"""
metrics_engine.py
-----------------
Computes the daily KPI snapshot and period-over-period deltas
that feed the narrative generator.

Outputs:
  - kpis           : dict of metric → {value, dod, wow, mom, yoy}
  - anomalies      : list of metrics flagged by z-score
  - trend_series   : 90-day daily series (used for plots/context)
  - reference_date : the date being reported on ("yesterday")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover - older Python
    from backports.zoneinfo import ZoneInfo  # type: ignore

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class KPI:
    name: str
    value: float
    value_fmt: str
    dod_pct: Optional[float]   # vs previous day
    wow_pct: Optional[float]   # vs same day last week
    mom_pct: Optional[float]   # vs same day last month
    yoy_pct: Optional[float]   # vs same day last year
    direction: str = "flat"    # up | down | flat
    format: str = "currency"


@dataclass
class Anomaly:
    metric: str
    value: float
    expected: float
    zscore: float
    direction: str  # spike | dip


@dataclass
class MetricsSnapshot:
    reference_date: str
    kpis: Dict[str, KPI]
    anomalies: List[Anomaly] = field(default_factory=list)
    trend_summary: Dict[str, Dict[str, float]] = field(default_factory=dict)
    window_start: str = ""
    timeframe: str = "1d"

    def to_dict(self) -> dict:
        return {
            "reference_date": self.reference_date,
            "window_start":   self.window_start,
            "timeframe":      self.timeframe,
            "kpis": {k: asdict(v) for k, v in self.kpis.items()},
            "anomalies": [asdict(a) for a in self.anomalies],
            "trend_summary": self.trend_summary,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fmt(value: float, kind: str) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if kind == "currency":
        return f"${value:,.0f}"
    if kind == "integer":
        return f"{int(value):,}"
    if kind == "percent":
        return f"{value:.1%}"
    return f"{value:,.2f}"


def _pct_change(curr: float, prev: float) -> Optional[float]:
    if prev is None or pd.isna(prev) or prev == 0:
        return None
    return (curr - prev) / abs(prev)


def _direction(delta: Optional[float], threshold: float = 0.005) -> str:
    if delta is None:
        return "flat"
    if delta > threshold:
        return "up"
    if delta < -threshold:
        return "down"
    return "flat"


def _aggregate(df: pd.DataFrame, kpi_cfg: dict) -> float:
    col = kpi_cfg.get("column", "")   # ratio KPIs use num_col/den_col — column may be absent
    agg = kpi_cfg["agg"]
    if df.empty:
        return 0.0
    if agg == "sum":
        return float(df[col].sum())
    if agg == "nunique":
        return float(df[col].nunique())
    if agg == "mean":
        return float(df[col].mean())
    if agg == "ratio":
        # Derived ratio KPI: num_col / den_col * scale
        # e.g. Profit Margin = SUM(Profit) / SUM(Sales) * 100
        num_col = kpi_cfg.get("num_col", col)
        den_col = kpi_cfg.get("den_col")
        den_agg = kpi_cfg.get("den_agg", "sum")
        scale   = float(kpi_cfg.get("scale", 1))
        if not den_col:
            return 0.0
        num_val = float(df[num_col].sum()) if num_col in df.columns else 0.0
        if den_agg == "nunique":
            den_val = float(df[den_col].nunique()) if den_col in df.columns else 0.0
        else:
            den_val = float(df[den_col].sum()) if den_col in df.columns else 0.0
        return (num_val / den_val * scale) if den_val != 0 else 0.0
    raise ValueError(f"Unsupported agg: {agg}")


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #

def _compute_start_date(reference_date: date, timeframe: str) -> date:
    """Return the window start date for a given timeframe key."""
    if timeframe == "wtd":
        return reference_date - timedelta(days=reference_date.weekday())
    if timeframe == "mtd":
        return reference_date.replace(day=1)
    if timeframe == "qtd":
        q_start_month = ((reference_date.month - 1) // 3) * 3 + 1
        return date(reference_date.year, q_start_month, 1)
    if timeframe == "ytd":
        return date(reference_date.year, 1, 1)
    if timeframe == "alltime":
        # Far-past anchor so the entire dataset is included
        return date(2000, 1, 1)
    # 1d and anything else — single day window
    return reference_date


def compute_metrics(
    df: pd.DataFrame,
    config: dict,
    reference_date: Optional[date] = None,
) -> MetricsSnapshot:
    """Compute KPIs, deltas, and anomalies for the configured timeframe."""
    date_col = config["data"]["date_column"]
    kpi_cfgs = config["metrics"]["kpis"]
    anomaly_threshold = config["metrics"].get("anomaly_zscore_threshold", 2.0)
    lookback = config["metrics"].get("anomaly_lookback_days", 90)
    timeframe = config["metrics"].get("timeframe", "1d")

    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col]).dt.date

    if reference_date is None:
        tz_name = config["data"].get("timezone", "America/Chicago")
        try:
            now_tz = datetime.now(ZoneInfo(tz_name))
        except Exception:
            log.warning("Unknown timezone '%s' — using local clock.", tz_name)
            now_tz = datetime.now()
        target = (now_tz.date() - timedelta(days=1))

        max_in_data = df["_date"].max()
        if target in set(df["_date"].unique()):
            reference_date = target
        elif config["data"].get("fallback_to_max_date_if_missing", True):
            log.warning(
                "Yesterday (%s in %s) not yet in the data — falling back to "
                "the latest available date: %s. Turn off "
                "fallback_to_max_date_if_missing in config.yaml once your "
                "data refreshes daily.",
                target, tz_name, max_in_data,
            )
            reference_date = max_in_data
        else:
            raise ValueError(
                f"No data for yesterday ({target}). Latest day in file is "
                f"{max_in_data}. Either refresh the data or set "
                "fallback_to_max_date_if_missing=true in config.yaml."
            )

    # Compute window start date based on timeframe
    start_date = _compute_start_date(reference_date, timeframe)
    window_days = max((reference_date - start_date).days + 1, 1)
    log.info("Timeframe=%s | Window: %s → %s (%d days)",
             timeframe, start_date, reference_date, window_days)

    df["_date"] = pd.to_datetime(df[date_col]).dt.date

    def slice_window(s: date, e: date):
        return df[(df["_date"] >= s) & (df["_date"] <= e)]

    # Current window
    curr_df = slice_window(start_date, reference_date)

    # Comparison windows — same-length period shifted back
    prev_start = start_date - timedelta(days=window_days)
    prev_end   = reference_date - timedelta(days=window_days)
    prev_df    = slice_window(prev_start, prev_end)

    # YoY — same window one year prior
    yoy_start = date(start_date.year - 1, start_date.month, start_date.day)
    yoy_end   = date(reference_date.year - 1, reference_date.month, reference_date.day)
    yoy_df    = slice_window(yoy_start, yoy_end)

    kpis: Dict[str, KPI] = {}
    for kpi_cfg in kpi_cfgs:
        name = kpi_cfg["name"]
        fmt = kpi_cfg.get("format", "currency")

        curr = _aggregate(curr_df, kpi_cfg)
        prev = _aggregate(prev_df, kpi_cfg)
        yoy  = _aggregate(yoy_df,  kpi_cfg)

        chg     = _pct_change(curr, prev)
        yoy_chg = _pct_change(curr, yoy)

        # Map the primary comparison to the right field based on timeframe
        dod_pct = chg if timeframe == "1d"  else None
        wow_pct = chg if timeframe == "wtd" else None
        mom_pct = chg if timeframe in ("mtd", "1d") else None
        yoy_pct = yoy_chg

        kpis[name] = KPI(
            name=name,
            value=curr,
            value_fmt=_fmt(curr, fmt),
            dod_pct=dod_pct,
            wow_pct=wow_pct,
            mom_pct=mom_pct if mom_pct is not None else chg,
            yoy_pct=yoy_pct,
            direction=_direction(chg),
            format=fmt,
        )

    # ── Derived / ratio KPIs from config ──────────────────────────────────── #
    # Process any KPI declared with agg="ratio" directly in config.yaml.
    # These are computed from curr_df columns rather than from already-computed
    # KPI values, so they work for any dataset.
    for _kpi_cfg in kpi_cfgs:
        if _kpi_cfg.get("agg") != "ratio":
            continue
        _name    = _kpi_cfg["name"]
        _num_col = _kpi_cfg.get("num_col") or _kpi_cfg.get("column")
        _den_col = _kpi_cfg.get("den_col")
        _scale   = float(_kpi_cfg.get("scale", 1))
        _fmt_type = _kpi_cfg.get("format", "percent")
        if not _num_col or not _den_col:
            continue
        if _num_col not in curr_df.columns or _den_col not in curr_df.columns:
            continue
        _num = float(curr_df[_num_col].sum())
        _den = float(curr_df[_den_col].sum())
        if _den == 0:
            _val = 0.0
        else:
            _val = _num / _den * _scale
        if _name not in kpis:   # don't overwrite if already computed
            kpis[_name] = KPI(
                name=_name, value=_val, value_fmt=_fmt(_val, _fmt_type),
                dod_pct=None, wow_pct=None, mom_pct=None, yoy_pct=None,
                direction=_direction(_val - 0.1, threshold=0.0),
                format=_fmt_type,
            )

    # ── Auto-derive Margin% if not already in kpis ────────────────────────── #
    # Find the first revenue-like (currency, sum) and profit-like KPI dynamically.
    # This covers datasets that don't declare an explicit ratio KPI in config.
    if "Margin%" not in kpis and "Profit Margin %" not in kpis:
        _rev_kpi    = next(
            (k for k in kpi_cfgs
             if k.get("format") == "currency" and k.get("agg") == "sum"
             and any(w in k["name"].lower() for w in ("revenue", "sales", "income"))),
            None,
        )
        _profit_kpi = next(
            (k for k in kpi_cfgs
             if k.get("format") == "currency" and k.get("agg") == "sum"
             and any(w in k["name"].lower() for w in ("profit", "margin", "net"))),
            None,
        )
        if _rev_kpi and _profit_kpi and _rev_kpi["name"] != _profit_kpi["name"]:
            _rev_val    = kpis[_rev_kpi["name"]].value    if _rev_kpi["name"]    in kpis else 0.0
            _profit_val = kpis[_profit_kpi["name"]].value if _profit_kpi["name"] in kpis else 0.0
            if _rev_val:
                _margin = _profit_val / _rev_val
                kpis["Profit Margin %"] = KPI(
                    "Profit Margin %", _margin, _fmt(_margin, "percent"),
                    None, None, None, None,
                    _direction(_margin - 0.1, threshold=0.0), "percent",
                )

    # ── Legacy Superstore derived metrics (AOV, Margin%) ─────────────────── #
    # Kept for backward compatibility with Superstore dataset.
    sales  = kpis["Sales"].value  if "Sales"  in kpis else None
    profit = kpis["Profit"].value if "Profit" in kpis else None
    orders = kpis["Orders"].value if "Orders" in kpis else None

    if sales is not None and orders:
        aov = sales / orders
        kpis["AOV"] = KPI("AOV", aov, _fmt(aov, "currency"),
                          None, None, None, None, "flat", "currency")
    if sales is not None and profit is not None and sales and "Margin%" not in kpis:
        margin = profit / sales
        kpis["Margin%"] = KPI("Margin%", margin, _fmt(margin, "percent"),
                              None, None, None, None,
                              _direction(margin - 0.1, threshold=0.0),
                              "percent")

    # ---------------- Anomaly detection (z-score on lookback window) ----- #
    # Build a daily aggregation using only the KPIs from config (no hardcoded columns).
    _agg_spec: Dict[str, tuple] = {}
    for _kpi_cfg in kpi_cfgs:
        _col  = _kpi_cfg.get("column")
        _agg  = _kpi_cfg.get("agg", "sum")
        _name = _kpi_cfg["name"]
        if not _col or _col not in df.columns:
            continue
        if _agg == "sum":
            _agg_spec[_name] = (_col, "sum")
        elif _agg == "nunique":
            _agg_spec[_name] = (_col, "nunique")
        elif _agg == "mean":
            _agg_spec[_name] = (_col, "mean")

    if _agg_spec:
        daily = (
            df.groupby("_date")
              .agg(**_agg_spec)
              .reset_index()
              .sort_values("_date")
        )
    else:
        daily = df[["_date"]].drop_duplicates().sort_values("_date").reset_index(drop=True)

    window = daily[
        (daily["_date"] >= reference_date - timedelta(days=lookback)) &
        (daily["_date"] < reference_date)
    ]

    anomalies: List[Anomaly] = []
    for metric in list(kpis.keys()):
        # Only run anomaly detection on primary (non-derived) KPIs present in daily
        if metric not in daily.columns:
            continue
        if window.empty or len(window) < 3:
            continue
        col_std = window[metric].std(ddof=0)
        if col_std == 0:
            continue
        mean = window[metric].mean()
        std  = col_std
        curr_val = kpis[metric].value
        z = (curr_val - mean) / std
        if abs(z) >= anomaly_threshold:
            anomalies.append(
                Anomaly(
                    metric=metric,
                    value=curr_val,
                    expected=mean,
                    zscore=round(float(z), 2),
                    direction="spike" if z > 0 else "dip",
                )
            )

    # ---------------- Trend summary (last 7d / 30d averages) -------------- #
    trend: Dict[str, Dict[str, float]] = {}
    for metric in list(kpis.keys()):
        if metric not in daily.columns:
            continue
        last_7  = daily[daily["_date"] >= reference_date - timedelta(days=7)][metric].mean()
        last_30 = daily[daily["_date"] >= reference_date - timedelta(days=30)][metric].mean()
        trend[metric] = {
            "avg_last_7d":  round(float(last_7),  2) if not pd.isna(last_7)  else 0.0,
            "avg_last_30d": round(float(last_30), 2) if not pd.isna(last_30) else 0.0,
        }

    return MetricsSnapshot(
        reference_date=str(reference_date),
        window_start=str(start_date),
        timeframe=timeframe,
        kpis=kpis,
        anomalies=anomalies,
        trend_summary=trend,
    )


# ── Target achievement ───────────────────────────────────────────────────── #

import re as _re
import calendar as _calendar

# Common prefixes/suffixes on target column names that should be stripped
# when fuzzy-matching against actual KPI names
_TARGET_PREFIXES = ["target_", "tgt_", "t_", "plan_", "forecast_", "fcst_",
                    "budget_", "bdgt_", "goal_", "et_", "new_"]
_TARGET_SUFFIXES = ["_target", "_tgt", "_plan", "_forecast", "_fcst",
                    "_budget", "_bdgt", "_goal"]


def _normalize_col(name: str) -> str:
    """Lowercase + strip common target prefixes/suffixes + remove non-alphanumeric."""
    s = name.lower().strip()
    for p in _TARGET_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    for suf in _TARGET_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return _re.sub(r"[^a-z0-9]", "", s)


def _detect_target_schema(tdf: pd.DataFrame):
    """
    Auto-detect date column, dimension columns, and KPI columns in the
    target DataFrame. Returns (date_col, granularity, dim_cols, kpi_cols).

    granularity: "monthly" | "daily"
    dim_cols:    list of string/categorical low-cardinality columns
    kpi_cols:    list of numeric columns (potential target KPI values)
    """
    date_col    = None
    granularity = "monthly"
    kpi_cols    = []
    dim_cols    = []

    for col in tdf.columns:
        sample = tdf[col].dropna()
        if sample.empty:
            continue

        # ── Try to identify the date/period column ────────────────────────── #
        if date_col is None:
            # Try YYYY-MM string pattern first (most common in target files)
            if tdf[col].dtype == object:
                str_sample = sample.astype(str).head(5)
                if all(_re.match(r"^\d{4}-\d{2}$", str(v).strip()[:7]) for v in str_sample):
                    date_col    = col
                    granularity = "monthly"
                    continue
            # Try parsing as datetime
            try:
                parsed = pd.to_datetime(sample.head(20), errors="raise")
                date_col = col
                # If all values have day=1 or dtype is period-like → monthly
                unique_days = parsed.dt.day.nunique()
                granularity = "daily" if unique_days > 3 else "monthly"
                continue
            except Exception:
                pass

        # ── Numeric → candidate KPI column ───────────────────────────────── #
        if pd.api.types.is_numeric_dtype(tdf[col]):
            kpi_cols.append(col)
            continue

        # ── String/categorical low-cardinality → dimension column ─────────── #
        n_unique = tdf[col].nunique()
        if tdf[col].dtype == object and 1 < n_unique <= 60:
            dim_cols.append(col)

    return date_col, granularity, dim_cols, kpi_cols


def _fuzzy_match_kpi_cols(
    target_kpi_cols: List[str],
    kpi_names: List[str],
) -> Dict[str, str]:
    """
    Returns {kpi_name: target_col} for every KPI name that has a
    confident fuzzy match in the target columns.
    """
    norm_to_tcol: Dict[str, str] = {}
    for col in target_kpi_cols:
        n = _normalize_col(col)
        if n:
            norm_to_tcol[n] = col

    result: Dict[str, str] = {}
    for kpi_name in kpi_names:
        n = _normalize_col(kpi_name)
        if n and n in norm_to_tcol:
            result[kpi_name] = norm_to_tcol[n]
    return result


def _target_months_for_timeframe(reference_date: date, timeframe: str) -> List[str]:
    """Return list of YYYY-MM strings covering the reporting period."""
    ref_ym  = f"{reference_date.year}-{reference_date.month:02d}"
    q_start = date(reference_date.year,
                   ((reference_date.month - 1) // 3) * 3 + 1, 1)

    if timeframe in ("mtd", "wtd", "1d"):
        return [ref_ym]
    if timeframe == "qtd":
        months = []
        for i in range(3):
            m = q_start.month + i
            if m > 12:
                break
            d = date(q_start.year, m, 1)
            if d <= reference_date:
                months.append(f"{d.year}-{d.month:02d}")
        return months
    if timeframe == "ytd":
        return [f"{reference_date.year}-{m:02d}"
                for m in range(1, reference_date.month + 1)]
    return [ref_ym]


def compute_rich_targets(
    target_path: str,
    kpi_cfgs: list,
    reference_date: date,
    timeframe: str,
) -> dict:
    """
    Load the target Excel, auto-detect schema, fuzzy-match KPI columns, and
    return a rich structure used by the HTML templates and narrative generator.

    Returns a dict with keys:
      available              – bool: False if file unreadable or no KPI matched
      granularity            – "monthly" | "daily"
      dim_cols               – list of dimension column names found in target file
      kpi_col_map            – {kpi_name: target_col_name}  (fuzzy-matched)
      totals                 – {kpi_name: {target, target_fmt, achievement_pct}}
      monthly_series         – {kpi_name: {labels:[...], values:[...]}}
      dim_member_totals      – {dim: {member: {kpi_name: target_value, ...}}}
      dim_member_monthly     – {dim: {member: {kpi_name: {labels, values}}}}
    """
    _empty = {"available": False, "granularity": "monthly",
              "dim_cols": [], "kpi_col_map": {}, "totals": {},
              "monthly_series": {}, "dim_member_totals": {},
              "dim_member_monthly": {}}

    # ── Load file ─────────────────────────────────────────────────────────── #
    try:
        tdf = pd.read_excel(target_path, engine="openpyxl")
    except Exception as exc:
        log.warning("Target file not readable (%s): %s", target_path, exc)
        return _empty

    if tdf.empty:
        return _empty

    # ── Auto-detect schema ────────────────────────────────────────────────── #
    date_col, granularity, dim_cols, raw_kpi_cols = _detect_target_schema(tdf)
    if not date_col:
        log.warning("Target file: no date column detected — skipping.")
        return _empty

    # Normalise date column to YYYY-MM strings
    tdf["_ym"] = (
        pd.to_datetime(tdf[date_col].astype(str).str[:10], errors="coerce")
        .dt.to_period("M")
        .astype(str)
    )
    tdf = tdf.dropna(subset=["_ym"])

    # ── Fuzzy-match target cols → KPI names ──────────────────────────────── #
    kpi_names  = [k.get("name", "") for k in kpi_cfgs if k.get("name")]
    kpi_fmts   = {k.get("name", ""): k.get("format", "number") for k in kpi_cfgs}
    kpi_aggs   = {k.get("name", ""): k.get("agg", "sum") for k in kpi_cfgs}
    kpi_col_map = _fuzzy_match_kpi_cols(raw_kpi_cols, kpi_names)

    if not kpi_col_map:
        log.warning("Target file: no KPI columns matched — skipping.")
        return _empty

    log.info("Target KPI column map: %s", kpi_col_map)

    # ── Determine period months ───────────────────────────────────────────── #
    period_months = _target_months_for_timeframe(reference_date, timeframe)
    period_df     = tdf[tdf["_ym"].isin(period_months)]

    # All months present in the file (for sparkline series)
    all_months_sorted = sorted(tdf["_ym"].dropna().unique().tolist())

    # Pro-rate scalar for 1d timeframe
    days_in_month = _calendar.monthrange(reference_date.year, reference_date.month)[1]
    prorate       = reference_date.day / days_in_month if timeframe == "1d" else 1.0

    # ── Helper: aggregate a target column for a slice ────────────────────── #
    def _agg_target(df_slice: pd.DataFrame, tcol: str, agg: str) -> float:
        if df_slice.empty or tcol not in df_slice.columns:
            return 0.0
        vals = df_slice[tcol].dropna()
        if vals.empty:
            return 0.0
        if agg == "mean":
            return float(vals.mean())
        return float(vals.sum())

    # ── TOTALS — overall target per KPI for the period ────────────────────── #
    totals: Dict[str, dict] = {}
    for kpi_name, tcol in kpi_col_map.items():
        agg  = kpi_aggs.get(kpi_name, "sum")
        fmt  = kpi_fmts.get(kpi_name, "number")
        tval = _agg_target(period_df, tcol, agg) * (prorate if agg != "mean" else 1.0)
        if tval == 0:
            continue
        totals[kpi_name] = {
            "target":          round(tval, 2),
            "target_fmt":      _fmt(tval, fmt),
            "achievement_pct": 100.0,   # filled later by fill_achievement()
        }

    # ── MONTHLY SERIES — 12-month target sparkline per KPI (overall) ─────── #
    monthly_series: Dict[str, dict] = {}
    for kpi_name, tcol in kpi_col_map.items():
        agg = kpi_aggs.get(kpi_name, "sum")
        rows = []
        for ym in all_months_sorted[-12:]:
            slice_df = tdf[tdf["_ym"] == ym]
            rows.append((_agg_target(slice_df, tcol, agg), ym))
        if any(v > 0 for v, _ in rows):
            monthly_series[kpi_name] = {
                "labels": [ym for _, ym in rows],
                "values": [round(v, 2) for v, _ in rows],
            }

    # ── DIM MEMBER TOTALS — per dimension × member × KPI ────────────────── #
    dim_member_totals: Dict[str, Dict[str, Dict[str, float]]] = {}
    for dim in dim_cols:
        if dim not in period_df.columns:
            continue
        dim_member_totals[dim] = {}
        members = sorted(period_df[dim].dropna().unique().tolist(), key=str)
        for mem in members:
            mem_df = period_df[period_df[dim] == mem]
            kpi_vals: Dict[str, float] = {}
            for kpi_name, tcol in kpi_col_map.items():
                agg  = kpi_aggs.get(kpi_name, "sum")
                tval = _agg_target(mem_df, tcol, agg) * (prorate if agg != "mean" else 1.0)
                if tval > 0:
                    kpi_vals[kpi_name] = round(tval, 2)
            if kpi_vals:
                dim_member_totals[dim][str(mem)] = kpi_vals

    # ── DIM MEMBER MONTHLY SERIES — per dim × member × KPI × month ───────── #
    dim_member_monthly: Dict[str, Dict[str, Dict[str, dict]]] = {}
    for dim in dim_cols:
        if dim not in tdf.columns:
            continue
        dim_member_monthly[dim] = {}
        members = sorted(tdf[dim].dropna().unique().tolist(), key=str)
        for mem in members:
            mem_all = tdf[tdf[dim] == mem]
            kpi_series: Dict[str, dict] = {}
            for kpi_name, tcol in kpi_col_map.items():
                agg    = kpi_aggs.get(kpi_name, "sum")
                labels, values = [], []
                for ym in all_months_sorted[-12:]:
                    slice_df = mem_all[mem_all["_ym"] == ym]
                    v = _agg_target(slice_df, tcol, agg)
                    labels.append(ym)
                    values.append(round(v, 2))
                if any(v > 0 for v in values):
                    kpi_series[kpi_name] = {"labels": labels, "values": values}
            if kpi_series:
                dim_member_monthly[dim][str(mem)] = kpi_series

    return {
        "available":           bool(totals),
        "granularity":         granularity,
        "dim_cols":            dim_cols,
        "kpi_col_map":         kpi_col_map,
        "totals":              totals,
        "monthly_series":      monthly_series,
        "dim_member_totals":   dim_member_totals,
        "dim_member_monthly":  dim_member_monthly,
    }


# ── Backward-compat shim ─────────────────────────────────────────────────── #

def compute_target_achievement(
    target_path: str,
    kpi_cfgs: list,
    reference_date: date,
    timeframe: str,
) -> Dict[str, dict]:
    """Legacy wrapper — returns totals dict only. Use compute_rich_targets() instead."""
    rich = compute_rich_targets(target_path, kpi_cfgs, reference_date, timeframe)
    return rich.get("totals", {})


def fill_achievement(targets: Dict[str, dict], kpis: Dict[str, "KPI"]) -> Dict[str, dict]:
    """Cross-reference targets with actuals and compute achievement %."""
    for name, t in targets.items():
        if name in kpis and t.get("target", 0) != 0:
            actual = kpis[name].value
            t["achievement_pct"] = round((actual / t["target"]) * 100, 1)
    return targets
