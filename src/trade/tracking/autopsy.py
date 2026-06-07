"""Weekly autopsy: diagnose why recommended picks lost money.

All operations are silent on failure — never abort the pipeline.
Reads from recommendations.json; does not depend on live pipeline data.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Literal

LOSS_THRESHOLD = -5.0  # only diagnose picks below this return %
MACRO_TOLERANCE = 3.0  # stock vs benchmark within ±3% → macro-driven
SCORE_DROP_THRESHOLD = 10.0  # pts drop in any group → fundamentals_deteriorated

VerdictType = Literal[
    "macro",
    "fundamentals_deterioration",
    "model_failure",
    "unclear",
]


@dataclass
class FailureDiagnosis:
    ticker: str
    market: str
    period: str
    return_pct: float
    benchmark_label: str          # e.g. "SPY+QQQ" or "0050.TW"
    benchmark_return: float | None
    macro_driven: bool
    fundamentals_deteriorated: bool
    verdict: VerdictType
    one_liner: str                # human-readable Chinese summary


def fetch_benchmark_return(market: str, days: int) -> dict:
    """Pull benchmark return over the last `days` calendar days.

    US:  average of SPY and QQQ
    TW:  0050.TW
    Returns: {"label": str, "return_pct": float | None}
    Silent on any failure.
    """
    try:
        from trade.data.yfinance_client import fetch_price_history

        if market == "tw":
            symbols = ["0050.TW"]
            label = "0050.TW"
        else:
            symbols = ["SPY", "QQQ"]
            label = "SPY+QQQ"

        returns: list[float] = []
        for sym in symbols:
            try:
                df: pd.DataFrame = fetch_price_history(sym, period="3mo", ttl_hours=4)
                if df.empty:
                    continue
                close_col = "Close" if "Close" in df.columns else df.columns[-1]
                series = df[close_col].dropna()
                if len(series) < 2:
                    continue
                entry_price = float(series.iloc[0])
                latest_price = float(series.iloc[-1])
                returns.append((latest_price - entry_price) / entry_price * 100)
            except Exception:
                continue

        if not returns:
            return {"label": label, "return_pct": None}
        return {"label": label, "return_pct": round(sum(returns) / len(returns), 2)}
    except Exception:
        label = "0050.TW" if market == "tw" else "SPY+QQQ"
        return {"label": label, "return_pct": None}


def diagnose(
    ticker: str,
    market: str,
    period: str,
    entry_price: float,
    current_price: float,
    original_scores: dict[str, float],
    current_scores: dict[str, float],
    benchmark_info: dict,
) -> FailureDiagnosis:
    """Single-stock diagnosis. Returns a FailureDiagnosis regardless of data gaps."""
    ep, cp = entry_price, current_price
    stock_return = (cp - ep) / ep * 100 if ep and ep != 0 else 0.0

    bench_ret: float | None = benchmark_info.get("return_pct")
    bench_label: str = benchmark_info.get("label", "benchmark")

    macro_driven = (
        bench_ret is not None
        and abs(stock_return - bench_ret) <= MACRO_TOLERANCE
    )

    deteriorated_groups: list[str] = []
    if original_scores and current_scores:
        for group, orig_val in original_scores.items():
            curr_val = current_scores.get(group)
            if curr_val is not None and (orig_val - curr_val) > SCORE_DROP_THRESHOLD:
                deteriorated_groups.append(group)
    fundamentals_deteriorated = bool(deteriorated_groups)

    # Verdict priority: macro → fundamentals_deterioration → model_failure → unclear
    if macro_driven:
        verdict: VerdictType = "macro"
    elif fundamentals_deteriorated:
        verdict = "fundamentals_deterioration"
    elif original_scores:
        # Had scores at recommendation time but still lost — model failure
        verdict = "model_failure"
    else:
        verdict = "unclear"

    # Build one-liner
    if verdict == "macro":
        bench_str = f"{bench_ret:+.1f}%" if bench_ret is not None else "n/a"
        one_liner = f"大盤同步下跌（{bench_label} {bench_str}），非個股問題"
    elif verdict == "fundamentals_deterioration":
        groups_str = "、".join(deteriorated_groups)
        one_liner = f"基本面惡化：{groups_str} 分數下滑逾 {SCORE_DROP_THRESHOLD:.0f} 分"
    elif verdict == "model_failure":
        one_liner = "基本面與傳導分數當時通過篩選，但股價仍下跌；需檢視篩選門檻"
    else:
        one_liner = "原始分數缺失，無法確定原因（推薦週期前的舊紀錄）"

    return FailureDiagnosis(
        ticker=ticker,
        market=market,
        period=period,
        return_pct=round(stock_return, 2),
        benchmark_label=bench_label,
        benchmark_return=bench_ret,
        macro_driven=macro_driven,
        fundamentals_deteriorated=fundamentals_deteriorated,
        verdict=verdict,
        one_liner=one_liner,
    )


def run_autopsy(
    performance_rows: list[dict],
    market: str,
    score_fetcher: Callable[[str, str], dict] | None = None,
) -> list[FailureDiagnosis]:
    """Batch-diagnose all picks with return_pct < LOSS_THRESHOLD.

    performance_rows: output of load_performance() (includes original_scores key).
    score_fetcher: optional callable(market, ticker) -> fundamentals_by_group dict.
                   If None, current_scores will be empty.
    Always returns a list (empty on total failure or no losers).
    """
    try:
        losers = [
            r for r in performance_rows
            if r.get("return_pct") is not None
            and r["return_pct"] < LOSS_THRESHOLD
            and r.get("market", market) == market
        ]
        if not losers:
            return []

        # Estimate days since recommendation using weeks_tracked
        avg_days = max(7, (losers[0].get("weeks_tracked", 1) - 1) * 7)
        benchmark_info = fetch_benchmark_return(market, avg_days)

        results: list[FailureDiagnosis] = []
        for row in losers:
            try:
                ep = row.get("entry_price")
                lp = row.get("latest_price")
                if ep is None or lp is None:
                    continue

                current_scores: dict = {}
                if score_fetcher is not None:
                    try:
                        current_scores = score_fetcher(row["market"], row["ticker"])
                    except Exception:
                        pass

                diag = diagnose(
                    ticker=row["ticker"],
                    market=row["market"],
                    period=row["period"],
                    entry_price=ep,
                    current_price=lp,
                    original_scores=row.get("original_scores", {}),
                    current_scores=current_scores,
                    benchmark_info=benchmark_info,
                )
                results.append(diag)
            except Exception:
                continue

        return results
    except Exception:
        return []
