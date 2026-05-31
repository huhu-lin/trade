"""Hand-checked scoring math: the 0..1 linear map and weight renormalisation are the
parts that silently corrupt a ranking if they drift, so pin them with known numbers."""

from pytest import approx

from trade.metrics.fundamentals import Metric
from trade.metrics.scoring import _linear, score_metrics


def test_linear_higher_is_better_clamped():
    # gross_margin: floor 0.10, good 0.50
    assert _linear(0.50, 0.10, 0.50, "higher") == 1.0
    assert _linear(0.10, 0.10, 0.50, "higher") == 0.0
    assert _linear(0.30, 0.10, 0.50, "higher") == approx(0.5)
    assert _linear(0.80, 0.10, 0.50, "higher") == 1.0   # above good clamps to 1
    assert _linear(0.00, 0.10, 0.50, "higher") == 0.0   # below floor clamps to 0


def test_linear_lower_is_better_inverted():
    # debt_ratio: direction lower, good 0.30, floor 0.80
    assert _linear(0.30, 0.80, 0.30, "lower") == 1.0    # low debt = best
    assert _linear(0.80, 0.80, 0.30, "lower") == 0.0    # high debt = worst
    assert _linear(0.55, 0.80, 0.30, "lower") == approx(0.5)
    assert _linear(0.10, 0.80, 0.30, "lower") == 1.0    # below good clamps to 1


def test_missing_metric_excluded_not_penalised():
    # A group with one perfect metric and one missing metric scores as the perfect one,
    # not as a half — the missing metric must not act like a zero.
    metrics = {
        "gross_margin": Metric("gross_margin", 0.50, []),      # -> 1.0
        "operating_margin": Metric("operating_margin", None, []),  # missing
        "roe": Metric("roe", 0.25, []),                         # -> 1.0
    }
    result = score_metrics(metrics)
    assert result.by_group["profitability"] == 100.0
    assert "operating_margin" in result.missing


def test_score_is_zero_to_hundred_and_deterministic():
    metrics = {
        "gross_margin": Metric("gross_margin", 0.30, []),  # mid -> 0.5
        "debt_ratio": Metric("debt_ratio", 0.55, []),      # mid -> 0.5
    }
    a = score_metrics(metrics)
    b = score_metrics(metrics)
    assert a.score == b.score
    assert 0.0 <= a.score <= 100.0
    # Only two groups have data (profitability 0.30 weight, balance_sheet 0.15);
    # both sub-scores are 0.5, so the renormalised total is exactly 50.
    assert a.score == 50.0
