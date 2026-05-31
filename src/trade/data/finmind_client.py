"""FinMind client — Taiwan fundamentals: financial statements + monthly revenue.

Monthly revenue (月營收) is Taiwan's killer leading signal: it is published ~10 days
after month-end, far ahead of quarterly financials, so it is the first place a demand
surge shows up in the chain. We treat it as a first-class dataset.

Auth: a free FINMIND_TOKEN raises the limit to ~1500 req/hr; without it FinMind still
serves ~600 req/hr unauthenticated, so the token is optional but recommended.
FinMind returns long-format frames (date, stock_id, type, value); we cache the raw
frames and normalise on demand, never inventing a missing accounting line.
"""

from __future__ import annotations

import pandas as pd

from trade.data.cache import RateLimiter, cache_get, cache_put
from trade.settings import FINMIND_TOKEN

_SOURCE = "finmind"
_LIMITER = RateLimiter(2.5)  # ~1440/hr, under the 1500 authenticated cap

# FinMind financial-statement `type` keys -> our concept names. Keys absent in a given
# filing are simply skipped (no fabrication).
FS_CONCEPTS = {
    "Revenue": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncome": "operating_income",
    "IncomeAfterTaxes": "net_income",
    "EPS": "eps",
}
BS_CONCEPTS = {
    "TotalAssets": "assets",
    "Liabilities": "liabilities",
    "Equity": "equity",
    "Inventories": "inventory",
    "AccountsReceivableNet": "receivables",
}
CF_CONCEPTS = {
    "CashFlowsFromOperatingActivities": "operating_cash_flow",
    "PropertyAndPlantAndEquipment": "capex",
}


def _loader():
    from FinMind.data import DataLoader

    dl = DataLoader()
    if FINMIND_TOKEN:
        try:
            dl.login_by_token(api_token=FINMIND_TOKEN)
        except Exception:
            pass  # fall back to unauthenticated quota
    return dl


def _fetch(dataset: str, stock_id: str, start_date: str, ttl_hours: float) -> pd.DataFrame:
    params = {"dataset": dataset, "stock_id": stock_id, "start_date": start_date}
    cached = cache_get(_SOURCE, dataset, params, ttl_hours=ttl_hours)
    if cached is not None:
        return cached
    _LIMITER.wait()
    dl = _loader()
    method = getattr(dl, dataset)
    try:
        df = method(stock_id=stock_id, start_date=start_date)
    except Exception:
        df = pd.DataFrame()
    if df is None:
        df = pd.DataFrame()
    cache_put(_SOURCE, dataset, params, df)
    return df


def fetch_monthly_revenue(stock_id: str, start_date: str = "2019-01-01") -> pd.DataFrame:
    """Long frame with columns: date, stock_id, revenue, revenue_month, revenue_year."""
    return _fetch("taiwan_stock_month_revenue", stock_id, start_date, ttl_hours=24)


def fetch_financial_statement(stock_id: str, start_date: str = "2019-01-01") -> pd.DataFrame:
    return _fetch("taiwan_stock_financial_statement", stock_id, start_date, ttl_hours=24)


def fetch_balance_sheet(stock_id: str, start_date: str = "2019-01-01") -> pd.DataFrame:
    return _fetch("taiwan_stock_balance_sheet", stock_id, start_date, ttl_hours=24)


def fetch_cash_flow(stock_id: str, start_date: str = "2019-01-01") -> pd.DataFrame:
    return _fetch("taiwan_stock_cash_flows_statement", stock_id, start_date, ttl_hours=24)


def normalise_statement(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Pivot a FinMind long frame to columns: date, <concept...>. Missing concepts omitted."""
    if df is None or df.empty or "type" not in df.columns:
        return pd.DataFrame()
    sub = df[df["type"].isin(mapping)].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["concept"] = sub["type"].map(mapping)
    wide = sub.pivot_table(index="date", columns="concept", values="value", aggfunc="last")
    return wide.reset_index()
