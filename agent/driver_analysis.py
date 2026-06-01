"""
driver_analysis.py
------------------
Explains *why* a KPI moved by decomposing it across dimensions
(Category, Sub-Category, Region, Segment, Ship Mode).

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

    compare_to : 'dod' | 'wow' | 'mom' | 'yoy'
    """
    dims = config["drivers"]["dimensions"]
    top_n = top_n or config["drivers"].get("top_n", 3)
    date_col = config["data"]["date_column"]

    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col]).dt.date

    offset_map = {
        "dod": timedelta(days=1),
        "wow": timedelta(days=7),
        "mom": pd.DateOffset(months=1),
        "yoy": pd.DateOffset(years=1),
    }
    offset = offset_map[compare_to]

    if isinstance(offset, pd.DateOffset):
        prior_date = (pd.Timestamp(reference_date) - offset).date()
    else:
        prior_date = reference_date - offset

    today_df = df[df["_date"] == reference_date]
    prior_df = df[df["_date"] == prior_date]

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
        total_delta = 0.0
        cfg = kpi_cfgs.get(kpi)

        for dim in dims:
            if dim not in today_df.columns:
                continue
            curr = _agg(today_df, dim, kpi, cfg)
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

        total_delta = sum(
            i.delta for i in items
            if i.dimension == dims[0]  # avoid double-counting across dims
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
