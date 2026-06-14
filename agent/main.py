"""
main.py
-------
Orchestrator. Runs the full daily briefing pipeline:

    load data → compute KPIs → analyze drivers → generate narrative
              → generate Dark Editorial SVG card → convert to PNG
              → persist to file → post image to Slack
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# Allow running as a script: `python agent/main.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.data_loader import load_data
from agent.metrics_engine import compute_metrics
try:
    from agent.driver_analysis import analyze_drivers, drivers_to_dict, DRIVER_ANALYSIS_VERSION
except ImportError:
    # Backwards-compatible: old driver_analysis.py doesn't export the version constant
    from agent.driver_analysis import analyze_drivers, drivers_to_dict
    DRIVER_ANALYSIS_VERSION = "MISSING-pre-v2-no-autodetect"
from agent.narrative_generator import generate_narrative
from agent.report_writer import write_markdown, write_docx
from agent.slack_publisher import post_image_to_slack, post_to_slack, render_slack_preview
from agent.html_generator import generate_html_card, html_to_png
from agent.teams_publisher import post_to_teams


def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.FileHandler("logs/agent.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_roles(path: str = "roles.yaml") -> dict:
    """Load role profiles. Returns empty dict if file not found."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            return data.get("roles", {})
    except FileNotFoundError:
        log.warning("roles.yaml not found — running with default generic role.")
        return {}


def run(config_path: str = "config.yaml", dry_run: bool = False,
        date_override: Optional[str] = None) -> dict:
    setup_logging()
    log = logging.getLogger("agent.main")

    load_dotenv()
    config = load_config(config_path)
    roles  = load_roles("roles.yaml")

    # 1 — DATA ------------------------------------------------------------- #
    # Collect KPI columns from config so data_loader can coerce them to numeric
    _kpi_columns = [
        k.get("column") for k in config.get("metrics", {}).get("kpis", [])
        if k.get("column")
    ]
    df = load_data(
        google_sheet_url=config["data"].get("google_sheet_url"),
        excel_path=config["data"].get("excel_path"),
        sheet_name=config["data"].get("sheet_name", "Sheet1"),
        date_column=config["data"]["date_column"],
        kpi_columns=_kpi_columns,
    )

    # 2 — METRICS ---------------------------------------------------------- #
    ref_date_obj = None
    if date_override:
        import datetime as _dt
        ref_date_obj = _dt.date.fromisoformat(date_override)
        log.info("Date override supplied via CLI: %s", ref_date_obj)
    snapshot = compute_metrics(df, config, reference_date=ref_date_obj)
    log.info("Reference date: %s | KPIs: %d | Anomalies: %d",
             snapshot.reference_date, len(snapshot.kpis), len(snapshot.anomalies))

    # 3 — DRIVERS ---------------------------------------------------------- #
    import datetime as _dt
    ref = _dt.date.fromisoformat(snapshot.reference_date)

    _yoy_diag: dict = {}
    drivers_yoy = analyze_drivers(df, config, ref, compare_to="yoy",
                                  _diag_out=_yoy_diag)
    drivers_dod = analyze_drivers(df, config, ref, compare_to="dod")

    log.info("drivers_yoy summary: %s",
             {k: len(v) for k, v in drivers_yoy.items()})
    log.info("config dimensions: %s",
             config.get("drivers", {}).get("dimensions", []))
    log.info("config kpis (name/column/agg): %s",
             [(k.get("name"), k.get("column","?"), k.get("agg","?"))
              for k in config.get("metrics", {}).get("kpis", [])])

    # 4 — NARRATIVE -------------------------------------------------------- #
    # Compute last-30-days daily sales for the sparkline in the SVG card
    import datetime as _dt2
    import pandas as _pd
    date_col = config["data"]["date_column"]
    thirty_days_ago = _dt2.date.fromisoformat(snapshot.reference_date) - _dt2.timedelta(days=29)
    df_spark = df.copy()
    df_spark[date_col] = _pd.to_datetime(df_spark[date_col], errors="coerce")
    df_spark = df_spark[
        (df_spark[date_col].dt.date >= thirty_days_ago) &
        (df_spark[date_col].dt.date <= _dt2.date.fromisoformat(snapshot.reference_date))
    ]
    # Use the first "sum" KPI column for the sparkline (falls back to "Sales" if present)
    _spark_col = None
    for _kpi in config.get("metrics", {}).get("kpis", []):
        if _kpi.get("agg") == "sum" and _kpi.get("column") in df_spark.columns:
            _spark_col = _kpi["column"]
            break
    if _spark_col is None and "Sales" in df_spark.columns:
        _spark_col = "Sales"
    daily_sales_30d = (
        df_spark.groupby(df_spark[date_col].dt.date)[_spark_col]
        .sum()
        .sort_index()
        .tolist()
    ) if _spark_col else []

    # Monthly revenue for the past 12 months — used by dossier/scorecard trend chart.
    # Much smoother than daily data and more meaningful for leadership briefings.
    monthly_revenue_12m = {"labels": [], "values": [], "col": _spark_col or ""}
    if _spark_col:
        try:
            df_monthly = df.copy()
            df_monthly[date_col] = _pd.to_datetime(df_monthly[date_col], errors="coerce")
            df_monthly = df_monthly.dropna(subset=[date_col])
            df_monthly["_month"] = df_monthly[date_col].dt.to_period("M")
            monthly = (
                df_monthly.groupby("_month")[_spark_col]
                .sum()
                .sort_index()
                .tail(12)
            )
            monthly_revenue_12m = {
                "labels": [str(p) for p in monthly.index],   # e.g. "2024-01"
                "values": [round(float(v), 2) for v in monthly.values],
                "col": _spark_col,
            }
        except Exception:
            pass  # fall back to empty — html_generator will use daily_sales_30d

    # Per-KPI time series (scorecard: each tile shows its own KPI's sparkline)
    per_kpi_series: dict = {}
    per_kpi_dates:  list = []
    try:
        _ref_d   = _dt2.date.fromisoformat(snapshot.reference_date)
        _start_d = _dt2.date.fromisoformat(snapshot.window_start)
        _df_ts = df.copy()
        _df_ts[date_col] = _pd.to_datetime(_df_ts[date_col], errors="coerce")
        _df_ts["_date"] = _df_ts[date_col].dt.date
        _df_ts = _df_ts[(_df_ts["_date"] >= _start_d) & (_df_ts["_date"] <= _ref_d)]
        per_kpi_dates = [str(d) for d in sorted(_df_ts["_date"].unique().tolist())]
        for _kc in config.get("metrics", {}).get("kpis", []):
            _kname = _kc.get("name", ""); _col = _kc.get("column", "")
            _agg = _kc.get("agg", "sum"); _num = _kc.get("num_col", "")
            _den = _kc.get("den_col", ""); _sc = float(_kc.get("scale", 1))
            if not _kname: continue
            try:
                if _agg == "sum" and _col in _df_ts.columns:
                    _s = _df_ts.groupby("_date")[_col].sum().sort_index()
                    per_kpi_series[_kname] = [round(float(v) * _sc, 4) for v in _s.values]
                elif _agg in ("avg", "mean") and _col in _df_ts.columns:
                    _s = _df_ts.groupby("_date")[_col].mean().sort_index()
                    per_kpi_series[_kname] = [round(float(v) * _sc, 4) for v in _s.values]
                elif _agg in ("ratio", "pct") and _num in _df_ts.columns and _den in _df_ts.columns:
                    _dn = _df_ts.groupby("_date")[_num].sum()
                    _dd = _df_ts.groupby("_date")[_den].sum()
                    _r  = (_dn / _dd.where(_dd > 0)).fillna(0) * _sc
                    per_kpi_series[_kname] = [round(float(v), 4) for v in _r.sort_index().values]
            except Exception:
                pass
    except Exception:
        per_kpi_series = {}
        per_kpi_dates  = []

    # Per-dimension-member KPI values (scorecard filter: values + MoM + YoY per member)
    dim_member_kpis: dict = {}
    try:
        from datetime import timedelta as _td2
        _ref_d   = _dt2.date.fromisoformat(snapshot.reference_date)
        _start_d = _dt2.date.fromisoformat(snapshot.window_start)
        _df_filt = df.copy()
        _df_filt[date_col] = _pd.to_datetime(_df_filt[date_col], errors="coerce")
        _df_filt["_date"] = _df_filt[date_col].dt.date
        _df_curr = _df_filt[(_df_filt["_date"] >= _start_d) & (_df_filt["_date"] <= _ref_d)]

        # Prior period date ranges for MoM/YoY
        _win_days      = (_ref_d - _start_d).days
        _prior_m_end   = _start_d - _td2(days=1)
        _prior_m_start = _prior_m_end - _td2(days=_win_days)
        _prior_y_start = _start_d - _td2(days=365)
        _prior_y_end   = _ref_d - _td2(days=365)
        _df_prior_m = _df_filt[(_df_filt["_date"] >= _prior_m_start) &
                                (_df_filt["_date"] <= _prior_m_end)]
        _df_prior_y = _df_filt[(_df_filt["_date"] >= _prior_y_start) &
                                (_df_filt["_date"] <= _prior_y_end)]

        _cfg_dims = config.get("drivers", {}).get("dimensions", [])
        _cfg_kpis = config.get("metrics", {}).get("kpis", [])

        # If config has no driver dims, reuse effective_dims from driver analysis
        # (already auto-detected by analyze_drivers — more reliable than re-deriving)
        if not _cfg_dims:
            _eff = _yoy_diag.get("effective_dims", [])
            # Keep dims that exist in the current df AND have ≤8 unique values
            # (filters out high-cardinality ID cols like Store ID / Store Name)
            _cfg_dims = [d for d in _eff
                         if d in _df_curr.columns
                         and _df_curr[d].nunique() <= 8][:4]
            log.info("dim_member_kpis auto-dims (from yoy_diag.effective_dims): %s",
                     _cfg_dims)

        def _kpi_val_for_subset(df_sub, kpi_cfg, df_pm=None, df_py=None):
            col = kpi_cfg.get("column",""); agg = kpi_cfg.get("agg","sum")
            num = kpi_cfg.get("num_col",""); den = kpi_cfg.get("den_col","")
            scale = float(kpi_cfg.get("scale",1)); fmt = kpi_cfg.get("format","number")

            def _cv(ds):
                try:
                    if agg == "sum" and col in ds.columns:
                        return float(ds[col].sum()) * scale
                    if agg in ("avg", "mean") and col in ds.columns:
                        return float(ds[col].mean()) * scale if len(ds) > 0 else None
                    if agg in ("ratio","pct") and num in ds.columns and den in ds.columns:
                        n = float(ds[num].sum()); d = float(ds[den].sum())
                        return (n / d * scale) if d else 0.0
                    return None
                except Exception:
                    return None

            val = _cv(df_sub)
            if val is None:
                return None
            if fmt == "currency":
                vfmt = (f"${val/1e6:.1f}M" if abs(val)>=1e6
                        else f"${val/1e3:.0f}K" if abs(val)>=1e3 else f"${val:,.0f}")
            elif fmt == "percent":
                vfmt = f"{val:.1%}"
            elif fmt == "integer":
                vfmt = f"{int(val):,}"
            else:
                vfmt = (f"{val/1e6:.1f}M" if abs(val)>=1e6
                        else f"{val/1e3:.0f}K" if abs(val)>=1e3 else f"{val:,.2f}")
            mom_pct = yoy_pct = None
            if df_pm is not None and len(df_pm) > 0:
                pv = _cv(df_pm)
                if pv and pv != 0:
                    mom_pct = round((val / pv - 1) * 100, 2)
            if df_py is not None and len(df_py) > 0:
                pv = _cv(df_py)
                if pv and pv != 0:
                    yoy_pct = round((val / pv - 1) * 100, 2)
            return {"value": round(val,4), "value_fmt": vfmt,
                    "mom_pct": mom_pct, "yoy_pct": yoy_pct}

        for _dim in _cfg_dims:
            if _dim not in _df_curr.columns: continue
            dim_member_kpis[_dim] = {}
            _members = sorted(_df_curr[_dim].dropna().unique().tolist(), key=str)
            for _member in _members:
                _df_m  = _df_curr[_df_curr[_dim] == _member]
                _df_pm = _df_prior_m[_df_prior_m[_dim] == _member] \
                         if _dim in _df_prior_m.columns else _pd.DataFrame()
                _df_py = _df_prior_y[_df_prior_y[_dim] == _member] \
                         if _dim in _df_prior_y.columns else _pd.DataFrame()
                _mkpis = {}
                for _kc in _cfg_kpis:
                    _kn = _kc.get("name","")
                    if not _kn: continue
                    _res = _kpi_val_for_subset(_df_m, _kc, _df_pm, _df_py)
                    if _res: _mkpis[_kn] = _res
                dim_member_kpis[_dim][str(_member)] = _mkpis
    except Exception:
        log.exception("dim_member_kpis computation failed — filter will be inactive")
        dim_member_kpis = {}
        _cfg_dims = []   # ensure variable exists for debug dict below

    payload = {
        **snapshot.to_dict(),
        "drivers": drivers_to_dict(drivers_yoy),
        "drivers_dod": drivers_to_dict(drivers_dod),
        "daily_sales_30d":     daily_sales_30d,
        "monthly_revenue_12m": monthly_revenue_12m,
        "per_kpi_series":      per_kpi_series,
        "per_kpi_dates":       per_kpi_dates,
        "dim_member_kpis":     dim_member_kpis,
        "_aria_debug": {
            "da_ver": DRIVER_ANALYSIS_VERSION,
            "cfg_dims": _cfg_dims,   # actual dims used (after auto-detect fallback)
            "df_cols": list(df.columns),
            "df_shape": [int(df.shape[0]), int(df.shape[1])],
            "kpi_map": [
                (k.get("name"), k.get("column", "?"),
                 k.get("agg", "?"), k.get("num_col", ""))
                for k in config.get("metrics", {}).get("kpis", [])
            ],
            "yoy_summary": {k: len(v) for k, v in drivers_yoy.items()},
            "yoy_diag": _yoy_diag,
            # For debugging auto-detect: dtype of every low-cardinality non-KPI column
            "df_cat_cols": {
                c: f"{df[c].dtype}/{df[c].nunique()}"
                for c in df.columns
                if c != config["data"]["date_column"]
                and df[c].nunique() < 50
                and c not in _kpi_columns
            },
        },
    }

    # 5 — PERSIST raw payload (shared across all roles) ─────────────────── #
    out_dir      = config["delivery"]["file"]["output_dir"]
    payload_path = os.path.join(out_dir, f"payload_{snapshot.reference_date}.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(payload_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    # 6 — PER-ROLE LOOP ─────────────────────────────────────────────────── #
    # Generate one narrative + one card per role defined in roles.yaml.
    # If no roles are configured, fall back to a single generic card.
    channels = config["delivery"]["channels"]

    role_items = list(roles.items()) if roles else [("General", None)]
    all_results = {}

    # Playwright availability is checked inside html_to_png() — no pre-check needed

    for role_name, role_cfg in role_items:
        log.info("── Processing role: %s ──────────────────────────────────", role_name)

        # Narrative
        narrative = generate_narrative(payload, config, role_cfg)
        narrative.reference_date = snapshot.reference_date
        markdown  = narrative.to_markdown()
        log.info("Narrative generated by %s (%d chars)", narrative.model, len(markdown))

        # Files
        safe_role = role_name.lower().replace(" ", "_")
        md_path   = write_markdown(markdown, out_dir,
                                   f"{snapshot.reference_date}_{safe_role}")
        docx_path = write_docx(narrative, out_dir,
                               f"{snapshot.reference_date}_{safe_role}")

        # HTML → PNG via Playwright
        html_string = generate_html_card(narrative, payload, config, role_cfg)
        html_path   = os.path.join(out_dir, f"briefing_{snapshot.reference_date}_{safe_role}.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html_string)
        log.info("HTML card written: %s", html_path)

        png_bytes: Optional[bytes] = None
        png_path:  Optional[str]   = None
        try:
            png_bytes = html_to_png(html_string, width=900, height=520)
            png_path  = os.path.join(out_dir,
                        f"briefing_{snapshot.reference_date}_{safe_role}.png")
            with open(png_path, "wb") as fh:
                fh.write(png_bytes)
            log.info("PNG card written: %s (%d bytes)", png_path, len(png_bytes))
        except Exception as exc:
            log.error("PNG conversion failed for %s: %s", role_name, exc)

        # Deliver
        role_delivery: dict = {}
        # Per-role Slack channel — empty string means "not configured"
        _raw_channel = (role_cfg or {}).get("slack_channel", "")
        role_channel = _raw_channel if _raw_channel else None
        _multi_role  = len(role_items) > 1

        if dry_run:
            if "slack" in channels:
                preview = render_slack_preview(narrative, config)
                print(f"\n{'='*72}\nDRY RUN — {role_name}\n{'='*72}")
                print(preview)
                role_delivery["slack"] = {"status": "previewed (dry-run)"}
            if "file" in channels:
                role_delivery["file"] = {"status": "written"}
        else:
            if "slack" in channels:
                # Safety guard: in multi-role setups, skip Slack when no channel
                # is set for this role rather than falling back to a shared env var
                # (which would route all un-configured roles to the same channel).
                if _multi_role and not role_channel:
                    log.warning(
                        "Role '%s' has no slack_channel in roles.yaml — "
                        "Slack skipped to avoid cross-channel delivery.",
                        role_name,
                    )
                    role_delivery["slack"] = {
                        "status": "skipped",
                        "reason": f"slack_channel not set for role '{role_name}' in roles.yaml",
                    }
                elif png_bytes:
                    badge   = (role_cfg or {}).get("badge", role_name)
                    comment = f"*{badge}  ·  {snapshot.reference_date}*"
                    role_delivery["slack"] = post_image_to_slack(
                        png_bytes, narrative, config,
                        channel=role_channel,
                        initial_comment=comment,
                    )
                else:
                    log.error("PNG not available for %s — Slack skipped.", role_name)
                    role_delivery["slack"] = {
                        "status": "skipped", "reason": "PNG generation failed"
                    }
            if "teams" in channels:
                role_delivery["teams"] = post_to_teams(narrative, config)

        log.info("Delivery results for %s: %s", role_name, role_delivery)

        # Throttle: small pause between roles to respect Gemini free-tier RPM.
        log.info("Waiting 2 s before next role...")
        time.sleep(2)

        all_results[role_name] = {
            "headline": narrative.headline,
            "files": {
                "markdown": md_path,
                "docx":     docx_path,
                "html":     html_path,
                "png":      png_path,
            },
            "delivery": role_delivery,
        }

    return {
        "reference_date": snapshot.reference_date,
        "payload": payload_path,
        "roles": all_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Board Room Narrator Agent")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Teams delivery")
    parser.add_argument("--date", default=None,
                        help="Override reference date (YYYY-MM-DD). "
                             "Default: yesterday in the configured timezone.")
    args = parser.parse_args()

    result = run(args.config, dry_run=args.dry_run, date_override=args.date)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
