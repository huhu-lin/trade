"""Guard the fiscal-period normalisation that was the source of the trickiest M1 bugs."""

import pandas as pd

from trade.data.loader import _edgar_annual, tw_aggregate_annual


def test_edgar_annual_derives_year_from_end_and_filters_quarterly_spans():
    facts = pd.DataFrame(
        [
            # full-year revenue (annual span) — should be kept, year = end.year
            {"concept": "revenue", "period_start": "2024-01-29", "period_end": "2025-01-26",
             "fy": 2025, "fp": "FY", "form": "10-K", "value": 130497.0, "accn": "a1"},
            # SAME period restated as a comparative in a later 10-K (latest accn wins, same value)
            {"concept": "revenue", "period_start": "2024-01-29", "period_end": "2025-01-26",
             "fy": 2026, "fp": "FY", "form": "10-K", "value": 130497.0, "accn": "a2"},
            # quarterly span (~90 days) — must be EXCLUDED from annual
            {"concept": "revenue", "period_start": "2024-10-28", "period_end": "2025-01-26",
             "fy": 2025, "fp": "Q4", "form": "10-K", "value": 39331.0, "accn": "a1"},
            # balance-sheet instant (no start) — kept by end.year
            {"concept": "assets", "period_start": None, "period_end": "2025-01-26",
             "fy": 2025, "fp": "FY", "form": "10-K", "value": 111601.0, "accn": "a1"},
            # a 10-Q row must be ignored entirely
            {"concept": "revenue", "period_start": "2024-01-29", "period_end": "2025-01-26",
             "fy": 2025, "fp": "FY", "form": "10-Q", "value": 999.0, "accn": "q1"},
        ]
    )
    annual = _edgar_annual(facts)
    assert list(annual.index) == [2025]
    assert annual.loc[2025, "revenue"] == 130497.0  # annual span, not the 39331 quarter
    assert annual.loc[2025, "assets"] == 111601.0


def test_tw_aggregate_sums_income_but_takes_last_for_cashflow():
    # One full fiscal year of quarterly income (single-quarter) + cumulative YTD cash flow.
    merged = pd.DataFrame(
        {
            "date": ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
            "revenue": [100, 110, 120, 130],            # income: single-quarter -> sum = 460
            "operating_cash_flow": [50, 110, 180, 260],  # cash flow: YTD -> last = 260
            "capex": [-20, -45, -70, -100],              # cash flow YTD, negative -> abs(last)=100
            "equity": [900, 950, 1000, 1050],            # balance sheet instant -> last = 1050
        }
    )
    annual = tw_aggregate_annual(merged)
    assert annual.loc[2023, "revenue"] == 460
    assert annual.loc[2023, "operating_cash_flow"] == 260
    assert annual.loc[2023, "capex"] == 100
    assert annual.loc[2023, "equity"] == 1050


def test_tw_aggregate_drops_incomplete_years():
    merged = pd.DataFrame(
        {
            "date": ["2024-03-31", "2024-06-30"],  # only 2 quarters -> incomplete
            "revenue": [100, 110],
            "equity": [900, 950],
        }
    )
    assert tw_aggregate_annual(merged).empty
