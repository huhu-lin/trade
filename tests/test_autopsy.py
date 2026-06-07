"""Unit tests for the weekly autopsy module.

All tests monkeypatch fetch_price_history to avoid network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from trade.tracking.autopsy import (
    LOSS_THRESHOLD,
    FailureDiagnosis,
    diagnose,
    fetch_benchmark_return,
    run_autopsy,
)


# ----- Helpers -----

def _perf_row(
    ticker: str = "NVDA",
    market: str = "us",
    period: str = "2026-W22",
    entry_price: float = 100.0,
    latest_price: float = 88.0,
    return_pct: float | None = None,
    weeks_tracked: int = 2,
    original_scores: dict | None = None,
) -> dict:
    if return_pct is None:
        return_pct = round((latest_price - entry_price) / entry_price * 100, 2) if entry_price else None
    return {
        "ticker": ticker,
        "market": market,
        "period": period,
        "entry_price": entry_price,
        "latest_price": latest_price,
        "return_pct": return_pct,
        "weeks_tracked": weeks_tracked,
        "original_scores": original_scores or {},
    }


def _make_df_with_prices(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="W", tz="UTC")
    return pd.DataFrame({"Close": prices}, index=idx)


# ----- fetch_benchmark_return -----

def test_fetch_benchmark_return_returns_dict_on_network_failure():
    with patch("trade.data.yfinance_client.fetch_price_history", side_effect=RuntimeError("down")):
        result = fetch_benchmark_return("us", 7)
    assert isinstance(result, dict)
    assert "label" in result
    assert result["return_pct"] is None  # silent failure


def test_fetch_benchmark_return_us_averages_spy_qqq():
    spy_prices = [100.0, 105.0]
    qqq_prices = [200.0, 214.0]

    def fake_fetch(symbol, period="2mo", ttl_hours=4):
        if symbol == "SPY":
            return _make_df_with_prices(spy_prices)
        return _make_df_with_prices(qqq_prices)

    with patch("trade.data.yfinance_client.fetch_price_history", side_effect=fake_fetch):
        result = fetch_benchmark_return("us", 14)

    assert result["label"] == "SPY+QQQ"
    # SPY: +5%, QQQ: +7% → average ~6%
    assert result["return_pct"] == pytest.approx(6.0, abs=0.1)


def test_fetch_benchmark_return_tw_uses_0050():
    def fake_fetch(symbol, period="2mo", ttl_hours=4):
        assert symbol == "0050.TW"
        return _make_df_with_prices([100.0, 103.0])

    with patch("trade.data.yfinance_client.fetch_price_history", side_effect=fake_fetch):
        result = fetch_benchmark_return("tw", 7)

    assert result["label"] == "0050.TW"
    assert result["return_pct"] == pytest.approx(3.0, abs=0.1)


# ----- diagnose -----

def test_diagnose_macro_driven():
    d = diagnose(
        ticker="NVDA", market="us", period="2026-W22",
        entry_price=100.0, current_price=92.0,
        original_scores={"growth": 70.0}, current_scores={"growth": 68.0},
        benchmark_info={"label": "SPY+QQQ", "return_pct": -9.5},
    )
    assert d.macro_driven is True
    assert d.verdict == "macro"
    assert "大盤" in d.one_liner


def test_diagnose_fundamentals_deterioration():
    d = diagnose(
        ticker="AAPL", market="us", period="2026-W22",
        entry_price=100.0, current_price=85.0,
        original_scores={"growth": 75.0, "profitability": 60.0},
        current_scores={"growth": 58.0, "profitability": 59.0},   # growth dropped > 10
        benchmark_info={"label": "SPY+QQQ", "return_pct": -1.0},  # benchmark fine
    )
    assert d.fundamentals_deteriorated is True
    assert d.verdict == "fundamentals_deterioration"
    assert "基本面惡化" in d.one_liner
    assert "growth" in d.one_liner


def test_diagnose_model_failure():
    # Stock down 12%, benchmark only down 1%, no score drop (current_scores == original_scores)
    d = diagnose(
        ticker="TSLA", market="us", period="2026-W22",
        entry_price=100.0, current_price=88.0,
        original_scores={"growth": 70.0},
        current_scores={"growth": 70.0},
        benchmark_info={"label": "SPY+QQQ", "return_pct": -1.0},
    )
    assert d.verdict == "model_failure"
    assert "門檻" in d.one_liner


def test_diagnose_unclear_when_no_original_scores():
    d = diagnose(
        ticker="X", market="us", period="2026-W21",
        entry_price=100.0, current_price=80.0,
        original_scores={}, current_scores={},
        benchmark_info={"label": "SPY+QQQ", "return_pct": -1.0},
    )
    assert d.verdict == "unclear"


def test_diagnose_macro_takes_priority_over_fundamentals():
    # Both macro and fundamentals would trigger — macro wins
    d = diagnose(
        ticker="NVDA", market="us", period="2026-W22",
        entry_price=100.0, current_price=88.0,
        original_scores={"growth": 75.0},
        current_scores={"growth": 55.0},     # dropped > 10
        benchmark_info={"label": "SPY+QQQ", "return_pct": -13.0},  # within ±3% of -12%
    )
    assert d.verdict == "macro"


# ----- run_autopsy -----

def test_run_autopsy_filters_threshold():
    # return_pct = -3%, above threshold of -5%: should be excluded
    rows = [_perf_row(return_pct=-3.0)]
    result = run_autopsy(rows, "us")
    assert result == []


def test_run_autopsy_empty_on_all_positive():
    rows = [_perf_row(return_pct=10.0), _perf_row(return_pct=5.0)]
    result = run_autopsy(rows, "us")
    assert result == []


def test_run_autopsy_processes_losers():
    rows = [_perf_row(return_pct=-10.0, original_scores={"growth": 70.0})]

    with patch("trade.tracking.autopsy.fetch_benchmark_return",
               return_value={"label": "SPY+QQQ", "return_pct": -1.0}):
        result = run_autopsy(rows, "us")

    assert len(result) == 1
    assert result[0].ticker == "NVDA"
    assert result[0].verdict in {"model_failure", "fundamentals_deterioration", "macro", "unclear"}


def test_run_autopsy_silent_on_score_fetcher_failure():
    rows = [_perf_row(return_pct=-10.0)]

    def bad_fetcher(market, ticker):
        raise RuntimeError("API down")

    with patch("trade.tracking.autopsy.fetch_benchmark_return",
               return_value={"label": "SPY+QQQ", "return_pct": None}):
        result = run_autopsy(rows, "us", score_fetcher=bad_fetcher)

    # Should not raise; score_fetcher failure is silent
    assert isinstance(result, list)


def test_run_autopsy_filters_by_market():
    rows = [
        _perf_row(ticker="NVDA", market="us", return_pct=-10.0),
        _perf_row(ticker="2330", market="tw", return_pct=-12.0),
    ]

    with patch("trade.tracking.autopsy.fetch_benchmark_return",
               return_value={"label": "SPY+QQQ", "return_pct": -1.0}):
        result = run_autopsy(rows, "us")

    # Only US losers
    assert all(d.market == "us" for d in result)


def test_run_autopsy_returns_empty_list_on_total_failure():
    # Pass garbage that would cause an internal error; must return []
    result = run_autopsy(None, "us")  # type: ignore[arg-type]
    assert result == []
