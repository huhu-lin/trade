"""Engine two: pin the propagation mechanics and — most importantly — the verified-gate.
An unverified edge must NEVER let a node's demand evidence raise its propagation score,
because that gate is the project's core anti-hallucination guarantee."""

import pandas as pd

from trade.data.loader import CompanyFinancials
from trade.industry.demand_chain import (
    DemandEvidence,
    node_evidence,
    score_candidate,
)
from trade.industry.supply_chain import build_graph, propagate

# A tiny synthetic graph: SEED -[verified]-> A -[verified]-> B, and SEED -[UNVERIFIED]-> C.
_CFG = {
    "catalysts": [{"id": "test", "name": "t", "seeds": ["m:SEED"], "drivers": []}],
    "edges": [
        {"from": "m:SEED", "to": "m:A", "relation": "verified link", "source": "x", "verified": True},
        {"from": "m:A", "to": "m:B", "relation": "verified link", "source": "y", "verified": True},
        {"from": "m:SEED", "to": "m:C", "relation": "unverified guess", "source": "", "verified": False},
    ],
}


def test_propagate_tracks_hops_and_path_verification():
    g = build_graph(_CFG)
    nodes = {n.node_id: n for n in propagate(g, ["m:SEED"], max_hops=4)}

    assert nodes["m:SEED"].hops == 0 and nodes["m:SEED"].fully_verified
    assert nodes["m:A"].hops == 1 and nodes["m:A"].path_verified
    assert nodes["m:B"].hops == 2 and nodes["m:B"].path == ["m:SEED", "m:A", "m:B"]
    assert nodes["m:B"].fully_verified  # every edge on the path is verified

    # C is reachable but via an unverified edge -> surfaced, but NOT fully verified.
    assert nodes["m:C"].hops == 1
    assert nodes["m:C"].edge_verified is False
    assert nodes["m:C"].fully_verified is False


def test_unverified_path_gates_propagation_score_to_zero():
    g = build_graph(_CFG)
    nodes = {n.node_id: n for n in propagate(g, ["m:SEED"], max_hops=4)}

    # Strong demand evidence (all signals max strength).
    strong = [DemandEvidence("revenue_yoy", 1.0, "FY", "s", strength=1.0)]

    verified = score_candidate(nodes["m:A"], "A", strong)
    unverified = score_candidate(nodes["m:C"], "C", strong)

    assert verified.propagation_score > 0           # verified link earns score
    assert unverified.propagation_score == 0.0      # identical evidence, but gated to 0
    assert unverified.narrative_assumption is True
    assert verified.narrative_assumption is False


def test_position_factor_decays_with_hops():
    g = build_graph(_CFG)
    nodes = {n.node_id: n for n in propagate(g, ["m:SEED"], max_hops=4)}
    strong = [DemandEvidence("revenue_yoy", 1.0, "FY", "s", strength=1.0)]
    a = score_candidate(nodes["m:A"], "A", strong)   # 1 hop -> factor 0.5
    b = score_candidate(nodes["m:B"], "B", strong)   # 2 hops -> factor 0.333
    assert a.position_factor == 0.5
    assert b.propagation_score < a.propagation_score  # deeper = lower, same evidence


def test_node_evidence_monthly_yoy_and_missing_are_not_imputed():
    # TW company with 13 months of monthly revenue: Apr 2026 = 120 vs Apr 2025 = 100 -> +20%.
    dates = pd.date_range("2025-04-30", periods=13, freq="ME")
    rev = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 120]
    cf = CompanyFinancials(
        market="tw", ticker="9999",
        annual=pd.DataFrame(),  # no annual -> annual signals must be None, not guessed
        monthly_revenue=pd.DataFrame({"date": dates, "revenue": rev}),
    )
    ev = {e.metric: e for e in node_evidence(cf)}
    assert ev["monthly_revenue_yoy"].available
    assert abs(ev["monthly_revenue_yoy"].value - 0.20) < 1e-9
    assert abs(ev["monthly_revenue_yoy"].strength - 1.0) < 1e-9  # +20% hits "good"
    # Annual frame empty -> these are explicitly missing, never fabricated.
    assert ev["revenue_yoy"].value is None
    assert ev["capex_yoy"].value is None
