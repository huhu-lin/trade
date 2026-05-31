"""End-to-end orchestration: catalyst -> demand chain -> dual-engine scoring -> packets
-> deterministic confidence -> constrained Claude analysis -> ranked recommendations.

A recommendation is the DUAL-ENGINE INTERSECTION: it must (a) pass the quantitative
fundamentals floor AND (b) sit on a *verified* demand chain with a non-zero propagation
score (real demand evidence, no unverified link). Everything else that's on the chain but
fails one engine drops to a transparent watchlist with the reason stated. Each node is
loaded once and run through both engines together, so we never double-fetch.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from trade.analysis.claude_analyst import AnalystVerdict, analyse
from trade.analysis.confidence import attach_confidence
from trade.analysis.data_packet import CandidatePacket, build_packet
from trade.config_loader import node_id, universe
from trade.data.loader import CompanyFinancials, load_company
from trade.industry.catalyst_detector import CatalystHeat, detect_catalysts
from trade.industry.demand_chain import node_evidence, score_candidate
from trade.industry.supply_chain import Catalyst, build_graph, get_catalyst, propagate
from trade.metrics.fundamentals import compute_metrics
from trade.metrics.scoring import score_metrics


@dataclass
class Recommendation:
    packet: CandidatePacket
    verdict: AnalystVerdict


@dataclass
class PipelineResult:
    catalyst: Catalyst
    period: str                       # ISO year-week, e.g. "2026-W22"
    generated_at: str
    catalyst_heat: list[CatalystHeat]
    recommendations: list[Recommendation] = field(default_factory=list)
    watchlist: list[CandidatePacket] = field(default_factory=list)


def _iso_period(now: dt.datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _names() -> dict[str, str]:
    out: dict[str, str] = {}
    for market in ("tw", "us"):
        try:
            out.update({node_id(market, str(r["id"])): r.get("name", "") for r in universe(market)})
        except Exception:
            pass
    return out


def run_pipeline(
    catalyst: str | None = None,
    max_hops: int = 4,
    run_analyst: bool = True,
    financials_provider: Callable[[str, str, str], CompanyFinancials] = load_company,
    now: dt.datetime | None = None,
) -> PipelineResult:
    now = now or dt.datetime.now()
    heats = detect_catalysts(financials_provider)
    if catalyst:
        cat = get_catalyst(catalyst)
        if cat is None:
            raise ValueError(f"Unknown catalyst {catalyst!r}")
    else:
        cat = heats[0].catalyst if heats else None
    if cat is None:
        raise ValueError("No catalysts configured")

    graph = build_graph()
    propagated = propagate(graph, cat.seeds, max_hops)
    names = _names()

    recs: list[Recommendation] = []
    watch: list[CandidatePacket] = []

    for pn in propagated:
        name = names.get(pn.node_id, "")
        try:
            cf = financials_provider(pn.market, pn.ticker, name)
        except Exception as exc:
            print(f"[pipeline] skip {pn.node_id}: {exc}")
            continue

        metrics = compute_metrics(cf)
        fs = score_metrics(metrics)
        candidate = score_candidate(pn, name, node_evidence(cf))
        packet = attach_confidence(build_packet(cf, metrics, fs, candidate, cat, graph))

        passes_fundamentals = fs.passes
        passes_demand = candidate.propagation_score > 0 and not candidate.narrative_assumption

        if passes_fundamentals and passes_demand:
            verdict = analyse(packet) if run_analyst else AnalystVerdict(
                "(analyst disabled for this run)", "", available=False, note="run_analyst=False")
            recs.append(Recommendation(packet, verdict))
        else:
            packet.reason = _watch_reason(passes_fundamentals, candidate)
            watch.append(packet)

    recs.sort(key=lambda r: (r.packet.confidence or 0), reverse=True)
    watch.sort(key=lambda p: (p.confidence or 0), reverse=True)

    return PipelineResult(
        catalyst=cat, period=_iso_period(now), generated_at=now.strftime("%Y-%m-%d %H:%M"),
        catalyst_heat=heats, recommendations=recs, watchlist=watch,
    )


def _watch_reason(passes_fundamentals: bool, candidate) -> str:
    reasons = []
    if not passes_fundamentals:
        reasons.append("基本面未過門檻")
    if candidate.narrative_assumption:
        reasons.append("傳導鏈含未查證環節（敘事假設）")
    elif candidate.propagation_score <= 0:
        reasons.append("無足夠需求流動證據")
    return "；".join(reasons) or "—"
