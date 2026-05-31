"""Normalise both markets into one CompanyFinancials shape for the metrics engine.

US annual figures come from EDGAR 10-K facts; TW from FinMind statements (year-end
period). Monthly revenue is TW-only. Everything is best-effort: a market that lacks a
concept yields a missing column, never an imputed value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from trade.data import edgar_client, finmind_client, yfinance_client

ANNUAL_CONCEPTS = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "assets",
    "liabilities",
    "equity",
    "operating_cash_flow",
    "capex",
    "inventory",
    "receivables",
]


@dataclass
class CompanyFinancials:
    market: str  # "tw" | "us"
    ticker: str
    name: str = ""
    annual: pd.DataFrame = field(default_factory=pd.DataFrame)  # index=year, concept cols
    monthly_revenue: pd.DataFrame | None = None  # date, revenue (TW only)
    valuation: dict = field(default_factory=dict)

    def has_annual(self) -> bool:
        return not self.annual.empty and "revenue" in self.annual.columns


def _edgar_annual(facts: pd.DataFrame) -> pd.DataFrame:
    """Build an annual wide frame keyed by fiscal-year-end year.

    The XBRL `fy` field is unreliable: a 10-K restates the same period under multiple fy
    tags (comparatives), so we derive the year from the period END date instead. Flow
    concepts (revenue, net income…) carry quarterly/YTD spans alongside the full year, so
    we keep only ~annual durations; stock concepts (assets…) are point-in-time instants.
    """
    from trade.data.edgar_client import FLOW_SET

    if facts.empty:
        return pd.DataFrame()
    ann = facts[facts["form"].isin(["10-K", "20-F"])].copy()  # 20-F covers ADR foreign filers
    if ann.empty:
        return pd.DataFrame()
    end = pd.to_datetime(ann["period_end"], errors="coerce")
    start = pd.to_datetime(ann["period_start"], errors="coerce")
    ann["year"] = end.dt.year
    duration_days = (end - start).dt.days
    is_flow = ann["concept"].isin(FLOW_SET)
    annual_span = duration_days.between(330, 400)
    # Keep flow rows only when their span is ~1 year; keep all stock (instant) rows.
    ann = ann[(~is_flow) | annual_span].dropna(subset=["year"])
    ann["year"] = ann["year"].astype(int)
    ann = ann.sort_values(["period_end", "accn"])
    wide = ann.pivot_table(index="year", columns="concept", values="value", aggfunc="last")
    return wide.sort_index()


# FinMind uses MIXED period conventions, verified against TSMC actuals:
#   - Income statement: SINGLE-QUARTER values  -> sum the 4 quarters for an annual figure.
#   - Cash-flow statement: CUMULATIVE YTD       -> the year-end (Q4) row is already annual.
#   - Balance sheet: point-in-time instants     -> take the year-end value.
_TW_SUM = {"revenue", "gross_profit", "operating_income", "net_income", "eps"}
_TW_LAST = {"operating_cash_flow", "capex",  # cash-flow YTD: last row = full year
            "assets", "liabilities", "equity", "inventory", "receivables"}


def _finmind_annual(stock_id: str) -> pd.DataFrame:
    fs = finmind_client.normalise_statement(
        finmind_client.fetch_financial_statement(stock_id), finmind_client.FS_CONCEPTS
    )
    bs = finmind_client.normalise_statement(
        finmind_client.fetch_balance_sheet(stock_id), finmind_client.BS_CONCEPTS
    )
    cf = finmind_client.normalise_statement(
        finmind_client.fetch_cash_flow(stock_id), finmind_client.CF_CONCEPTS
    )
    frames = [f for f in (fs, bs, cf) if not f.empty]
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="date", how="outer")
    return tw_aggregate_annual(merged)


def tw_aggregate_annual(merged: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a merged quarterly FinMind frame (a `date` column + concept columns) into
    annual rows, applying the per-statement period conventions. Pure / testable."""
    if merged.empty or "date" not in merged.columns:
        return pd.DataFrame()
    merged = merged.copy()
    merged["year"] = pd.to_datetime(merged["date"], errors="coerce").dt.year
    merged = merged.dropna(subset=["year"]).sort_values("date")
    merged["year"] = merged["year"].astype(int)

    quarters_per_year = merged.groupby("year")["date"].nunique()
    sum_cols = [c for c in merged.columns if c in _TW_SUM]
    last_cols = [c for c in merged.columns if c in _TW_LAST]

    summed = merged.groupby("year")[sum_cols].sum(min_count=1) if sum_cols else pd.DataFrame()
    last = merged.groupby("year")[last_cols].last() if last_cols else pd.DataFrame()
    annual = pd.concat([summed, last], axis=1)

    # Only keep complete fiscal years (4 quarters) so summed flows are real annuals.
    complete_years = quarters_per_year[quarters_per_year >= 4].index
    annual = annual[annual.index.isin(complete_years)]
    # Normalise capex to a positive outflow magnitude (FinMind reports it negative).
    if "capex" in annual.columns:
        annual["capex"] = annual["capex"].abs()
    return annual.sort_index()


def load_company(market: str, ticker: str, name: str = "") -> CompanyFinancials:
    market = market.lower()
    if market == "us":
        facts = edgar_client.fetch_company_facts(ticker)
        annual = _edgar_annual(facts)
        valuation = yfinance_client.fetch_valuation(ticker)
        return CompanyFinancials("us", ticker, name, annual, None, valuation)
    if market == "tw":
        annual = _finmind_annual(ticker)
        monthly = finmind_client.fetch_monthly_revenue(ticker)
        mr = None
        if monthly is not None and not monthly.empty and "revenue" in monthly.columns:
            mr = monthly[["date", "revenue"]].sort_values("date").reset_index(drop=True)
        # FinMind has no market cap; yfinance serves TW via .TW (TWSE) / .TWO (TPEx).
        valuation = yfinance_client.fetch_valuation(f"{ticker}.TW")
        if not valuation:
            valuation = yfinance_client.fetch_valuation(f"{ticker}.TWO")
        return CompanyFinancials("tw", ticker, name, annual, mr, valuation)
    raise ValueError(f"Unknown market {market!r} (expected 'tw' or 'us')")
