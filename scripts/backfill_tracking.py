"""One-time backfill: parse historical Markdown reports and populate
tracking/recommendations.json with original_scores.

Usage:
    python scripts/backfill_tracking.py [--dry-run]

What it does:
1. Reads every reports/*.md file
2. Parses each recommendation's 基本面佐證 table (metric sub-scores)
3. Computes original_scores (group averages, same formula as scoring.py)
4. Fetches historical entry prices via yfinance (first trading day of that ISO week)
5. Writes to tracking/recommendations.json (creates if missing, skips existing keys)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

REPORTS_DIR = ROOT / "reports"
TRACKING_DIR = ROOT / "tracking"
TRACKING_FILE = TRACKING_DIR / "recommendations.json"

# Metric key → (group, Chinese label)
METRIC_META: dict[str, tuple[str, str]] = {
    "gross_margin":       ("profitability", "毛利率"),
    "operating_margin":   ("profitability", "營業利益率"),
    "roe":                ("profitability", "股東權益報酬率"),
    "debt_ratio":         ("balance_sheet", "負債比率"),
    "ocf_to_net_income":  ("cash_flow",     "現金流"),
    "revenue_cagr_3y":    ("growth",        "近三年營收年複成長"),
    "earnings_growth_yoy":("growth",        "淨利年增率"),
    "fcf_margin":         ("cash_flow",     "自由現金流利潤率"),
    "fcf_yield":          ("valuation",     "自由現金流殖利率"),
}

# Chinese label (partial match) → metric key
LABEL_TO_KEY: list[tuple[str, str]] = [
    ("毛利率",           "gross_margin"),
    ("營業利益率",       "operating_margin"),
    ("股東權益報酬率",   "roe"),
    ("負債比率",         "debt_ratio"),
    ("現金流／淨利",     "ocf_to_net_income"),
    ("近三年營收年複成長","revenue_cagr_3y"),
    ("淨利年增率",       "earnings_growth_yoy"),
    ("自由現金流利潤率", "fcf_margin"),
    ("自由現金流殖利率", "fcf_yield"),
]

GROUPS = ["growth", "profitability", "balance_sheet", "cash_flow", "valuation"]


def _label_to_key(chinese: str) -> str | None:
    for pattern, key in LABEL_TO_KEY:
        if pattern in chinese:
            return key
    return None


def _compute_group_scores(sub_scores: dict[str, float]) -> dict[str, float]:
    """Replicate scoring.py logic: group_score = mean(sub_scores) * 100."""
    buckets: dict[str, list[float]] = {g: [] for g in GROUPS}
    for metric_key, sub in sub_scores.items():
        group = METRIC_META.get(metric_key, (None, None))[0]
        if group:
            buckets[group].append(sub)
    result: dict[str, float] = {}
    for group in GROUPS:
        subs = buckets[group]
        result[group] = round(sum(subs) / len(subs) * 100, 2) if subs else 0.0
    return result


def _parse_report(path: Path) -> list[dict]:
    """Parse one Markdown report. Returns list of candidate dicts."""
    text = path.read_text(encoding="utf-8")
    period = path.stem  # e.g. "2026-W22"

    # Split into per-recommendation sections at ### N. headers
    # Also accept standalone `### N.` with no period prefix
    sections = re.split(r"(?=^### \d+\. )", text, flags=re.MULTILINE)

    candidates = []
    for section in sections:
        # Match header: ### N. NAME（MARKET:TICKER） · 信心 ...
        header_match = re.search(
            r"###\s+\d+\.\s+.+?（(US|TW|us|tw):(\S+?)）",
            section, re.IGNORECASE
        )
        if not header_match:
            continue
        market = header_match.group(1).lower()
        ticker = header_match.group(2)

        # Parse 基本面佐證 table rows: | LABEL | VALUE | SUB_SCORE |
        sub_scores: dict[str, float] = {}
        for row in re.finditer(
            r"\|\s*([^|]+?)\s*\|\s*(-?[\d.]+)\s*\|\s*([\d.]+)\s*\|",
            section
        ):
            label, _value, sub_str = row.group(1), row.group(2), row.group(3)
            key = _label_to_key(label)
            if key:
                try:
                    sub_scores[key] = float(sub_str)
                except ValueError:
                    pass

        if not sub_scores:
            # Likely header row or watchlist — skip
            continue

        original_scores = _compute_group_scores(sub_scores)
        candidates.append({
            "period": period,
            "market": market,
            "ticker": ticker,
            "original_scores": original_scores,
        })

    return candidates


def _iso_week_monday(period: str) -> dt.date:
    """Convert '2026-W22' to the Monday of that ISO week."""
    year, week = int(period[:4]), int(period[6:])
    return dt.date.fromisocalendar(year, week, 1)  # Monday = weekday 1


def _fetch_entry_price(market: str, ticker: str, week_monday: dt.date) -> float | None:
    """Fetch the closing price on or just after week_monday.

    fetch_price_history returns a RangeIndex DataFrame with 'date' (str) and 'close' columns.
    """
    try:
        from trade.data.yfinance_client import fetch_price_history
        yf_sym = f"{ticker}.TW" if market == "tw" else ticker
        df = fetch_price_history(yf_sym, period="6mo", ttl_hours=1)
        if df.empty:
            return None
        # date col is string like "2026-05-26" or "2026-05-26 00:00:00+00:00"
        target_str = week_monday.isoformat()  # "2026-05-26"
        close_col = "close" if "close" in df.columns else "Close"
        # Filter rows on or after the target date (string comparison works for ISO dates)
        candidates = df[df["date"].astype(str).str[:10] >= target_str]
        if candidates.empty:
            candidates = df  # fallback: most recent available
        return round(float(candidates[close_col].iloc[0]), 4)
    except Exception as e:
        print(f"  [warn] price fetch failed for {market}:{ticker}: {e}")
        return None


def _load_tracking() -> dict:
    if TRACKING_FILE.exists():
        return json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
    return {"entries": []}


def _save_tracking(data: dict) -> None:
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    TRACKING_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill tracking/recommendations.json from reports/")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't write")
    args = parser.parse_args()

    report_files = sorted(REPORTS_DIR.glob("*.md"))
    if not report_files:
        print("[!] No report files found in reports/")
        return 1

    # Collect all candidates across all reports
    all_candidates: list[dict] = []
    for path in report_files:
        print(f"Parsing {path.name}...")
        candidates = _parse_report(path)
        print(f"  Found {len(candidates)} recommendations: {[c['ticker'] for c in candidates]}")
        all_candidates.extend(candidates)

    if not all_candidates:
        print("[!] No candidates parsed.")
        return 1

    data = _load_tracking()
    existing_keys = {(e["period"], e["market"], e["ticker"]) for e in data["entries"]}

    added = 0
    skipped = 0
    for c in all_candidates:
        key = (c["period"], c["market"], c["ticker"])
        if key in existing_keys:
            # Update original_scores if missing
            for entry in data["entries"]:
                if (entry["period"], entry["market"], entry["ticker"]) == key:
                    if not entry.get("original_scores"):
                        if not args.dry_run:
                            entry["original_scores"] = c["original_scores"]
                        print(f"  [update] {key}: original_scores backfilled")
                        added += 1
                    else:
                        skipped += 1
            continue

        # New entry — fetch price
        week_monday = _iso_week_monday(c["period"])
        price = _fetch_entry_price(c["market"], c["ticker"], week_monday)
        today = week_monday.isoformat()
        print(f"  [add] {c['market']}:{c['ticker']} ({c['period']}) price={price} "
              f"scores={list(c['original_scores'].keys())}")

        if not args.dry_run:
            data["entries"].append({
                "period": c["period"],
                "added_date": today,
                "market": c["market"],
                "ticker": c["ticker"],
                "name": c["ticker"],
                "entry_price": price,
                "prices": [{"period": c["period"], "date": today, "price": price}],
                "original_scores": c["original_scores"],
            })
            existing_keys.add(key)
        added += 1

    if not args.dry_run:
        _save_tracking(data)
        print(f"\nDone: {added} entries added/updated, {skipped} already complete.")
        print(f"Written -> {TRACKING_FILE}")
    else:
        print(f"\n[dry-run] Would add/update {added} entries, skip {skipped}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
