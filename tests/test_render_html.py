"""HTML report rendering: same PipelineResult as the Markdown path, emitted as a
self-contained interactive page. Network-free — reuses the synthetic providers."""

import pandas as pd

from trade.data.loader import CompanyFinancials
from trade.pipeline import run_pipeline
from trade.report.render import (
    render_html_report,
    write_html_report,
    write_index,
)


def _strong(market: str, ticker: str, name: str = "") -> CompanyFinancials:
    annual = pd.DataFrame(
        {
            "revenue": [100.0, 200.0],
            "gross_profit": [70.0, 150.0],
            "operating_income": [60.0, 120.0],
            "net_income": [40.0, 100.0],
            "equity": [110.0, 130.0],
            "liabilities": [40.0, 50.0],
            "assets": [150.0, 180.0],
            "operating_cash_flow": [60.0, 100.0],
            "capex": [8.0, 10.0],
            "receivables": [30.0, 45.0],
        },
        index=[2025, 2026],
    )
    mr = None
    if market == "tw":
        dates = pd.date_range("2025-04-30", periods=13, freq="ME")
        mr = pd.DataFrame({"date": dates, "revenue": list(range(100, 112)) + [140]})
    return CompanyFinancials(market, ticker, name, annual, mr, {"market_cap": 1000.0})


def _empty(market: str, ticker: str, name: str = "") -> CompanyFinancials:
    return CompanyFinancials(market, ticker, name, pd.DataFrame(), None, {})


def _result_with_nvda():
    def provider(market, ticker, name=""):
        return _strong(market, ticker, name) if (market, ticker) == ("us", "NVDA") else _empty(market, ticker, name)

    return run_pipeline(catalyst="ai_capex", run_analyst=False, financials_provider=provider)


def test_html_report_has_structure_and_data():
    html = render_html_report(_result_with_nvda())
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "中長期投資研究報告" in html
    assert "NVDA" in html
    assert "需求傳導路徑" in html
    # Interactive widgets present
    assert 'class="donut"' in html          # confidence gauge
    assert 'id="watch"' in html             # sortable/filterable watchlist
    assert "watch-filter" in html
    # Verified vs assumption badge styling is reachable
    assert "已查證" in html or "敘事假設" in html
    # Self-contained: no external resource fetches
    assert "http://" not in html and "https://" not in html
    assert "<script" in html and "src=" not in html.split("</body>")[0]


def test_html_autoescapes_untrusted_fields():
    # A company name carrying markup must be escaped, never injected as live HTML.
    def provider(market, ticker, name=""):
        nm = "<script>alert(1)</script>" if (market, ticker) == ("us", "NVDA") else name
        return _strong(market, ticker, nm) if (market, ticker) == ("us", "NVDA") else _empty(market, ticker, name)

    html = render_html_report(run_pipeline(catalyst="ai_capex", run_analyst=False, financials_provider=provider))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_write_html_and_index(tmp_path):
    result = _result_with_nvda()
    out = write_html_report(result, tmp_path)
    assert out.exists() and out.name == f"{result.period}.html"

    idx = write_index(tmp_path)
    assert idx.name == "index.html"
    body = idx.read_text(encoding="utf-8")
    assert result.period in body
    assert f'href="{result.period}.html"' in body
    # The index must not list itself as a report entry.
    assert 'href="index.html"' not in body
