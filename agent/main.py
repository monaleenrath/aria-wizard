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
from agent.driver_analysis import analyze_drivers, drivers_to_dict
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

    drivers_yoy = analyze_drivers(df, config, ref, compare_to="yoy")
    drivers_dod = analyze_drivers(df, config, ref, compare_to="dod")

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

    payload = {
        **snapshot.to_dict(),
        "drivers": drivers_to_dict(drivers_yoy),
        "drivers_dod": drivers_to_dict(drivers_dod),
        "daily_sales_30d": daily_sales_30d,
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
