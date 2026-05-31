"""SEC EDGAR client — authoritative US fundamentals via XBRL companyfacts.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
Rules respected here:
  - 10 req/s cap  -> RateLimiter(0.15s) keeps us well under.
  - Descriptive User-Agent header is mandatory (name + email); we refuse to call
    without SEC_USER_AGENT set rather than send a fake one.
  - companyfacts returns every value ever filed, including restatements, so we keep the
    latest-filed value per (concept, period) by accession order.
"""

from __future__ import annotations

import requests

import pandas as pd

from trade.data.cache import RateLimiter, cache_get, cache_put
from trade.settings import SEC_USER_AGENT

_SOURCE = "edgar"
_LIMITER = RateLimiter(0.15)
_BASE = "https://data.sec.gov"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# us-gaap concepts we pull. A concept may be reported under different tags across filers
# or eras (e.g. NVDA moved revenue from RevenueFromContract... to Revenues), so we read
# ALL listed tags and merge — never stopping at the first populated one.
# FLOW concepts are durations (need ~annual span filtering); STOCK concepts are instants.
FLOW_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
}
STOCK_CONCEPTS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
}
CONCEPTS = {**FLOW_CONCEPTS, **STOCK_CONCEPTS}
FLOW_SET = set(FLOW_CONCEPTS)


class EdgarError(RuntimeError):
    pass


def _headers() -> dict:
    if not SEC_USER_AGENT:
        raise EdgarError(
            "SEC_USER_AGENT is not set. SEC requires a descriptive 'Name email' User-Agent."
        )
    return {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def resolve_cik(ticker: str) -> str | None:
    """Map a ticker to a zero-padded 10-digit CIK, or None if unknown."""
    df = cache_get(_SOURCE, "tickers", {}, ttl_hours=24 * 30)
    if df is None:
        _LIMITER.wait()
        resp = requests.get(_TICKERS_URL, headers=_headers(), timeout=30)
        resp.raise_for_status()
        rows = list(resp.json().values())
        df = pd.DataFrame(rows)
        cache_put(_SOURCE, "tickers", {}, df)
    match = df[df["ticker"].str.upper() == ticker.upper()]
    if match.empty:
        return None
    return str(int(match.iloc[0]["cik_str"])).zfill(10)


def fetch_company_facts(ticker: str, ttl_hours: float = 24) -> pd.DataFrame:
    """Return a tidy long DataFrame of US-GAAP facts for `ticker`.

    Columns: concept, period_end, fy, fp, form, value, unit, accn, frame.
    Empty DataFrame (not an error) if the company has no XBRL facts.
    """
    cik = resolve_cik(ticker)
    if cik is None:
        raise EdgarError(f"No CIK found for ticker {ticker!r}")

    params = {"cik": cik}
    cached = cache_get(_SOURCE, "companyfacts", params, ttl_hours=ttl_hours)
    if cached is not None:
        return cached

    _LIMITER.wait()
    url = f"{_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    resp = requests.get(url, headers=_headers(), timeout=60)
    resp.raise_for_status()
    facts = resp.json().get("facts", {}).get("us-gaap", {})

    records: list[dict] = []
    for concept, tags in CONCEPTS.items():
        for tag in tags:  # merge all tags; eras/filers differ in which they use
            node = facts.get(tag)
            if not node:
                continue
            for unit, entries in node.get("units", {}).items():
                for e in entries:
                    if e.get("val") is None or not e.get("end"):
                        continue
                    records.append(
                        {
                            "concept": concept,
                            "period_start": e.get("start"),
                            "period_end": e["end"],
                            "fy": e.get("fy"),
                            "fp": e.get("fp"),
                            "form": e.get("form"),
                            "value": float(e["val"]),
                            "unit": unit,
                            "accn": e.get("accn", ""),
                        }
                    )

    df = pd.DataFrame.from_records(
        records,
        columns=["concept", "period_start", "period_end", "fy", "fp", "form", "value", "unit", "accn"],
    )
    if not df.empty:
        # Keep the latest-filed value per (concept, period, FORM). Form must stay in the key:
        # the same period appears in both the 10-K and later 10-Qs (as a comparative); if we
        # collapsed across forms the 10-Q copy would shadow the 10-K and later get filtered
        # out. Restatements within a form collapse to the latest accession.
        df = (
            df.sort_values("accn")
            .drop_duplicates(["concept", "period_start", "period_end", "form"], keep="last")
            .reset_index(drop=True)
        )
    cache_put(_SOURCE, "companyfacts", params, df)
    return df
