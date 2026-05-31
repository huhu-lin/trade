"""Engine two, part 0: which demand chain is actually accelerating right now?

A catalyst (e.g. "AI capex") is only worth propagating if its *source* is heating up. We
measure that from the catalyst's `drivers` (the hyperscalers funding the spend) and its
`seeds` (the chips the money first lands on), using the same hard signals as the chain:
capex growth (the spend itself) and revenue growth (demand pulling through). The heat
score is deterministic — it ranks catalysts so the weekly run can auto-pick the hottest
chains, but it never fabricates: a driver with no data simply doesn't contribute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from trade.data.loader import CompanyFinancials, load_company
from trade.industry.demand_chain import DemandEvidence, node_evidence
from trade.industry.supply_chain import Catalyst, catalysts

# Signals that indicate the catalyst's demand source is accelerating.
_HEAT_SIGNALS = ("capex_yoy", "revenue_yoy")


@dataclass
class CatalystHeat:
    catalyst: Catalyst
    heat: float                                  # 0..100
    signals: dict[str, list[DemandEvidence]]     # node_id -> its heat evidence
    data_gaps: list[str] = field(default_factory=list)  # nodes with no usable signal


def _parse(node_id: str) -> tuple[str, str]:
    market, ticker = node_id.split(":", 1)
    return market, ticker


def detect_catalysts(
    financials_provider: Callable[[str, str, str], CompanyFinancials] = load_company,
    cfg: dict | None = None,
) -> list[CatalystHeat]:
    """Score every configured catalyst by the momentum of its drivers + seeds, hottest
    first. `financials_provider` is injectable for testing without network."""
    results: list[CatalystHeat] = []
    for cat in catalysts(cfg):
        nodes = list(dict.fromkeys(cat.drivers + cat.seeds))  # dedup, preserve order
        per_node: dict[str, list[DemandEvidence]] = {}
        gaps: list[str] = []
        strengths: list[float] = []
        for nid in nodes:
            market, ticker = _parse(nid)
            try:
                cf = financials_provider(market, ticker, "")
                ev = [e for e in node_evidence(cf) if e.metric in _HEAT_SIGNALS]
            except Exception:
                ev = []
            usable = [e for e in ev if e.available and e.strength is not None]
            per_node[nid] = ev
            if usable:
                strengths.extend(e.strength for e in usable)
            else:
                gaps.append(nid)
        heat = round(100 * sum(strengths) / len(strengths), 2) if strengths else 0.0
        results.append(CatalystHeat(cat, heat, per_node, gaps))
    results.sort(key=lambda c: c.heat, reverse=True)
    return results
