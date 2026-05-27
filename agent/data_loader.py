"""
data_loader.py
--------------
Loads the Superstore dataset.

Primary source : Google Sheets (public, CSV export endpoint)
Backup source  : local Excel file (Superstore.xls / .xlsx) in data/

Why Google Sheets first:
  - Refreshes automatically — agent always sees yesterday's data.
  - No file to babysit on the user's Mac.
  - Free, no API key, no OAuth: requires sharing="Anyone with the link".

The loader is intentionally forgiving:
  1. Try the Sheets URL.
  2. If it fails (network, sharing not public, sheet deleted), log the
     reason and fall back to the local Excel file so the daily briefing
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


# Required columns the downstream pipeline expects. Other columns are
# preserved untouched.
REQUIRED_COLUMNS = [
    "Order Date",
    "Sales",
    "Profit",
    "Quantity",
    "Order ID",
    "Category",
    "Sub-Category",
    "Region",
    "Segment",
    "Ship Mode",
]


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

def _clean(df: pd.DataFrame, date_column: str = "Order Date") -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Data source is missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column])

    for col in ("Sales", "Profit", "Quantity"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Sales", "Profit", "Quantity"])

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

def load_data(
    google_sheet_url: Optional[str] = None,
    excel_path: Optional[str] = None,
    sheet_name: str = "Orders",
    date_column: str = "Order Date",
    tableau_public_url: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load Superstore data. Google Sheets first, Excel as fallback.

    google_sheet_url : any Google Sheets URL (the loader extracts the CSV)
    excel_path       : optional local file path (used only if Sheets fails)
    """
    last_exc: Optional[Exception] = None

    # 1) Try Google Sheets
    if google_sheet_url:
        try:
            df = _read_google_sheet(google_sheet_url)
            return _clean(df, date_column=date_column)
        except Exception as exc:
            log.warning(
                "Google Sheets fetch failed (%s). Will try local Excel fallback.",
                exc,
            )
            last_exc = exc

    # 2) Try local Excel
    if excel_path:
        # Try the configured extension first, then the sibling .xls/.xlsx
        candidates = [excel_path]
        base, ext = os.path.splitext(excel_path)
        if ext.lower() == ".xls":
            candidates.append(base + ".xlsx")
        elif ext.lower() == ".xlsx":
            candidates.append(base + ".xls")

        for p in candidates:
            if not os.path.exists(p):
                continue
            try:
                df = _read_excel(p, sheet_name=sheet_name)
                return _clean(df, date_column=date_column)
            except Exception as exc:
                log.warning("Could not read %s (%s) — trying next.", p, exc)
                last_exc = exc

    msg = (
        "Could not load Superstore data from any source. "
        "Google Sheet: "
        f"{'configured' if google_sheet_url else 'not configured'}. "
        f"Local Excel: "
        f"{'configured ('+excel_path+')' if excel_path else 'not configured'}. "
        "Most common fix: make sure the Google Sheet is shared as "
        "\"Anyone with the link → Viewer\". "
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
