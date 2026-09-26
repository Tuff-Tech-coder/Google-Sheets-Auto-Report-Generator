"""
Google Sheets Auto-Report Generator
=====================================
Reads data from a Google Sheet (or a local CSV in demo mode), generates a
comprehensive HTML summary report with key statistics, and emails it to a
configurable recipient list. Designed to run on a schedule (cron, Task
Scheduler, etc.) for automated weekly/monthly reporting.

Usage:
    python report_generator.py --demo              # Run against local sample_data.csv
    python report_generator.py                     # Connect to real Google Sheet
    python report_generator.py --output report.html  # Choose output path (email still enabled)

Environment variables:
    GOOGLE_SHEET_ID   The spreadsheet ID from the Google Sheet URL
    SMTP_PASSWORD     Gmail App Password for sending email alerts

See README.md for Google Sheets API setup instructions.
"""

import argparse
import datetime
import html
import json
import logging
import math
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

# Google Sheets imports — only needed when not in demo mode
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


def configure_logging(log_file: str | Path = "report.log") -> None:
    """Configure CLI logging without creating files when this module is imported."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        # Respect logging configured by an embedding application (or pytest).
        return

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_error = None
    try:
        # Sheet data is arbitrary user content, so the log file is explicitly
        # utf-8 rather than the platform default (cp1252 on Windows).
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError as exc:
        # A read-only working directory should not prevent report generation;
        # console logging remains available.
        file_error = exc

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    if file_error is not None:
        logger.warning("Could not open log file %s: %s", log_file, file_error)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_FILE = Path(__file__).parent / "report_config.json"
SAMPLE_CSV = Path(__file__).parent / "sample_data.csv"

DEFAULT_CONFIG = {
    "sheet_name": "Sheet1",
    "report_title": "Sales Performance Report",
    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "your_email@gmail.com",
        "recipients": ["recipient@example.com"],
        "subject": "Automated Sales Report — {date}"
    }
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_from_google_sheets(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    """
    Connect to Google Sheets using a service account credentials file.
    The credentials file path is read from the GOOGLE_CREDENTIALS_FILE
    environment variable, defaulting to 'credentials.json'.
    """
    if not GSPREAD_AVAILABLE:
        raise ImportError("gspread is not installed. Run: pip install gspread google-auth")

    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if not Path(creds_file).exists():
        raise FileNotFoundError(
            f"Google credentials file not found: {creds_file}\n"
            "See README.md for instructions on setting up a service account."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    client = gspread.authorize(creds)

    logger.info(f"Opening Google Sheet: {sheet_id} -> sheet: {sheet_name}")
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(sheet_name)
    data = worksheet.get_all_records()

    df = pd.DataFrame(data)
    logger.info(f"Loaded {len(df)} rows from Google Sheets")
    return df


def load_from_csv(csv_path: Path) -> pd.DataFrame:
    """Load data from a local CSV file (used in demo mode)."""
    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df)} rows from {csv_path.name}")
    return df


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
NUMERIC_COLUMNS = ("Revenue", "Profit", "Units Sold")


def _normalize_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with report metrics converted to numbers.

    Google Sheets can return otherwise numeric columns as strings when cells
    are blank or entered as text. Valid numeric strings are accepted, blank
    cells are treated as missing values, and other text raises a clear data
    error rather than failing later during aggregation or silently undercounting.
    """
    normalized = df.copy()
    for column in NUMERIC_COLUMNS:
        if column not in normalized.columns:
            continue

        raw = normalized[column]
        blank = raw.isna() | raw.astype("string").str.strip().eq("").fillna(True)
        values = raw.mask(blank, pd.NA)
        numeric = pd.to_numeric(values, errors="coerce")
        non_finite = numeric.map(lambda value: pd.notna(value) and not math.isfinite(value))
        invalid = values.notna() & (numeric.isna() | non_finite)
        if invalid.any():
            examples = ", ".join(repr(value) for value in values[invalid].head(3))
            row_labels = ", ".join(str(index) for index in values[invalid].index[:3])
            raise ValueError(
                f"Column {column!r} contains invalid numeric value(s) at row(s) "
                f"{row_labels}: {examples}"
            )
        normalized[column] = numeric

    return normalized


def generate_statistics(df: pd.DataFrame) -> dict:
    """
    Compute key business statistics from the DataFrame.
    Assumes standard column names: Revenue, Profit, Units Sold, Region,
    Salesperson, Product, Date.
    """
    if len(df.columns) > 0 and "Revenue" not in df.columns:
        raise ValueError("Required column 'Revenue' is missing")

    data = _normalize_numeric_columns(df)
    stats = {}

    # --- Overall totals ---
    stats["total_revenue"] = data["Revenue"].sum() if "Revenue" in data.columns else 0
    stats["total_profit"] = data["Profit"].sum() if "Profit" in data.columns else 0
    stats["total_units"] = data["Units Sold"].sum() if "Units Sold" in data.columns else 0
    stats["total_rows"] = len(data)

    # --- Profit margin ---
    if stats["total_revenue"] != 0:
        stats["profit_margin_pct"] = (stats["total_profit"] / stats["total_revenue"]) * 100
    else:
        stats["profit_margin_pct"] = 0

    # --- By region ---
    if "Region" in data.columns and "Revenue" in data.columns:
        stats["by_region"] = (
            data.groupby("Region")["Revenue"]
            .sum()
            .sort_values(ascending=False)
            .reset_index()
            .rename(columns={"Revenue": "Total Revenue"})
        )

    # --- By product ---
    if "Product" in data.columns and "Revenue" in data.columns:
        # Only aggregate columns that actually exist -- a sheet without
        # "Units Sold" previously raised KeyError despite the Revenue guard.
        value_cols = [c for c in ("Revenue", "Units Sold") if c in data.columns]
        stats["by_product"] = (
            data.groupby("Product")[value_cols]
            .sum()
            .sort_values("Revenue", ascending=False)
            .reset_index()
        )

    # --- Top salesperson by revenue ---
    if "Salesperson" in data.columns and "Revenue" in data.columns:
        value_cols = [c for c in ("Revenue", "Profit") if c in data.columns]
        stats["by_salesperson"] = (
            data.groupby("Salesperson")[value_cols]
            .sum()
            .sort_values("Revenue", ascending=False)
            .reset_index()
        )
        if not stats["by_salesperson"].empty:
            stats["top_salesperson"] = stats["by_salesperson"].iloc[0]["Salesperson"]
            stats["top_salesperson_revenue"] = stats["by_salesperson"].iloc[0]["Revenue"]

    # --- Date range ---
    if "Date" in data.columns:
        # Work on a copy: generate_statistics must not mutate the caller's frame.
        raw_dates = data["Date"]
        blank = raw_dates.isna() | raw_dates.astype("string").str.strip().eq("").fillna(True)
        date_values = raw_dates.mask(blank, pd.NA)
        dates = pd.to_datetime(date_values, errors="coerce", format="mixed")
        invalid = date_values.notna() & dates.isna()
        if invalid.any():
            examples = ", ".join(repr(value) for value in date_values[invalid].head(3))
            row_labels = ", ".join(str(index) for index in date_values[invalid].index[:3])
            raise ValueError(
                f"Column 'Date' contains invalid date value(s) at row(s) "
                f"{row_labels}: {examples}"
            )
        stats["date_start"] = dates.min().strftime("%b %d, %Y") if not dates.isna().all() else "N/A"
        stats["date_end"] = dates.max().strftime("%b %d, %Y") if not dates.isna().all() else "N/A"
    else:
        stats["date_start"] = stats["date_end"] = "N/A"

    return stats


def df_to_html_table(df: pd.DataFrame, format_cols: dict | None = None) -> str:
    """Convert a DataFrame to an HTML table with optional number formatting.

    Cell values and column names come from a live Google Sheet, so they are
    untrusted input: anyone who can edit the sheet controls these strings.
    Every value is escaped before interpolation -- without this, a cell
    containing markup was injected verbatim into the rendered report and into
    the email body.
    """
    format_cols = format_cols or {}
    rows_html = ""
    for _, row in df.iterrows():
        row_html = "<tr>"
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                display = ""
            elif col in format_cols:
                display = format_cols[col].format(val)
            elif isinstance(val, float):
                display = f"{val:,.2f}"
            else:
                display = str(val)
            row_html += f"<td>{html.escape(display)}</td>"
        row_html += "</tr>"
        rows_html += row_html

    headers_html = "".join(f"<th>{html.escape(str(col))}</th>" for col in df.columns)
    return f"<table><thead><tr>{headers_html}</tr></thead><tbody>{rows_html}</tbody></table>"


def render_html_report(
    stats: dict,
    config: dict,
    generated_at: datetime.datetime | None = None,
) -> str:
    """Render a complete HTML report from the computed statistics."""
    now = generated_at or datetime.datetime.now()
    title = html.escape(str(config.get("report_title") or "Sales Report"))
    date_start = html.escape(str(stats.get("date_start", "N/A")))
    date_end = html.escape(str(stats.get("date_end", "N/A")))

    # --- KPI cards ---
    kpi_html = f"""
    <div class="kpi-row">
        <div class="kpi-card">
            <div class="kpi-label">Total Revenue</div>
            <div class="kpi-value">${stats['total_revenue']:,.2f}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Profit</div>
            <div class="kpi-value green">${stats['total_profit']:,.2f}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Profit Margin</div>
            <div class="kpi-value">{stats['profit_margin_pct']:.1f}%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Units Sold</div>
            <div class="kpi-value">{int(stats['total_units']):,}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Transactions</div>
            <div class="kpi-value">{stats['total_rows']:,}</div>
        </div>
    </div>
    """

    # --- Tables ---
    region_table = ""
    if "by_region" in stats:
        # Format a copy so render_html_report stays idempotent -- formatting the
        # stats dict in place corrupted it on a second call.
        rg = stats["by_region"].copy()
        rg["Total Revenue"] = rg["Total Revenue"].map("${:,.2f}".format)
        region_table = df_to_html_table(rg)

    product_table = ""
    if "by_product" in stats:
        sp = stats["by_product"].copy()
        sp["Revenue"] = sp["Revenue"].map("${:,.2f}".format)
        if "Units Sold" in sp.columns:
            sp["Units Sold"] = sp["Units Sold"].map("{:,.0f}".format)
        product_table = df_to_html_table(sp)

    top_reps_table = ""
    if "by_salesperson" in stats:
        sr = stats["by_salesperson"].head(10).copy()
        sr["Revenue"] = sr["Revenue"].map("${:,.2f}".format)
        if "Profit" in sr.columns:
            sr["Profit"] = sr["Profit"].map("${:,.2f}".format)
        top_reps_table = df_to_html_table(sr)

    top_salesperson_callout = ""
    if "top_salesperson" in stats:
        # Sheet-derived name -- escape it like any other untrusted cell value.
        top_name = html.escape(str(stats["top_salesperson"]))
        top_salesperson_callout = f"""
        <div class="callout">
            🏆 Top Performer: <strong>{top_name}</strong>
            with <strong>${stats['top_salesperson_revenue']:,.2f}</strong> in revenue
        </div>
        """

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         margin: 0; padding: 0; background: #f5f7fa; color: #333; }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 24px; }}
  .header {{ background: linear-gradient(135deg, #1a3a5c 0%, #2d6a9f 100%);
             color: white; padding: 32px; border-radius: 8px; margin-bottom: 24px; }}
  .header h1 {{ margin: 0 0 8px 0; font-size: 28px; }}
  .header p {{ margin: 0; opacity: 0.8; font-size: 14px; }}
  .kpi-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .kpi-card {{ background: white; border-radius: 8px; padding: 20px 24px;
               flex: 1; min-width: 140px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .kpi-label {{ font-size: 12px; color: #888; text-transform: uppercase;
                letter-spacing: 0.05em; margin-bottom: 8px; }}
  .kpi-value {{ font-size: 24px; font-weight: 700; color: #1a3a5c; }}
  .kpi-value.green {{ color: #2d9e6b; }}
  .section {{ background: white; border-radius: 8px; padding: 24px;
              margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .section h2 {{ margin: 0 0 16px 0; font-size: 18px; color: #1a3a5c; border-bottom: 2px solid #e8edf2; padding-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  thead th {{ background: #1a3a5c; color: white; padding: 10px 14px; text-align: left; }}
  tbody tr:nth-child(even) {{ background: #f7fafc; }}
  tbody td {{ padding: 9px 14px; border-bottom: 1px solid #eee; }}
  .callout {{ background: #fef9e7; border-left: 4px solid #f1c40f; padding: 14px 18px;
              border-radius: 4px; margin-bottom: 16px; }}
  .footer {{ text-align: center; color: #aaa; font-size: 12px; margin-top: 32px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}</h1>
    <p>Period: {date_start} – {date_end}
       &nbsp;|&nbsp; Generated: {now.strftime('%B %d, %Y at %I:%M %p')}</p>
  </div>

  {kpi_html}
  {top_salesperson_callout}

  <div class="section">
    <h2>Revenue by Region</h2>
    {region_table}
  </div>

  <div class="section">
    <h2>Revenue by Product</h2>
    {product_table}
  </div>

  <div class="section">
    <h2>Top Sales Representatives</h2>
    {top_reps_table}
  </div>

  <div class="footer">
    <p>This report was generated automatically by the Google Sheets Auto-Reporter.</p>
  </div>
</div>
</body>
</html>"""
    return document


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------
def send_report_email(html_content: str, config: dict) -> None:
    """Send the HTML report as an email."""
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_password:
        raise RuntimeError("SMTP_PASSWORD is required unless --no-email is used")

    email_cfg = config["email"]
    now = datetime.datetime.now()
    subject = email_cfg.get("subject", "Automated Report — {date}").format(
        date=now.strftime("%Y-%m-%d")
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg["sender_email"]
    msg["To"] = ", ".join(email_cfg["recipients"])
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP(
            email_cfg["smtp_server"], email_cfg["smtp_port"], timeout=30
        ) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(email_cfg["sender_email"], smtp_password)
            server.sendmail(
                email_cfg["sender_email"],
                email_cfg["recipients"],
                msg.as_string(),
            )
        logger.info(f"Report emailed to: {', '.join(email_cfg['recipients'])}")
    except Exception as e:
        logger.error(f"Email failed: {e}")
        raise


def save_report(html_content: str, output_path: str | Path) -> Path:
    """Write a report as UTF-8, creating requested parent directories."""
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Google Sheets Auto-Report Generator")
    parser.add_argument("--demo", action="store_true",
                        help="Run against local sample_data.csv (no Google credentials needed)")
    parser.add_argument("--output", default=None,
                        help="Save HTML report to this file (e.g. report.html)")
    parser.add_argument("--no-email", action="store_true",
                        help="Generate report but do not send email")
    args = parser.parse_args()

    config = load_config()

    # --- Load data ---
    if args.demo:
        logger.info("Demo mode: loading from sample_data.csv")
        df = load_from_csv(SAMPLE_CSV)
    else:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
        if not sheet_id:
            parser.error("GOOGLE_SHEET_ID is required unless --demo is used")
        sheet_name = config.get("sheet_name", "Sheet1")
        df = load_from_google_sheets(sheet_id, sheet_name)

    # --- Generate report ---
    logger.info("Computing statistics...")
    stats = generate_statistics(df)
    report_html = render_html_report(stats, config)

    # --- Save HTML file ---
    output_path = save_report(report_html, args.output or "report.html")
    logger.info(f"Report saved to: {output_path}")

    # --- Email ---
    if not args.no_email:
        send_report_email(report_html, config)

    print(f"\n[OK] Report generated: {output_path}")
    print(f"  Total Revenue : ${stats['total_revenue']:,.2f}")
    print(f"  Total Profit  : ${stats['total_profit']:,.2f}")
    print(f"  Profit Margin : {stats['profit_margin_pct']:.1f}%")
    print(f"  Top Performer : {stats.get('top_salesperson', 'N/A')}")


if __name__ == "__main__":
    configure_logging()
    main()
