"""
data_loader.py
--------------
Loads data for the ARIA pipeline.

Primary source : Google Sheets (public, CSV export endpoint)
Backup source  : local Excel / CSV file in data/

Why Google Sheets first:
  - Refreshes automatically — agent always sees yesterday's data.
  - No file to babysit on the user's Mac.
  - Free, no API key, no OAuth: requires sharing="Anyone with the link".

The loader is intentionally forgiving:
  1. Try the Sheets URL.
  2. If it fails (network, sharing not public, sheet deleted), log the
     reason and fall back to the local file so the daily briefing
     still goes out.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

import pandas as pd

log = logging.getLogger(__name__)


# Kept for backward compatibility — but no longer enforced.
# The pipeline reads required columns from config.yaml at runtime.
REQUIRED_COLUMNS: list = []


# --------------------------------------------------------------------------- #
# Google Sheets helpers
# --------------------------------------------------------------------------- #

def _parse_google_sheet_url(url: str) -> Tuple[str, str]:
    """
    Accept any Google Sheets URL form and return (sheet_id, gid).

    Supports:
      https://docs.google.com/spreadsheets/d/<ID>/edit?gid=<GID>
      https://docs.google.com/spreadsheets/d/<ID>/edit#gid=<GID>
      https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=<GID>
      https://docs.google.com/spreadsheets/d/<ID>
    """
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError(
            f"Could not extract a Google Sheet ID from URL: {url!r}. "
            "Expected a URL like https://docs.google.com/spreadsheets/d/<ID>/edit"
        )
    sheet_id = m.group(1)

    # gid can appear in the query string OR after a fragment hash
    parsed = urlparse(url)
    gid = parse_qs(parsed.query).get("gid", [None])[0]
    if not gid and parsed.fragment:
        gid = parse_qs(parsed.fragment).get("gid", [None])[0]
    gid = gid or "0"
    return sheet_id, gid


def _csv_export_url(sheet_url: str) -> str:
    """Convert any Sheets URL into a clean CSV export URL."""
    sheet_id, gid = _parse_google_sheet_url(sheet_url)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _read_google_sheet(sheet_url: str) -> pd.DataFrame:
    csv_url = _csv_export_url(sheet_url)
    log.info("Fetching live data from Google Sheets: %s", csv_url)
    # pandas handles the redirect chain Google returns
    return pd.read_csv(csv_url)


# --------------------------------------------------------------------------- #
# Excel fallback
# --------------------------------------------------------------------------- #

def _read_excel(path: str, sheet_name: str = "Orders") -> pd.DataFrame:
    ext = Path(path).suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    log.info("Reading Excel fallback file %s with engine=%s", path, engine)
    return pd.read_excel(path, sheet_name=sheet_name, engine=engine)


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #

def _clean(df: pd.DataFrame, date_column: str = "Order Date",
           kpi_columns: Optional[list] = None) -> pd.DataFrame:
    """Clean loaded dataframe.

    Only the date column is strictly required. KPI columns (from config.yaml)
    are coerced to numeric but never dropped — missing ones surface later as
    zeros so the pipeline stays live even with partial data.
    """
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]

    if date_column not in df.columns:
        raise ValueError(
            f"Date column '{date_column}' not found in data. "
            f"Available columns: {list(df.columns)}"
        )

    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column])

    # Coerce any numeric-looking KPI columns to float
    numeric_candidates = kpi_columns or []
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(date_column).reset_index(drop=True)
    log.info(
        "Loaded %d rows | date range %s → %s",
        len(df),
        df[date_column].min().date(),
        df[date_column].max().date(),
    )
    return df


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #

def _read_csv(path: str) -> pd.DataFrame:
    log.info("Reading CSV file %s", path)
    return pd.read_csv(path)


def load_data(
    google_sheet_url: Optional[str] = None,
    excel_path: Optional[str] = None,
    sheet_name: str = "Orders",
    date_column: str = "Order Date",
    tableau_public_url: Optional[str] = None,
    kpi_columns: Optional[list] = None,
) -> pd.DataFrame:
    """
    Load data for the ARIA pipeline. Google Sheets first, local file as fallback.

    google_sheet_url : any Google Sheets URL (the loader extracts the CSV)
    excel_path       : optional local file path (.xls, .xlsx, or .csv)
    kpi_columns      : list of column names used as KPIs — coerced to numeric
    """
    last_exc: Optional[Exception] = None

    # 1) Try Google Sheets
    if google_sheet_url:
        try:
            df = _read_google_sheet(google_sheet_url)
            return _clean(df, date_column=date_column, kpi_columns=kpi_columns)
        except Exception as exc:
            log.warning(
                "Google Sheets fetch failed (%s). Will try local file fallback.",
                exc,
            )
            last_exc = exc

    # 2) Try local file (Excel or CSV)
    if excel_path:
        ext = Path(excel_path).suffix.lower()

        # Build candidate list (try both .xls and .xlsx for Excel)
        candidates = [excel_path]
        if ext == ".xls":
            candidates.append(Path(excel_path).with_suffix(".xlsx").as_posix())
        elif ext == ".xlsx":
            candidates.append(Path(excel_path).with_suffix(".xls").as_posix())

        for p in candidates:
            if not os.path.exists(p):
                continue
            try:
                p_ext = Path(p).suffix.lower()
                if p_ext == ".csv":
                    df = _read_csv(p)
                else:
                    df = _read_excel(p, sheet_name=sheet_name)
                return _clean(df, date_column=date_column, kpi_columns=kpi_columns)
            except Exception as exc:
                log.warning("Could not read %s (%s) — trying next.", p, exc)
                last_exc = exc

    msg = (
        "Could not load data from any source. "
        f"Google Sheet: {'configured' if google_sheet_url else 'not configured'}. "
        f"Local file: {'configured ('+str(excel_path)+')' if excel_path else 'not configured'}. "
        "If using Google Sheets, make sure it is shared as 'Anyone with the link → Viewer'. "
        f"Backup dashboard: {tableau_public_url}"
    )
    log.error(msg)
    if last_exc:
        raise RuntimeError(msg) from last_exc
    raise FileNotFoundError(msg)


if __name__ == "__main__":  # quick smoke-test
    logging.basicConfig(level=logging.INFO)
    df = load_data(
        google_sheet_url="https://docs.google.com/spreadsheets/d/"
                         "1eEfyVh4VmFlmV6ZzqJkJ_ZTaogoKrE8O7_1pebFHW34/edit?gid=0",
        excel_path="data/Superstore.xls",
    )
    print(df.head())
    print("Shape:", df.shape)
