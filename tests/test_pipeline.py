"""M5: the dual-engine intersection. A node is recommended ONLY if it passes fundamentals
AND sits on a verified demand chain with real evidence; otherwise it lands on a transparent
watchlist with a stated reason. Driven by injected synthetic financials so it's network-free."""

import pandas as pd

from trade.data.loader import CompanyFinancials
from trade.pipeline import run_pipeline
from trade.report.render import render_report


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


def test_intersection_recommends_strong_seed_and_watchlists_weak():
    # NVDA (a seed of ai_capex) is strong -> recommendation; everything else has no data
    # -> fails fundamentals -> watchlist. No network, no Claude.
    def provider(market, ticker, name=""):
        return _strong(market, ticker, name) if (market, ticker) == ("us", "NVDA") else _empty(market, ticker, name)

    result = run_pipeline(catalyst="ai_capex", run_analyst=False, financials_provider=provider)

    rec_ids = {(r.packet.market, r.packet.ticker) for r in result.recommendations}
    assert ("us", "NVDA") in rec_ids
    nvda = next(r.packet for r in result.recommendations if r.packet.ticker == "NVDA")
    assert nvda.fundamentals_passes
    assert nvda.propagation_score > 0
    assert nvda.narrative_assumption is False
    assert nvda.confidence is not None and nvda.confidence > 0

    # Weak nodes are surfaced transparently, not dropped silently.
    assert result.watchlist
    assert all("基本面" in p.reason for p in result.watchlist)


def test_unverified_node_never_recommended_even_if_fundamentals_strong():
    # tw:3661 is reachable ONLY via an unverified edge in supply_chain.yml. Give it strong
    # financials; it must STILL be excluded from recommendations (narrative assumption).
    def provider(market, ticker, name=""):
        return _strong(market, ticker, name)

    result = run_pipeline(catalyst="ai_capex", run_analyst=False, financials_provider=provider)
    rec_ids = {(r.packet.market, r.packet.ticker) for r in result.recommendations}
    assert ("tw", "3661") not in rec_ids
    watch_3661 = [p for p in result.watchlist if p.ticker == "3661"]
    assert watch_3661 and "敘事假設" in watch_3661[0].reason


def test_report_renders_markdown_with_recommendation():
    def provider(market, ticker, name=""):
        return _strong(market, ticker, name) if (market, ticker) == ("us", "NVDA") else _empty(market, ticker, name)

    result = run_pipeline(catalyst="ai_capex", run_analyst=False, financials_provider=provider)
    md = render_report(result)
    assert "中長期價值投資研究報告" in md
    assert "NVDA" in md
    assert "需求傳導路徑" in md
