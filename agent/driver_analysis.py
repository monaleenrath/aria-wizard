"""
driver_analysis.py
------------------
Explains *why* a KPI moved by decomposing it across dimensions.

Compares the SAME date window used by metrics_engine (full timeframe range)
vs the equivalent prior-period window — NOT just single day vs single day.

For each dimension and each KPI it returns:
  - Top contributors  (biggest absolute positive change)
  - Top detractors    (biggest absolute negative change)
  - Contribution %    (share of the total delta)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class DriverItem:
    dimension: str
    member: str
    kpi: str
    current: float
    prior: float
    delta: float
    delta_pct: Optional[float]
    contribution_pct: Optional[float]  # of total delta for that KPI


def _compute_start_date(reference_date: date, timeframe: str) -> date:
    """Mirror of metrics_engine._compute_start_date — keep in sync."""
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
        return date(2000, 1, 1)
    # 1d — single day
    return reference_date


def _agg(df: pd.DataFrame, dim: str, kpi: str,
         kpi_cfg: Optional[dict] = None) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="float64")
    if kpi_cfg:
        agg_type = kpi_cfg.get("agg", "sum")
        col = kpi_cfg.get("column", kpi)
        if col not in df.columns:
            return pd.Series(dtype="float64")
        if agg_type == "nunique":
            return df.groupby(dim)[col].nunique()
        elif agg_type == "mean":
            return df.groupby(dim)[col].mean()
        else:
            return df.groupby(dim)[col].sum()
    # fallback
    if kpi in df.columns:
        return df.groupby(dim)[kpi].sum()
    return pd.Series(dtype="float64")


def analyze_drivers(
    df: pd.DataFrame,
    config: dict,
    reference_date: date,
    compare_to: str = "yoy",
    top_n: Optional[int] = None,
) -> Dict[str, List[DriverItem]]:
    """
    Decompose KPI deltas across dimensions.

    Uses the same date window as metrics_engine (driven by config timeframe),
    then shifts that window back by the compare_to offset for the prior period.

    compare_to : 'dod' | 'wow' | 'mom' | 'yoy'
    """
    dims     = config["drivers"]["dimensions"]
    top_n    = top_n or config["drivers"].get("top_n", 3)
    date_col = config["data"]["date_column"]
    timeframe = config.get("metrics", {}).get("timeframe", "1d")

    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col]).dt.date

    # ── Current window (mirrors metrics_engine logic) ─────────────────────── #
    curr_start = _compute_start_date(reference_date, timeframe)
    window_days = max((reference_date - curr_start).days + 1, 1)

    # ── Prior window — same length shifted back ───────────────────────────── #
    # For YoY comparison shift by 1 year; otherwise shift by window length
    if compare_to == "yoy":
        try:
            prior_end   = date(reference_date.year - 1,
                               reference_date.month, reference_date.day)
            prior_start = date(curr_start.year - 1,
                               curr_start.month, curr_start.day)
        except ValueError:
            # leap-day edge case
            prior_end   = reference_date - timedelta(days=365)
            prior_start = curr_start    - timedelta(days=365)
    else:
        prior_end   = reference_date - timedelta(days=window_days)
        prior_start = curr_start     - timedelta(days=window_days)

    curr_df  = df[(df["_date"] >= curr_start)  & (df["_date"] <= reference_date)]
    prior_df = df[(df["_date"] >= prior_start) & (df["_date"] <= prior_end)]

    # Build a lookup from KPI name → config dict
    kpi_cfgs = {k["name"]: k for k in config.get("metrics", {}).get("kpis", [])}

    # Only decompose KPIs that have a real column (skip DERIVED ratio KPIs)
    kpis_to_decompose = [
        k["name"] for k in config.get("metrics", {}).get("kpis", [])
        if k.get("column") and k.get("column") != "DERIVED"
        and k.get("agg") in ("sum", "nunique", "mean")
    ]

    results: Dict[str, List[DriverItem]] = {}

    for kpi in kpis_to_decompose:
        items: List[DriverItem] = []
        cfg = kpi_cfgs.get(kpi)

        for dim in dims:
            if dim not in curr_df.columns:
                continue
            curr  = _agg(curr_df,  dim, kpi, cfg)
            prior = _agg(prior_df, dim, kpi, cfg)
            members = sorted(set(curr.index) | set(prior.index))
            for m in members:
                c = float(curr.get(m, 0.0))
                p = float(prior.get(m, 0.0))
                delta = c - p
                pct = (delta / abs(p)) if p else None
                items.append(
                    DriverItem(
                        dimension=dim,
                        member=str(m),
                        kpi=kpi,
                        current=round(c, 2),
                        prior=round(p, 2),
                        delta=round(delta, 2),
                        delta_pct=round(pct, 4) if pct is not None else None,
                        contribution_pct=None,
                    )
                )

        # Contribution % of total delta (computed within each dim)
        by_dim: Dict[str, List[DriverItem]] = {}
        for item in items:
            by_dim.setdefault(item.dimension, []).append(item)

        for dim, group in by_dim.items():
            dim_total = sum(i.delta for i in group)
            for it in group:
                if dim_total:
                    it.contribution_pct = round(it.delta / dim_total, 4)

        # Rank: top contributors + top detractors per dimension (deduped)
        ranked: List[DriverItem] = []
        seen: set = set()
        for dim, group in by_dim.items():
            group_sorted = sorted(group, key=lambda x: x.delta, reverse=True)
            picks = list(group_sorted[:top_n]) + list(group_sorted[-top_n:][::-1])
            for it in picks:
                key = (it.dimension, it.member, it.kpi)
                if key in seen:
                    continue
                seen.add(key)
                ranked.append(it)

        results[kpi] = ranked

    return results


def drivers_to_dict(drivers: Dict[str, List[DriverItem]]) -> dict:
    return {k: [asdict(i) for i in v] for k, v in drivers.items()}
