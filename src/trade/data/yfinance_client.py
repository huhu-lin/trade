"""yfinance client — US prices, market cap, and valuation multiples.

yfinance scrapes Yahoo and can break or rate-limit without notice, so every accessor
degrades gracefully: on failure it returns an empty frame / None and records nothing,
never a guessed number. EDGAR remains the authoritative source for fundamentals; this
client supplies price-derived context (price, market cap, trailing P/E, P/B).
"""

from __future__ import annotations

import pandas as pd

from trade.data.cache import RateLimiter, cache_get, cache_put

_SOURCE = "yfinance"
_LIMITER = RateLimiter(1.0)


def _ticker(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol)


def fetch_price_history(symbol: str, period: str = "2y", ttl_hours: float = 12) -> pd.DataFrame:
    params = {"symbol": symbol, "period": period}
    cached = cache_get(_SOURCE, "prices", params, ttl_hours=ttl_hours)
    if cached is not None:
        return cached
    _LIMITER.wait()
    try:
        hist = _ticker(symbol).history(period=period, auto_adjust=True)
    except Exception:
        return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()[["Date", "Close", "Volume"]].rename(
        columns={"Date": "date", "Close": "close", "Volume": "volume"}
    )
    hist["date"] = hist["date"].astype(str)
    cache_put(_SOURCE, "prices", params, hist)
    return hist


def fetch_valuation(symbol: str, ttl_hours: float = 12) -> dict:
    """Return price-derived valuation fields. Missing fields are simply absent."""
    params = {"symbol": symbol}
    cached = cache_get(_SOURCE, "valuation", params, ttl_hours=ttl_hours)
    if cached is not None and not cached.empty:
        return cached.iloc[0].dropna().to_dict()
    _LIMITER.wait()
    try:
        info = _ticker(symbol).info
    except Exception:
        info = {}
    keep = {
        "price": info.get("currentPrice"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "price_to_book": info.get("priceToBook"),
        "enterprise_to_ebitda": info.get("enterpriseToEbitda"),
    }
    cache_put(_SOURCE, "valuation", params, pd.DataFrame([keep]))
    return {k: v for k, v in keep.items() if v is not None}
