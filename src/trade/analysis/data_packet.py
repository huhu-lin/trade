"""Engine-three input: the evidence packet.

A `CandidatePacket` is the ONLY thing the Claude analyst is allowed to see. It bundles
both engines' output for one company as structured, sourced facts — every number carries
its provenance (concept/year for fundamentals; period/source for demand signals) and a
missing input is an explicit `None`, never a blank that could be mistaken for zero. This
is what lets the analyst layer cite precise data and refuse to assert anything the packet
doesn't contain.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from trade.data.loader import CompanyFinancials
from trade.industry.demand_chain import ChainCandidate
from trade.industry.supply_chain import Catalyst
from trade.metrics.fundamentals import Metric
from trade.metrics.scoring import FundamentalsScore


class MetricFact(BaseModel):
    name: str
    value: float | None
    inputs: list[dict] = Field(default_factory=list)  # [{concept, year, value}]
    sub_score: float | None = None  # 0..1 vs config thresholds
    note: str = ""


class EvidenceFact(BaseModel):
    metric: str
    value: float | None
    period: str = ""
    source: str = ""
    strength: float | None = None  # 0..1
    note: str = ""


class ChainLink(BaseModel):
    from_id: str
    to_id: str
    relation: str
    source: str = ""
    verified: bool = False


class CandidatePacket(BaseModel):
    market: str
    ticker: str
    name: str = ""

    catalyst_id: str
    catalyst_name: str
    catalyst_source: str = ""

    # --- engine one: quantitative fundamentals ---
    fundamentals_score: float
    fundamentals_passes: bool
    fundamentals_by_group: dict[str, float] = Field(default_factory=dict)
    metrics: list[MetricFact] = Field(default_factory=list)
    fundamentals_missing: list[str] = Field(default_factory=list)

    # --- engine two: demand propagation ---
    propagation_score: float
    chain_hops: int
    chain_path: list[str] = Field(default_factory=list)
    chain_links: list[ChainLink] = Field(default_factory=list)  # edges along the path
    demand_evidence: list[EvidenceFact] = Field(default_factory=list)
    narrative_assumption: bool = False  # path has an unverified link
    demand_data_gaps: list[str] = Field(default_factory=list)

    valuation: dict = Field(default_factory=dict)

    # --- filled by confidence.py (deterministic) ---
    confidence: float | None = None
    confidence_tier: str = ""
    confidence_breakdown: dict = Field(default_factory=dict)

    # --- filled by the pipeline when a node lands on the watchlist ---
    reason: str = ""


def _metric_facts(metrics: dict[str, Metric], fs: FundamentalsScore) -> list[MetricFact]:
    subs = {d.name: d.sub_score for d in fs.details}
    out = []
    for name, m in metrics.items():
        out.append(MetricFact(name=name, value=m.value, inputs=m.inputs,
                              sub_score=subs.get(name), note=m.note))
    return out


def _chain_links(path: list[str], graph) -> list[ChainLink]:
    """Resolve each hop on the path back to its edge attributes so the packet carries the
    source + verified flag for every link in the causal story."""
    links = []
    for a, b in zip(path, path[1:]):
        if graph is not None and graph.has_edge(a, b):
            attrs = graph.edges[a, b]
            links.append(ChainLink(from_id=a, to_id=b, relation=attrs.get("relation", ""),
                                   source=attrs.get("source", "") or "",
                                   verified=bool(attrs.get("verified", False))))
        else:
            links.append(ChainLink(from_id=a, to_id=b, relation="", source="", verified=False))
    return links


def build_packet(
    cf: CompanyFinancials,
    metrics: dict[str, Metric],
    fs: FundamentalsScore,
    candidate: ChainCandidate,
    catalyst: Catalyst,
    graph=None,
) -> CandidatePacket:
    demand = [
        EvidenceFact(metric=e.metric, value=e.value, period=e.period,
                     source=e.source, strength=e.strength, note=e.note)
        for e in candidate.evidence
    ]
    return CandidatePacket(
        market=cf.market, ticker=cf.ticker, name=cf.name or candidate.name,
        catalyst_id=catalyst.id, catalyst_name=catalyst.name, catalyst_source=catalyst.source,
        fundamentals_score=fs.score, fundamentals_passes=fs.passes,
        fundamentals_by_group=fs.by_group, metrics=_metric_facts(metrics, fs),
        fundamentals_missing=fs.missing,
        propagation_score=candidate.propagation_score, chain_hops=candidate.node.hops,
        chain_path=candidate.node.path, chain_links=_chain_links(candidate.node.path, graph),
        demand_evidence=demand, narrative_assumption=candidate.narrative_assumption,
        demand_data_gaps=candidate.data_gaps, valuation=cf.valuation,
    )
