# Google Sheets Auto-Report Generator

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Google Sheets API](https://img.shields.io/badge/Google%20Sheets-API-34A853?logo=googlesheets&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-3.x-150458?logo=pandas&logoColor=white)
![Tests](https://img.shields.io/badge/tests-34%20passing-brightgreen)
![License](https://img.shields.io/badge/License-MIT-green)

An automated reporting tool that reads business data straight from a Google Sheet, computes the key metrics, renders a polished HTML report, and emails it to a recipient list — all on a schedule. Point it at a sheet that your team already updates, and stakeholders get a clean weekly or monthly summary without anyone touching a spreadsheet.

A built-in **demo mode** runs the entire pipeline against a local CSV, so the output can be evaluated with no Google credentials at all.

---

## Why it's useful

The data is usually already in a shared sheet — what's missing is the discipline to summarize and circulate it on time. This tool closes that loop: scheduled, consistent, formatted reporting that turns a live spreadsheet into a stakeholder-ready email automatically.

---

## Features

- **Live Google Sheets integration** via `gspread` with service-account authentication.
- **Automatic KPI computation** — total revenue, profit, profit margin, units sold, and transaction count.
- **Breakdowns** by region and by product, plus a ranked top-10 sales reps table and a "top performer" callout.
- **Polished HTML report** — KPI cards, styled tables, and a date-range header for browser and email viewing.
- **SMTP email delivery** to a configurable recipient list.
- **Scheduling-ready** — designed to run under cron or Task Scheduler for hands-off weekly/monthly reports.
- **Credential-free demo mode** — `--demo` runs against `sample_data.csv` and produces the same report locally.

---

## Tech stack

`Python` · `gspread` · `google-auth` · `pandas` · `smtplib` (SMTP/TLS) · `argparse` · `logging` · HTML/CSS

---

## Project structure

```
report_generator.py    # Main script
report_config.json     # Report title, sheet name, email settings
sample_data.csv        # Demo dataset (30 rows of sales data)
sample_report.html     # Pre-generated sample report
requirements.txt       # Python dependencies
tests/                 # 34 unit tests
report.log             # Generated: application log
```

---

## Quick start (demo mode)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python report_generator.py --demo --no-email
# → writes report.html from the local CSV
```

---

## Connecting a real Google Sheet

1. In Google Cloud, enable the **Google Sheets API** and create a **service account**; download its JSON key.
2. Share your sheet with the service account's email address (Viewer is enough).
3. Set the environment variables:

```bash
export GOOGLE_SHEET_ID="your_sheet_id_from_the_url"
export GOOGLE_CREDENTIALS_FILE="credentials.json"
export SMTP_PASSWORD="your_gmail_app_password"
```

4. Edit `report_config.json` for the sheet name, report title, and recipients.

> The addresses shipped in `report_config.json` (`your_email@gmail.com`,
> `manager@yourcompany.com`, `director@yourcompany.com`) are **placeholders**, not
> real recipients. Replace them before enabling email, or run with `--no-email`.

---

## Usage

```bash
python report_generator.py                 # Live sheet, generate + email
python report_generator.py --no-email      # Live sheet, save HTML only
python report_generator.py --demo          # Local CSV, full pipeline
python report_generator.py --output weekly_report.html
```

Live mode requires `GOOGLE_SHEET_ID`; if it is missing, the command exits with
an error instead of silently sending the bundled demo data. Email mode likewise
fails clearly when `SMTP_PASSWORD` is missing or delivery fails. Use `--demo`
and `--no-email` explicitly for a credential-free local run.

**Expected columns:** `Date`, `Region`, `Product`, `Salesperson`, `Revenue`, `Profit`, `Units Sold`.

### Schedule it (cron example — every Monday at 8am)

```cron
0 8 * * 1  cd /path/to/Google-Sheets-Auto-Report-Generator && ./venv/bin/python report_generator.py
```

---

## How it works

1. Loads data from Google Sheets (or the sample CSV in demo mode) into a pandas DataFrame.
2. Computes totals, margin, and grouped breakdowns by region, product, and salesperson.
3. Renders a styled HTML report with KPI cards, a top-performer callout, and tables.
4. Saves the HTML and emails it over SMTP/TLS to the configured recipients.

---

## Development

```bash
pip install -r requirements.txt
pip install pytest ruff

pytest -q          # 34 tests
ruff check .
```

The suite covers KPI computation, mixed and partial sheet data, HTML escaping,
file output, command-line safety checks, and SMTP failure/TLS behavior. No
Google credentials or network access are required.

---

## Possible extensions

Add charts (matplotlib/Plotly), period-over-period comparisons, PDF export, or Slack delivery alongside email.
