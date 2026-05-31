"""Engine one: compute fundamental metrics from CompanyFinancials.

Every metric carries its provenance (the exact concept + fiscal year + raw value it was
built from). This is what lets the report cite precise data and lets the analyst layer
refuse to make a claim that isn't backed by a populated input. A metric whose inputs are
missing has value=None — never a guessed number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from trade.data.loader import CompanyFinancials


@dataclass
class Metric:
    name: str
    value: float | None
    inputs: list[dict] = field(default_factory=list)  # [{concept, year, value}]
    note: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None and not (
            isinstance(self.value, float) and math.isnan(self.value)
        )


def _cell(annual: pd.DataFrame, concept: str, year: int) -> float | None:
    if concept not in annual.columns or year not in annual.index:
        return None
    v = annual.loc[year, concept]
    if pd.isna(v):
        return None
    return float(v)


def _years(annual: pd.DataFrame) -> list[int]:
    return sorted(int(y) for y in annual.index)


def compute_metrics(cf: CompanyFinancials) -> dict[str, Metric]:
    a = cf.annual
    metrics: dict[str, Metric] = {}

    def add(name: str, value, inputs, note=""):
        metrics[name] = Metric(name, value, inputs, note)

    if not cf.has_annual() or not _years(a):
        for n in (
            "revenue_cagr_3y", "earnings_growth_yoy", "gross_margin", "operating_margin",
            "roe", "debt_ratio", "fcf_margin", "ocf_to_net_income", "fcf_yield",
        ):
            add(n, None, [], "no annual data")
        return metrics

    years = _years(a)
    L = years[-1]  # latest complete fiscal year

    def ratio(name, num_concept, den_concept, note=""):
        num = _cell(a, num_concept, L)
        den = _cell(a, den_concept, L)
        inputs = [
            {"concept": num_concept, "year": L, "value": num},
            {"concept": den_concept, "year": L, "value": den},
        ]
        val = (num / den) if (num is not None and den not in (None, 0)) else None
        add(name, val, inputs, note)

    ratio("gross_margin", "gross_profit", "revenue")
    ratio("operating_margin", "operating_income", "revenue")
    ratio("roe", "net_income", "equity", "ending equity")
    ratio("debt_ratio", "liabilities", "assets")
    ratio("ocf_to_net_income", "operating_cash_flow", "net_income", "earnings quality")

    # revenue 3y CAGR (uses up to 4 years; falls back to longest available span >=2y)
    rev_L = _cell(a, "revenue", L)
    span_inputs = [{"concept": "revenue", "year": L, "value": rev_L}]
    cagr = None
    for back in (3, 2, 1):
        if len(years) > back:
            y0 = years[-1 - back]
            rev_0 = _cell(a, "revenue", y0)
            span_inputs.append({"concept": "revenue", "year": y0, "value": rev_0})
            if rev_L and rev_0 and rev_0 > 0:
                cagr = (rev_L / rev_0) ** (1 / back) - 1
            break
    add("revenue_cagr_3y", cagr, span_inputs, "annualised over available span")

    # earnings growth YoY (net income — available in both markets)
    ni_L = _cell(a, "net_income", L)
    ni_prev = _cell(a, "net_income", years[-2]) if len(years) >= 2 else None
    eg = (ni_L / ni_prev - 1) if (ni_L is not None and ni_prev not in (None, 0)) else None
    if ni_prev is not None and ni_prev < 0:
        eg = None  # growth off a negative base is meaningless
    add("earnings_growth_yoy", eg,
        [{"concept": "net_income", "year": L, "value": ni_L},
         {"concept": "net_income", "year": years[-2] if len(years) >= 2 else L, "value": ni_prev}])

    # free cash flow + derived
    ocf = _cell(a, "operating_cash_flow", L)
    capex = _cell(a, "capex", L)
    fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
    fcf_inputs = [
        {"concept": "operating_cash_flow", "year": L, "value": ocf},
        {"concept": "capex", "year": L, "value": capex},
    ]
    fcf_margin = (fcf / rev_L) if (fcf is not None and rev_L not in (None, 0)) else None
    add("fcf_margin", fcf_margin, fcf_inputs + [{"concept": "revenue", "year": L, "value": rev_L}])

    market_cap = cf.valuation.get("market_cap")
    fcf_yield = (fcf / market_cap) if (fcf is not None and market_cap) else None
    add("fcf_yield", fcf_yield,
        fcf_inputs + [{"concept": "market_cap", "year": L, "value": market_cap}],
        "fcf / market cap")

    return metrics
