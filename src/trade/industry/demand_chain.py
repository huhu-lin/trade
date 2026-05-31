"""Engine two, part 2: attach "is demand actually flowing here?" evidence to each node a
catalyst propagates to, and score it.

The evidence is HARD, pullable data — never narrative:
  - monthly_revenue_yoy : TW monthly sales vs same month last year (strongest LEADING
                          signal; TW-only, FinMind).
  - revenue_yoy         : latest annual revenue vs prior year.
  - capex_yoy           : capex growth — capacity build = management betting on demand.
  - receivables_yoy     : order-visibility proxy (rising AR alongside rising revenue =
                          real shipped orders, not channel stuffing).

Each signal carries value + period + source so the report can cite it; a missing input
yields value=None and simply doesn't count — it is never imputed.

Scoring: propagation_score = 100 * position_factor * evidence_strength, then GATED to 0
unless the whole path from the catalyst is verified. An unverified link is a narrative
assumption: we still surface the node and its evidence so the analyst sees the hypothesis,
but it cannot raise confidence (the user's #1 requirement).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from trade.config_loader import node_id, universe
from trade.data.loader import CompanyFinancials, load_company
from trade.industry.supply_chain import (
    Catalyst,
    PropagatedNode,
    build_graph,
    get_catalyst,
)
from trade.metrics.scoring import _linear

# (floor, good) growth thresholds per signal — value scoring 0.0 at floor, 1.0 at good.
_SIGNAL_THRESHOLDS = {
    "monthly_revenue_yoy": (0.0, 0.20),
    "revenue_yoy": (0.0, 0.25),
    "capex_yoy": (0.0, 0.30),
    "receivables_yoy": (0.0, 0.20),
}


@dataclass
class DemandEvidence:
    metric: str
    value: float | None     # a growth rate, e.g. 0.18 == +18%
    period: str             # "2026-04" (monthly) or "FY2025->FY2026" (annual)
    source: str             # data origin, e.g. "finmind:monthly_revenue"
    strength: float | None = None  # 0..1 normalised; None when value is missing
    note: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class ChainCandidate:
    node: PropagatedNode
    name: str
    evidence: list[DemandEvidence]
    evidence_strength: float       # 0..1 mean of available signals (ungated)
    position_factor: float         # 1/(1+hops)
    propagation_score: float       # 0..100, already gated by path verification
    narrative_assumption: bool     # True when the path is not fully verified
    data_gaps: list[str] = field(default_factory=list)

    @property
    def node_id(self) -> str:
        return self.node.node_id


def _yoy(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior in (None, 0) or prior < 0:
        return None
    return latest / prior - 1.0


def _annual_yoy(cf: CompanyFinancials, concept: str) -> tuple[float | None, str]:
    a = cf.annual
    if a.empty or concept not in a.columns:
        return None, ""
    years = sorted(int(y) for y in a.index)
    if len(years) < 2:
        return None, ""
    L, P = years[-1], years[-2]
    lv = a.loc[L, concept]
    pv = a.loc[P, concept]
    lv = None if pd.isna(lv) else float(lv)
    pv = None if pd.isna(pv) else float(pv)
    return _yoy(lv, pv), f"FY{P}->FY{L}"


def _monthly_yoy(cf: CompanyFinancials) -> tuple[float | None, str]:
    """Latest month vs the same calendar month a year earlier (TW only)."""
    mr = cf.monthly_revenue
    if mr is None or mr.empty or "revenue" not in mr.columns or "date" not in mr.columns:
        return None, ""
    df = mr.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if df.empty:
        return None, ""
    last = df.iloc[-1]
    target = last["date"] - pd.DateOffset(years=1)
    prior = df[(df["date"].dt.year == target.year) & (df["date"].dt.month == target.month)]
    if prior.empty:
        return None, last["date"].strftime("%Y-%m")
    return _yoy(float(last["revenue"]), float(prior.iloc[-1]["revenue"])), last["date"].strftime("%Y-%m")


def node_evidence(cf: CompanyFinancials) -> list[DemandEvidence]:
    """Extract demand-flow signals from one company's financials. Pure / testable."""
    ev: list[DemandEvidence] = []

    m_val, m_period = _monthly_yoy(cf)
    ev.append(DemandEvidence("monthly_revenue_yoy", m_val, m_period,
                             "finmind:monthly_revenue",
                             note="TW leading demand signal" if cf.market == "tw" else "n/a outside TW"))

    rev_val, rev_period = _annual_yoy(cf, "revenue")
    ev.append(DemandEvidence("revenue_yoy", rev_val, rev_period, f"{_src(cf)}:revenue"))

    cap_val, cap_period = _annual_yoy(cf, "capex")
    ev.append(DemandEvidence("capex_yoy", cap_val, cap_period, f"{_src(cf)}:capex",
                             note="capacity build = anticipated demand"))

    ar_val, ar_period = _annual_yoy(cf, "receivables")
    ev.append(DemandEvidence("receivables_yoy", ar_val, ar_period, f"{_src(cf)}:receivables",
                             note="order-visibility proxy"))

    for e in ev:
        if e.available and e.metric in _SIGNAL_THRESHOLDS:
            floor, good = _SIGNAL_THRESHOLDS[e.metric]
            e.strength = _linear(e.value, floor, good, "higher")
    return ev


def _src(cf: CompanyFinancials) -> str:
    return "edgar" if cf.market == "us" else "finmind"


def _name_lookup(market: str) -> dict[str, str]:
    try:
        return {node_id(market, str(r["id"])): r.get("name", "") for r in universe(market)}
    except Exception:
        return {}


def score_candidate(node: PropagatedNode, name: str, evidence: list[DemandEvidence]) -> ChainCandidate:
    strengths = [e.strength for e in evidence if e.available and e.strength is not None]
    evidence_strength = sum(strengths) / len(strengths) if strengths else 0.0
    position_factor = 1.0 / (1.0 + node.hops)
    gate = 1.0 if node.fully_verified else 0.0
    propagation_score = round(100 * position_factor * evidence_strength * gate, 2)
    gaps = [e.metric for e in evidence if not e.available]
    return ChainCandidate(
        node=node, name=name, evidence=evidence,
        evidence_strength=round(evidence_strength, 4),
        position_factor=round(position_factor, 4),
        propagation_score=propagation_score,
        narrative_assumption=not node.fully_verified,
        data_gaps=gaps,
    )


def analyse_chain(
    catalyst: str | Catalyst,
    max_hops: int = 4,
    financials_provider: Callable[[str, str, str], CompanyFinancials] = load_company,
    cfg: dict | None = None,
) -> tuple[Catalyst, list[ChainCandidate]]:
    """Walk a catalyst's demand chain and score every reachable node by position ×
    evidence strength (gated by path verification). `financials_provider` is injectable so
    the propagation + scoring logic can be tested with synthetic data."""
    cat = catalyst if isinstance(catalyst, Catalyst) else get_catalyst(catalyst, cfg)
    if cat is None:
        raise ValueError(f"Unknown catalyst {catalyst!r}")

    g = build_graph(cfg)
    propagated = propagate_for(g, cat.seeds, max_hops)

    names = {**_name_lookup("tw"), **_name_lookup("us")}
    candidates: list[ChainCandidate] = []
    for pn in propagated:
        try:
            cf = financials_provider(pn.market, pn.ticker, names.get(pn.node_id, ""))
            ev = node_evidence(cf)
        except Exception:
            ev = []  # data fetch failed: no evidence, node still surfaced with score 0
        candidates.append(score_candidate(pn, names.get(pn.node_id, ""), ev))

    candidates.sort(key=lambda c: c.propagation_score, reverse=True)
    return cat, candidates


def propagate_for(g, seeds, max_hops):
    from trade.industry.supply_chain import propagate
    return propagate(g, seeds, max_hops)
