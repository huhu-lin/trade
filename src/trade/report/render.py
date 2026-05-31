"""Render the weekly Markdown report from a PipelineResult via Jinja2."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Plain-Chinese labels for the raw metric keys, so non-experts can read the tables.
# Unknown keys fall back to the raw key (see `_metric_label`).
_METRIC_LABELS = {
    # 引擎一 · 基本面
    "gross_margin": "毛利率",
    "operating_margin": "營業利益率",
    "roe": "股東權益報酬率（ROE，賺錢效率）",
    "debt_ratio": "負債比率（越低越穩健）",
    "ocf_to_net_income": "現金流／淨利（盈餘品質）",
    "revenue_cagr_3y": "近三年營收年複成長",
    "earnings_growth_yoy": "淨利年增率",
    "fcf_margin": "自由現金流利潤率",
    "fcf_yield": "自由現金流殖利率（估值，越高越便宜）",
    # 引擎二 · 需求證據
    "revenue_yoy": "營收年增率",
    "capex_yoy": "資本支出年增率（擴產投資）",
    "receivables_yoy": "應收帳款年增率（接單能見度）",
    "monthly_revenue_yoy": "月營收年增率（台股獨有，需求領先訊號）",
}


def _metric_label(key: str) -> str:
    return _METRIC_LABELS.get(key, key)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=()),  # markdown, not HTML
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["metric_label"] = _metric_label
    return env


def render_report(result) -> str:
    """result is a trade.pipeline.PipelineResult. Returns the Markdown body."""
    heat_view = [
        {"catalyst": h.catalyst, "heat": h.heat, "summary": _heat_summary(h)}
        for h in result.catalyst_heat
    ]
    template = _env().get_template("report.md.j2")
    return template.render(
        period=result.period,
        generated_at=result.generated_at,
        catalyst=result.catalyst,
        recommendations=result.recommendations,
        watchlist=result.watchlist,
        catalyst_heat=heat_view,
    )


def _heat_summary(heat) -> str:
    parts = []
    for nid, evs in heat.signals.items():
        bits = [f"{e.metric.split('_')[0]} {e.value:+.0%}" for e in evs if e.available]
        if bits:
            parts.append(f"{nid}: {', '.join(bits)}")
    return " ｜ ".join(parts) if parts else "資料不足"


def write_report(result, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{result.period}.md"
    out.write_text(render_report(result), encoding="utf-8")
    return out
