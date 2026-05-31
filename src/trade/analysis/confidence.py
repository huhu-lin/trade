"""Deterministic dual-engine confidence — the number Claude CANNOT inflate.

Confidence is a pure function of the packet's facts:
  confidence = (w_f * fundamentals + w_p * propagation) * completeness_factor

  - fundamentals (0..100): engine one's score.
  - propagation (0..100): engine two's score, ALREADY gated to 0 when the chain path has
    an unverified link — so a narrative assumption contributes nothing here by design.
  - completeness_factor (0.6..1.0): scales the whole thing down when inputs are missing,
    so "great scores but half the data is absent" never reads as high confidence.

The breakdown is returned in full so the report can show exactly how the number was
built, which links were narrative assumptions, and where the data gaps are. Same inputs
always yield the same confidence — the analyst prose is constrained to *explain* it, never
to set it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trade.analysis.data_packet import CandidatePacket

W_FUNDAMENTALS = 0.5
W_PROPAGATION = 0.5

# How far missing data can drag confidence down (1.0 = full data, 0.6 = everything missing).
_COMPLETENESS_FLOOR = 0.6

_TIERS = ((70.0, "high"), (45.0, "medium"), (0.0, "low"))


@dataclass
class ConfidenceResult:
    confidence: float            # 0..100
    tier: str                    # high / medium / low
    breakdown: dict = field(default_factory=dict)
    narrative_links: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


def _completeness(packet: CandidatePacket) -> tuple[float, int, int]:
    """Fraction of expected inputs that are actually present, across both engines."""
    total = len(packet.metrics) + len(packet.demand_evidence)
    missing = len(packet.fundamentals_missing) + len(packet.demand_data_gaps)
    if total == 0:
        return _COMPLETENESS_FLOOR, 0, 0
    present_frac = max(0.0, (total - missing) / total)
    factor = _COMPLETENESS_FLOOR + (1.0 - _COMPLETENESS_FLOOR) * present_frac
    return factor, missing, total


def _tier(score: float) -> str:
    for threshold, label in _TIERS:
        if score >= threshold:
            return label
    return "low"


def compute_confidence(packet: CandidatePacket) -> ConfidenceResult:
    factor, missing, total = _completeness(packet)
    weighted = W_FUNDAMENTALS * packet.fundamentals_score + W_PROPAGATION * packet.propagation_score
    confidence = round(weighted * factor, 2)

    narrative_links = [
        f"{ln.from_id}->{ln.to_id} ({ln.relation})"
        for ln in packet.chain_links if not ln.verified
    ]
    gaps = list(packet.fundamentals_missing) + [f"demand:{g}" for g in packet.demand_data_gaps]

    breakdown = {
        "fundamentals_score": packet.fundamentals_score,
        "propagation_score": packet.propagation_score,
        "weights": {"fundamentals": W_FUNDAMENTALS, "propagation": W_PROPAGATION},
        "weighted_base": round(weighted, 2),
        "completeness_factor": round(factor, 4),
        "inputs_present": total - missing,
        "inputs_total": total,
        "narrative_assumption": packet.narrative_assumption,
    }
    return ConfidenceResult(confidence, _tier(confidence), breakdown, narrative_links, gaps)


def attach_confidence(packet: CandidatePacket) -> CandidatePacket:
    """Compute confidence and write it back into the packet (so it travels to the report
    and to the analyst as a fact it must explain, not invent)."""
    result = compute_confidence(packet)
    packet.confidence = result.confidence
    packet.confidence_tier = result.tier
    packet.confidence_breakdown = result.breakdown
    return packet
