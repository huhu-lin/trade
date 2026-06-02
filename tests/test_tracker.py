"""Unit tests for the weekly recommendation tracker.

All tests use tmp_path so they never touch the real tracking/ directory.
Price fetching is monkeypatched to avoid network calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from trade.analysis.data_packet import CandidatePacket
from trade.pipeline import Recommendation
from trade.analysis.claude_analyst import AnalystVerdict


# ----- Helpers -----

def _make_packet(market: str, ticker: str, name: str = "") -> CandidatePacket:
    return CandidatePacket(
        market=market,
        ticker=ticker,
        name=name or f"{market}:{ticker}",
        catalyst_id="test",
        catalyst_name="Test",
        fundamentals_score=60.0,
        fundamentals_passes=True,
        propagation_score=55.0,
        chain_hops=1,
        confidence=57.5,
        confidence_tier="medium",
    )


def _make_rec(market: str, ticker: str, name: str = "") -> Recommendation:
    return Recommendation(
        packet=_make_packet(market, ticker, name),
        verdict=AnalystVerdict("", "", available=False, note="test"),
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ----- Tests -----

def test_record_week_creates_file_on_first_run(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"

    tracker.record_week([_make_rec("us", "NVDA"), _make_rec("tw", "2330")], "2026-W22", path=tf)

    data = _load(tf)
    assert len(data["entries"]) == 2
    tickers = {e["ticker"] for e in data["entries"]}
    assert tickers == {"NVDA", "2330"}
    assert all(e["period"] == "2026-W22" for e in data["entries"])
    assert all(e["entry_price"] == 100.0 for e in data["entries"])


def test_record_week_is_idempotent(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"

    recs = [_make_rec("us", "NVDA")]
    tracker.record_week(recs, "2026-W22", path=tf)
    tracker.record_week(recs, "2026-W22", path=tf)

    data = _load(tf)
    assert len(data["entries"]) == 1


def test_record_week_appends_new_period(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"

    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W23", path=tf)

    data = _load(tf)
    assert len(data["entries"]) == 2
    periods = {e["period"] for e in data["entries"]}
    assert periods == {"2026-W22", "2026-W23"}


def test_refresh_prices_appends_snapshot(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 115.0)
    tracker.refresh_prices("2026-W23", path=tf)

    data = _load(tf)
    snapshots = data["entries"][0]["prices"]
    assert len(snapshots) == 2
    assert snapshots[1]["period"] == "2026-W23"
    assert snapshots[1]["price"] == 115.0


def test_refresh_prices_is_idempotent(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 115.0)
    tracker.refresh_prices("2026-W23", path=tf)
    tracker.refresh_prices("2026-W23", path=tf)

    data = _load(tf)
    w23_snapshots = [s for s in data["entries"][0]["prices"] if s["period"] == "2026-W23"]
    assert len(w23_snapshots) == 1


def test_refresh_prices_stores_null_on_fetch_failure(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: None)
    tracker.refresh_prices("2026-W23", path=tf)

    data = _load(tf)
    assert data["entries"][0]["prices"][1]["price"] is None


def test_refresh_prices_does_not_raise_on_fetch_error(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    def bad_fetch(m, t):
        raise RuntimeError("network down")

    monkeypatch.setattr(tracker, "_fetch_price_safe", bad_fetch)
    # _fetch_price_safe itself is already safe, but let's ensure refresh_prices also handles it
    tracker.refresh_prices("2026-W23", path=tf)  # must not raise


def test_load_performance_empty_when_no_file(tmp_path):
    from trade.tracking import tracker
    rows = tracker.load_performance(path=tmp_path / "nonexistent.json")
    assert rows == []


def test_load_performance_computes_return_pct(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 115.0)
    tracker.refresh_prices("2026-W23", path=tf)

    rows = tracker.load_performance(path=tf)
    assert len(rows) == 1
    assert rows[0]["return_pct"] == pytest.approx(15.0, rel=1e-3)
    assert rows[0]["weeks_tracked"] == 2


def test_load_performance_handles_null_entry_price(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: None)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    rows = tracker.load_performance(path=tf)
    assert rows[0]["return_pct"] is None
    assert rows[0]["entry_price"] is None


def test_load_performance_handles_null_latest_price(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W22", path=tf)

    # Force latest price to null by directly patching the JSON
    data = _load(tf)
    data["entries"][0]["prices"][0]["price"] = None
    tf.write_text(json.dumps(data), encoding="utf-8")

    rows = tracker.load_performance(path=tf)
    assert rows[0]["return_pct"] is None


def test_load_performance_sort_order(tmp_path, monkeypatch):
    from trade.tracking import tracker
    monkeypatch.setattr(tracker, "_fetch_price_safe", lambda m, t: 100.0)
    tf = tmp_path / "recommendations.json"
    tracker.record_week([_make_rec("us", "AAPL"), _make_rec("us", "MSFT")], "2026-W22", path=tf)
    tracker.record_week([_make_rec("us", "NVDA")], "2026-W23", path=tf)

    # W22: AAPL +10%, MSFT -5%; W23: NVDA +2%
    data = _load(tf)
    for e in data["entries"]:
        if e["ticker"] == "AAPL":
            e["entry_price"] = 100.0
            e["prices"] = [{"period": "2026-W22", "date": "2026-05-26", "price": 100.0},
                           {"period": "2026-W23", "date": "2026-06-02", "price": 110.0}]
        elif e["ticker"] == "MSFT":
            e["entry_price"] = 100.0
            e["prices"] = [{"period": "2026-W22", "date": "2026-05-26", "price": 100.0},
                           {"period": "2026-W23", "date": "2026-06-02", "price": 95.0}]
        elif e["ticker"] == "NVDA":
            e["entry_price"] = 100.0
            e["prices"] = [{"period": "2026-W23", "date": "2026-06-02", "price": 102.0}]
    tf.write_text(json.dumps(data), encoding="utf-8")

    rows = tracker.load_performance(path=tf)

    # Most recent period first
    assert rows[0]["period"] == "2026-W23"
    # Within W22: positive (+10%) before negative (-5%)
    w22 = [r for r in rows if r["period"] == "2026-W22"]
    assert w22[0]["ticker"] == "AAPL"
    assert w22[1]["ticker"] == "MSFT"
