"""Weekly recommendation tracker.

Records each week's recommended stocks with entry prices, and appends a price snapshot
every subsequent Monday. All I/O is atomic: load → mutate in memory → write back.
Price fetch failures are silent and stored as null — never raises.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trade.settings import TRACKING_DIR

TRACKING_FILE = TRACKING_DIR / "recommendations.json"


# ----- Internal helpers -----

def _load_raw(path: Path = TRACKING_FILE) -> dict:
    if not path.exists():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_raw(data: dict, path: Path = TRACKING_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _yf_symbol(market: str, ticker: str) -> str:
    return f"{ticker}.TW" if market == "tw" else ticker


def _fetch_price_safe(market: str, ticker: str) -> float | None:
    try:
        from trade.data.yfinance_client import fetch_valuation
        val = fetch_valuation(_yf_symbol(market, ticker), ttl_hours=1)
        price = val.get("price")
        return float(price) if price is not None else None
    except Exception:
        return None


def _iso_today() -> str:
    return dt.date.today().isoformat()


# ----- Public API -----

def record_week(
    recommendations: list,
    period: str,
    added_date: str | None = None,
    path: Path = TRACKING_FILE,
) -> None:
    """Add this week's recommendations to the tracker if not already present.

    Idempotent: re-running the same period does not create duplicate entries.
    """
    data = _load_raw(path)
    existing = {(e["period"], e["market"], e["ticker"]) for e in data["entries"]}
    today = added_date or _iso_today()

    for rec in recommendations:
        p = rec.packet
        key = (period, p.market, p.ticker)
        if key in existing:
            continue
        price = _fetch_price_safe(p.market, p.ticker)
        data["entries"].append({
            "period": period,
            "added_date": today,
            "market": p.market,
            "ticker": p.ticker,
            "name": p.name,
            "entry_price": price,
            "prices": [{"period": period, "date": today, "price": price}],
        })
        existing.add(key)

    _save_raw(data, path)


def refresh_prices(current_period: str, path: Path = TRACKING_FILE) -> None:
    """Fetch latest prices for all tracked entries and append a snapshot for current_period.

    Skips entries that already have a snapshot for current_period (idempotent).
    """
    data = _load_raw(path)
    today = _iso_today()

    for entry in data["entries"]:
        if any(s["period"] == current_period for s in entry["prices"]):
            continue
        try:
            price = _fetch_price_safe(entry["market"], entry["ticker"])
        except Exception:
            price = None
        entry["prices"].append({"period": current_period, "date": today, "price": price})

    _save_raw(data, path)


def load_performance(path: Path = TRACKING_FILE) -> list[dict]:
    """Return a flat list of performance dicts ready for template rendering.

    Each dict has: period, market, ticker, name, entry_price, latest_price,
    return_pct (float % or None), weeks_tracked.
    Sorted by period descending, then return_pct descending (None last).
    Returns [] if the file does not exist.
    """
    if not path.exists():
        return []

    data = _load_raw(path)
    rows = []

    for entry in data["entries"]:
        ep = entry.get("entry_price")
        non_null = [s["price"] for s in entry["prices"] if s.get("price") is not None]
        lp = non_null[-1] if non_null else None
        rp: float | None = None
        if ep is not None and lp is not None and ep != 0:
            rp = round((lp - ep) / ep * 100, 2)
        rows.append({
            "period": entry["period"],
            "market": entry["market"],
            "ticker": entry["ticker"],
            "name": entry["name"],
            "entry_price": ep,
            "latest_price": lp,
            "return_pct": rp,
            "weeks_tracked": len(entry["prices"]),
        })

    rows.sort(key=lambda r: (r["period"], 1 if r["return_pct"] is None else 0, -(r["return_pct"] or 0)), reverse=False)
    rows.sort(key=lambda r: r["period"], reverse=True)
    # Within same period: positive returns first, None last
    from itertools import groupby
    sorted_rows: list[dict] = []
    for _, group in groupby(rows, key=lambda r: r["period"]):
        g = list(group)
        g.sort(key=lambda r: (1 if r["return_pct"] is None else 0, -(r["return_pct"] or 0)))
        sorted_rows.extend(g)

    return sorted_rows
