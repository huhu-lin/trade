"""Engine one, second half: turn raw Metrics into a 0..100 fundamentals score.

Scoring is fully deterministic and driven by config/screening_rules.yml — the same
inputs always produce the same score, and every sub-score is traceable back to the
metric value and the floor/good thresholds it was measured against. A metric with no
value (missing inputs) scores nothing and is excluded from its group's weight, so a
company is never penalised for a data gap the way it would be for a genuinely bad
number — but a group with no usable metrics at all contributes 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trade.config_loader import screening_rules
from trade.metrics.fundamentals import Metric


@dataclass
class MetricScore:
    name: str
    group: str
    value: float | None
    sub_score: float | None  # 0..1, or None when the metric value is missing
    weight_in_group: float
    note: str = ""


@dataclass
class FundamentalsScore:
    score: float  # 0..100
    by_group: dict[str, float] = field(default_factory=dict)  # group -> 0..100
    details: list[MetricScore] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # metric names with no value

    @property
    def passes(self) -> bool:
        return self.score >= screening_rules().get("min_fundamentals_score", 0)


def _linear(value: float, floor: float, good: float, direction: str) -> float:
    """Map a raw value onto 0..1, clamped. `good` scores 1.0, `floor` scores 0.0,
    linear in between. For direction 'lower', the relationship is inverted so that
    smaller raw values score better (floor is the worst/high value)."""
    if direction == "lower":
        # good < floor here (e.g. debt_ratio good 0.30, floor 0.80).
        if floor == good:
            return 1.0 if value <= good else 0.0
        frac = (floor - value) / (floor - good)
    else:
        if good == floor:
            return 1.0 if value >= good else 0.0
        frac = (value - floor) / (good - floor)
    return max(0.0, min(1.0, frac))


def score_metrics(metrics: dict[str, Metric]) -> FundamentalsScore:
    rules = screening_rules()
    weights: dict[str, float] = rules["weights"]
    metric_rules: dict[str, dict] = rules["metrics"]

    details: list[MetricScore] = []
    missing: list[str] = []
    # group -> list of (sub_score) for metrics that have a value
    group_scores: dict[str, list[float]] = {g: [] for g in weights}

    for name, spec in metric_rules.items():
        group = spec["group"]
        m = metrics.get(name)
        value = m.value if (m is not None and m.available) else None
        if value is None:
            missing.append(name)
            details.append(MetricScore(name, group, None, None, 0.0,
                                       "missing input" if m is None else (m.note or "no value")))
            continue
        sub = _linear(value, spec["floor"], spec["good"], spec.get("direction", "higher"))
        group_scores.setdefault(group, []).append(sub)
        details.append(MetricScore(name, group, value, sub, 0.0, m.note if m else ""))

    # Each group's score = simple mean of its available metrics' sub-scores (0..1).
    # A metric within a group carries equal weight; missing metrics simply don't dilute.
    by_group: dict[str, float] = {}
    total = 0.0
    total_weight_used = 0.0
    for group, w in weights.items():
        subs = group_scores.get(group, [])
        if not subs:
            by_group[group] = 0.0
            continue
        g_score = sum(subs) / len(subs)
        by_group[group] = round(g_score * 100, 2)
        total += g_score * w
        total_weight_used += w

    # Renormalise by the weight actually backed by data, so a company missing an entire
    # group (e.g. no valuation) is scored on what we know rather than dragged toward 0.
    score = (total / total_weight_used * 100) if total_weight_used else 0.0

    # Fill in the per-metric weight_in_group for transparency in the report.
    group_counts = {g: len(s) for g, s in group_scores.items()}
    for d in details:
        n = group_counts.get(d.group, 0)
        d.weight_in_group = round(1 / n, 4) if n else 0.0

    return FundamentalsScore(round(score, 2), by_group, details, missing)
