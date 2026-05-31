"""M4: the deterministic confidence rubric and packet assembly. The key guarantees:
confidence is a pure function of packet facts (reproducible), an unverified chain link is
recorded as a narrative assumption, and missing data drags confidence down via the
completeness factor — never silently ignored."""

from trade.analysis.confidence import compute_confidence
from trade.analysis.data_packet import (
    CandidatePacket,
    ChainLink,
    EvidenceFact,
    MetricFact,
)


def _packet(**over) -> CandidatePacket:
    base = dict(
        market="us", ticker="NVDA", name="NVIDIA",
        catalyst_id="ai_capex", catalyst_name="AI capex",
        fundamentals_score=80.0, fundamentals_passes=True,
        metrics=[MetricFact(name="gross_margin", value=0.7, sub_score=1.0)],
        fundamentals_missing=[],
        propagation_score=60.0, chain_hops=1, chain_path=["us:NVDA", "us:MU"],
        chain_links=[ChainLink(from_id="us:NVDA", to_id="us:MU", relation="HBM",
                               source="Micron call", verified=True)],
        demand_evidence=[EvidenceFact(metric="revenue_yoy", value=0.3, strength=1.0)],
        demand_data_gaps=[],
    )
    base.update(over)
    return CandidatePacket(**base)


def test_confidence_is_deterministic_and_reproducible():
    p = _packet()
    a = compute_confidence(p)
    b = compute_confidence(p)
    assert a.confidence == b.confidence
    # full data -> completeness factor 1.0; base = 0.5*80 + 0.5*60 = 70.
    assert a.breakdown["completeness_factor"] == 1.0
    assert a.confidence == 70.0
    assert a.tier == "high"


def test_missing_data_drags_confidence_below_the_clean_score():
    clean = compute_confidence(_packet())
    gapped = compute_confidence(_packet(
        fundamentals_missing=["roe", "fcf_yield"],
        demand_data_gaps=["monthly_revenue_yoy"],
    ))
    # Same scores, but with gaps the completeness factor < 1 pulls confidence down.
    assert gapped.confidence < clean.confidence
    assert gapped.breakdown["completeness_factor"] < 1.0
    assert "roe" in gapped.data_gaps
    assert "demand:monthly_revenue_yoy" in gapped.data_gaps


def test_unverified_link_is_flagged_as_narrative_assumption():
    p = _packet(
        propagation_score=0.0,  # engine two already gated this to 0 upstream
        narrative_assumption=True,
        chain_links=[ChainLink(from_id="us:NVDA", to_id="tw:3661",
                               relation="guess", source="", verified=False)],
    )
    result = compute_confidence(p)
    assert result.narrative_links == ["us:NVDA->tw:3661 (guess)"]
    assert result.breakdown["narrative_assumption"] is True
    # With propagation gated to 0, confidence rests on fundamentals only -> strictly lower.
    assert result.confidence < compute_confidence(_packet()).confidence
