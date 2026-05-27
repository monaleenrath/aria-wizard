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

    def to_dict(self) -> dict:
        return {
            "reference_date": self.reference_date,
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
    col = kpi_cfg["column"]
    agg = kpi_cfg["agg"]
    if df.empty:
        return 0.0
    if agg == "sum":
        return float(df[col].sum())
    if agg == "nunique":
        return float(df[col].nunique())
    if agg == "mean":
        return float(df[col].mean())
    raise ValueError(f"Unsupported agg: {agg}")


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #

def compute_metrics(
    df: pd.DataFrame,
    config: dict,
    reference_date: Optional[date] = None,
) -> MetricsSnapshot:
    """Compute KPIs, deltas, and anomalies for the reference date."""
    date_col = config["data"]["date_column"]
    kpi_cfgs = config["metrics"]["kpis"]
    anomaly_threshold = config["metrics"].get("anomaly_zscore_threshold", 2.0)
    lookback = config["metrics"].get("anomaly_lookback_days", 90)

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

    log.info("Reporting on reference_date=%s", reference_date)

    # Comparison anchors
    prev_day = reference_date - timedelta(days=1)
    prev_week = reference_date - timedelta(days=7)
    prev_month = reference_date - pd.DateOffset(months=1)
    prev_month = prev_month.date() if hasattr(prev_month, "date") else prev_month
    prev_year = reference_date - pd.DateOffset(years=1)
    prev_year = prev_year.date() if hasattr(prev_year, "date") else prev_year

    def slice_day(d):
        return df[df["_date"] == d]

    today_df = slice_day(reference_date)

    kpis: Dict[str, KPI] = {}
    for kpi_cfg in kpi_cfgs:
        name = kpi_cfg["name"]
        fmt = kpi_cfg.get("format", "currency")

        curr = _aggregate(today_df, kpi_cfg)
        dod = _pct_change(curr, _aggregate(slice_day(prev_day), kpi_cfg))
        wow = _pct_change(curr, _aggregate(slice_day(prev_week), kpi_cfg))
        mom = _pct_change(curr, _aggregate(slice_day(prev_month), kpi_cfg))
        yoy = _pct_change(curr, _aggregate(slice_day(prev_year), kpi_cfg))

        kpis[name] = KPI(
            name=name,
            value=curr,
            value_fmt=_fmt(curr, fmt),
            dod_pct=dod,
            wow_pct=wow,
            mom_pct=mom,
            yoy_pct=yoy,
            direction=_direction(dod),
            format=fmt,
        )

    # Derived metrics
    sales = kpis["Sales"].value if "Sales" in kpis else 0
    profit = kpis["Profit"].value if "Profit" in kpis else 0
    orders = kpis["Orders"].value if "Orders" in kpis else 0

    if orders:
        aov = sales / orders
        kpis["AOV"] = KPI("AOV", aov, _fmt(aov, "currency"),
                          None, None, None, None, "flat", "currency")
    if sales:
        margin = profit / sales
        kpis["Margin%"] = KPI("Margin%", margin, _fmt(margin, "percent"),
                              None, None, None, None,
                              _direction(margin - 0.1, threshold=0.0),
                              "percent")

    # ---------------- Anomaly detection (z-score on lookback window) ----- #
    daily = (
        df.groupby("_date")
          .agg(Sales=("Sales", "sum"),
               Profit=("Profit", "sum"),
               Orders=("Order ID", "nunique"),
               Quantity=("Quantity", "sum"))
          .reset_index()
          .sort_values("_date")
    )

    window = daily[
        (daily["_date"] >= reference_date - timedelta(days=lookback)) &
        (daily["_date"] < reference_date)
    ]

    anomalies: List[Anomaly] = []
    for metric in ["Sales", "Profit", "Orders", "Quantity"]:
        if metric not in kpis:
            continue
        if window.empty or window[metric].std(ddof=0) == 0:
            continue
        mean = window[metric].mean()
        std = window[metric].std(ddof=0)
        curr_val = kpis[metric].value
        z = (curr_val - mean) / std if std else 0
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
    for metric in ["Sales", "Profit", "Orders"]:
        if metric not in daily.columns:
            continue
        last_7 = daily[daily["_date"] >= reference_date - timedelta(days=7)][metric].mean()
        last_30 = daily[daily["_date"] >= reference_date - timedelta(days=30)][metric].mean()
        trend[metric] = {
            "avg_last_7d": round(float(last_7), 2) if not pd.isna(last_7) else 0.0,
            "avg_last_30d": round(float(last_30), 2) if not pd.isna(last_30) else 0.0,
        }

    return MetricsSnapshot(
        reference_date=str(reference_date),
        kpis=kpis,
        anomalies=anomalies,
        trend_summary=trend,
    )
