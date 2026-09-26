"""Tests for KPI computation and HTML report rendering."""
import datetime
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_generator  # noqa: E402
from report_generator import (  # noqa: E402
    DEFAULT_CONFIG,
    df_to_html_table,
    generate_statistics,
    load_config,
    render_html_report,
    save_report,
    send_report_email,
)


@pytest.fixture
def sales_df():
    return pd.DataFrame({
        "Date": ["2024-01-05", "2024-02-11", "2024-03-02", "2024-03-20"],
        "Region": ["North", "South", "North", "West"],
        "Product": ["Widget", "Gadget", "Widget", "Gizmo"],
        "Salesperson": ["Ana", "Ben", "Ana", "Cy"],
        "Revenue": [1000.0, 2000.0, 3000.0, 500.0],
        "Profit": [400.0, 600.0, 900.0, 100.0],
        "Units Sold": [10, 20, 30, 5],
    })


class TestGenerateStatistics:
    def test_totals_and_margin(self, sales_df):
        s = generate_statistics(sales_df)
        assert s["total_revenue"] == 6500.0
        assert s["total_profit"] == 2000.0
        assert s["total_units"] == 65
        assert s["total_rows"] == 4
        assert s["profit_margin_pct"] == pytest.approx(2000 / 6500 * 100)

    def test_top_salesperson_ranked_by_revenue(self, sales_df):
        s = generate_statistics(sales_df)
        assert s["top_salesperson"] == "Ana"
        assert s["top_salesperson_revenue"] == 4000.0

    def test_region_breakdown_sorted_desc(self, sales_df):
        s = generate_statistics(sales_df)
        assert list(s["by_region"]["Region"]) == ["North", "South", "West"]
        assert s["by_region"]["Total Revenue"].iloc[0] == 4000.0

    def test_date_range(self, sales_df):
        s = generate_statistics(sales_df)
        assert s["date_start"] == "Jan 05, 2024"
        assert s["date_end"] == "Mar 20, 2024"

    def test_mixed_valid_date_formats_are_included(self):
        df = pd.DataFrame({
            "Date": ["2024-12-31", "01/01/2025"],
            "Revenue": [100, 200],
        })
        s = generate_statistics(df)
        assert s["date_start"] == "Dec 31, 2024"
        assert s["date_end"] == "Jan 01, 2025"

    def test_invalid_nonblank_date_has_a_clear_error(self):
        df = pd.DataFrame({"Date": ["not-a-date"], "Revenue": [100]})
        with pytest.raises(ValueError, match=r"Date.*row\(s\).*not-a-date"):
            generate_statistics(df)

    def test_missing_units_sold_does_not_raise(self, sales_df):
        """Regression: previously KeyError despite the Revenue-only guard."""
        s = generate_statistics(sales_df.drop(columns=["Units Sold"]))
        assert s["total_units"] == 0
        assert "Units Sold" not in s["by_product"].columns
        assert list(s["by_product"]["Product"])[0] == "Widget"

    def test_missing_profit_does_not_break_salesperson_breakdown(self, sales_df):
        """Regression: salesperson aggregation unconditionally selected Profit."""
        s = generate_statistics(sales_df.drop(columns=["Profit"]))
        assert s["total_profit"] == 0
        assert list(s["by_salesperson"].columns) == ["Salesperson", "Revenue"]
        assert s["top_salesperson"] == "Ana"

    def test_header_only_sheet_does_not_select_a_missing_top_row(self):
        df = pd.DataFrame(columns=["Salesperson", "Revenue", "Profit"])
        s = generate_statistics(df)
        assert s["by_salesperson"].empty
        assert "top_salesperson" not in s

    def test_numeric_text_and_blank_cells_are_normalized(self):
        df = pd.DataFrame({
            "Salesperson": ["Ana", "Ben", "Cy"],
            "Revenue": ["100.50", "200", ""],
            "Profit": ["25.25", "50", None],
            "Units Sold": ["2", "3", " "],
        })
        s = generate_statistics(df)
        assert s["total_revenue"] == pytest.approx(300.50)
        assert s["total_profit"] == pytest.approx(75.25)
        assert s["total_units"] == 5
        assert s["top_salesperson"] == "Ben"

    def test_invalid_numeric_text_has_a_clear_error(self):
        df = pd.DataFrame({"Revenue": ["100", "not-a-number"]})
        with pytest.raises(ValueError, match=r"Revenue.*row\(s\).*not-a-number"):
            generate_statistics(df)

    def test_non_finite_numbers_are_rejected_before_rendering(self):
        df = pd.DataFrame({"Revenue": [100], "Units Sold": [float("inf")]})
        with pytest.raises(ValueError, match=r"Units Sold.*inf"):
            generate_statistics(df)

    def test_does_not_mutate_input(self, sales_df):
        before = sales_df["Date"].tolist()
        generate_statistics(sales_df)
        assert sales_df["Date"].tolist() == before, "input DataFrame was mutated"

    def test_empty_frame_is_safe(self):
        s = generate_statistics(pd.DataFrame())
        assert s["total_revenue"] == 0
        assert s["profit_margin_pct"] == 0
        assert s["total_rows"] == 0

    def test_nonempty_schema_requires_revenue(self):
        with pytest.raises(ValueError, match="Required column 'Revenue'"):
            generate_statistics(pd.DataFrame({"Profit": [10]}))

    def test_zero_revenue_no_division_error(self):
        df = pd.DataFrame({"Revenue": [0.0], "Profit": [0.0]})
        assert generate_statistics(df)["profit_margin_pct"] == 0


class TestRenderHtmlReport:
    def test_produces_valid_document_with_kpis(self, sales_df):
        html = render_html_report(generate_statistics(sales_df), load_config())
        assert html.startswith("<!DOCTYPE html>")
        assert "$6,500.00" in html
        assert "Ana" in html
        assert html.count("<table") >= 3

    def test_is_idempotent(self, sales_df):
        """Regression: formatting used to mutate stats, so call 2 differed."""
        stats = generate_statistics(sales_df)
        generated_at = datetime.datetime(2026, 1, 2, 3, 4)
        assert render_html_report(stats, load_config(), generated_at) == render_html_report(
            stats, load_config(), generated_at
        )

    def test_renders_without_units_sold(self, sales_df):
        stats = generate_statistics(sales_df.drop(columns=["Units Sold"]))
        assert "<!DOCTYPE html>" in render_html_report(stats, load_config())

    def test_renders_without_profit(self, sales_df):
        stats = generate_statistics(sales_df.drop(columns=["Profit"]))
        assert "<!DOCTYPE html>" in render_html_report(stats, load_config())

    def test_escapes_report_title(self, sales_df):
        payload = '<img src=x onerror="alert(1)">'
        out = render_html_report(
            generate_statistics(sales_df),
            {"report_title": payload},
        )
        assert payload not in out
        assert "&lt;img src=x" in out


def test_df_to_html_table_escapes_structure():
    html = df_to_html_table(pd.DataFrame({"A": [1], "B": ["x"]}))
    assert "<th>A</th>" in html and "<td>x</td>" in html


def test_df_to_html_table_formats_integer_columns():
    html = df_to_html_table(pd.DataFrame({"Count": [1000]}), {"Count": "{:,.0f}"})
    assert "<td>1,000</td>" in html


class TestHtmlEscaping:
    """Sheet contents are untrusted: anyone with edit access controls them."""

    PAYLOAD = '<img src=x onerror="alert(1)">'

    def test_cell_values_are_escaped(self):
        out = df_to_html_table(pd.DataFrame({"Name": [self.PAYLOAD]}))
        assert self.PAYLOAD not in out
        assert "&lt;img src=x" in out

    def test_column_names_are_escaped(self):
        out = df_to_html_table(pd.DataFrame({"<script>x</script>": [1]}))
        assert "<script>" not in out

    def test_report_does_not_embed_raw_markup_from_a_cell(self, sales_df):
        df = sales_df.copy()
        df.loc[0, "Salesperson"] = self.PAYLOAD
        out = render_html_report(generate_statistics(df), dict(DEFAULT_CONFIG))
        assert self.PAYLOAD not in out
        assert 'onerror="alert(1)"' not in out

    def test_ordinary_values_survive_escaping(self):
        out = df_to_html_table(pd.DataFrame({"Name": ["Acme & Co"]}))
        assert "Acme &amp; Co" in out


class TestDeliveryAndCliFailures:
    def test_missing_smtp_password_is_an_error(self, monkeypatch):
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
            send_report_email("<p>report</p>", DEFAULT_CONFIG)

    def test_smtp_failure_is_not_swallowed(self, monkeypatch):
        monkeypatch.setenv("SMTP_PASSWORD", "not-a-real-secret")

        def fail_to_connect(*args, **kwargs):
            raise OSError("offline")

        monkeypatch.setattr(report_generator.smtplib, "SMTP", fail_to_connect)
        with pytest.raises(OSError, match="offline"):
            send_report_email("<p>report</p>", DEFAULT_CONFIG)

    def test_smtp_uses_verified_tls_and_a_timeout(self, monkeypatch):
        monkeypatch.setenv("SMTP_PASSWORD", "not-a-real-secret")
        calls = {}
        tls_context = object()

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                calls.update(host=host, port=port, timeout=timeout)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ehlo(self):
                calls["ehlo_count"] = calls.get("ehlo_count", 0) + 1

            def starttls(self, *, context):
                calls["tls_context"] = context

            def login(self, sender, password):
                calls["login"] = (sender, password)

            def sendmail(self, sender, recipients, message):
                calls["sendmail"] = (sender, recipients, message)

        monkeypatch.setattr(report_generator.smtplib, "SMTP", FakeSMTP)
        monkeypatch.setattr(report_generator.ssl, "create_default_context", lambda: tls_context)
        send_report_email("<p>report</p>", DEFAULT_CONFIG)

        assert calls["timeout"] == 30
        assert calls["tls_context"] is tls_context
        assert calls["ehlo_count"] == 2

    def test_live_mode_requires_sheet_id(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
        monkeypatch.setattr(sys, "argv", ["report_generator.py", "--no-email"])
        with pytest.raises(SystemExit) as exc_info:
            report_generator.main()
        assert exc_info.value.code == 2

    def test_demo_default_does_not_overwrite_checked_in_sample(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["report_generator.py", "--demo", "--no-email"],
        )
        report_generator.main()
        assert (tmp_path / "report.html").exists()
        assert not (tmp_path / "sample_report.html").exists()


def test_save_report_creates_parent_directories(tmp_path):
    output = tmp_path / "weekly" / "reports" / "report.html"
    assert save_report("<p>report</p>", output) == output
    assert output.read_text(encoding="utf-8") == "<p>report</p>"


def test_import_does_not_create_a_log_file(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)
    subprocess.run(
        [sys.executable, "-c", "import report_generator"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / "report.log").exists()
