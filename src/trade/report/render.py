"""Render the weekly Markdown report from a PipelineResult via Jinja2."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=()),  # markdown, not HTML
        trim_blocks=True,
        lstrip_blocks=True,
    )


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
