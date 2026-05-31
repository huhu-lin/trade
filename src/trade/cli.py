"""Command-line entry point.

Subcommands grow with the milestones:
  fetch  — M1: pull + cache fundamentals for a single ticker (smoke test the data layer)
  screen — M2: rank a market's universe by the deterministic fundamentals score
"""

from __future__ import annotations

import argparse
import csv
import sys

from trade.config_loader import screening_rules, universe
from trade.data.loader import load_company
from trade.industry.catalyst_detector import detect_catalysts
from trade.industry.demand_chain import analyse_chain, score_candidate
from trade.industry.supply_chain import build_graph, get_catalyst, propagate
from trade.metrics.fundamentals import compute_metrics
from trade.metrics.scoring import score_metrics
from trade.settings import REPORTS_DIR


def _cmd_fetch(args: argparse.Namespace) -> int:
    cf = load_company(args.market, args.ticker)
    print(f"# {cf.market.upper()} {cf.ticker} {cf.name}".rstrip())
    if cf.has_annual():
        print("\n## Annual financials (most recent rows)")
        print(cf.annual.tail(4).to_string())
    else:
        print("\n[!] No annual financials retrieved.")
    if cf.monthly_revenue is not None and not cf.monthly_revenue.empty:
        print("\n## Monthly revenue (last 6)")
        print(cf.monthly_revenue.tail(6).to_string(index=False))
    if cf.valuation:
        print("\n## Valuation")
        for k, v in cf.valuation.items():
            print(f"  {k}: {v}")
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    rows = universe(args.market)
    if args.limit:
        rows = rows[: args.limit]
    min_score = screening_rules().get("min_fundamentals_score", 0)

    results = []
    for entry in rows:
        ticker = str(entry["id"])
        name = entry.get("name", "")
        try:
            cf = load_company(args.market, ticker, name)
        except Exception as exc:  # a single bad ticker must not abort the whole run
            print(f"[!] {args.market}:{ticker} {name}: fetch failed ({exc})", file=sys.stderr)
            continue
        fs = score_metrics(compute_metrics(cf))
        results.append((entry, cf, fs))
        flag = "PASS" if fs.score >= min_score else "drop"
        print(f"  {args.market}:{ticker:<8} {name[:18]:<18} score={fs.score:6.2f}  [{flag}]  "
              f"missing={len(fs.missing)}")

    results.sort(key=lambda r: r[2].score, reverse=True)

    out = REPORTS_DIR / f"screen_{args.market}.csv"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    groups = list(screening_rules()["weights"].keys())
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["market", "ticker", "name", "score", "passes",
                    *(f"grp_{g}" for g in groups), "missing"])
        for entry, cf, fs in results:
            w.writerow([
                args.market, entry["id"], entry.get("name", ""), fs.score, fs.passes,
                *(fs.by_group.get(g, 0.0) for g in groups),
                ";".join(fs.missing),
            ])
    print(f"\nRanked {len(results)} names -> {out}")
    return 0


def _cmd_chain(args: argparse.Namespace) -> int:
    cat = get_catalyst(args.catalyst)
    if cat is None:
        print(f"[!] Unknown catalyst {args.catalyst!r}", file=sys.stderr)
        return 2
    print(f"# Catalyst: {cat.id} — {cat.name}")
    print(f"  seeds={cat.seeds}  drivers={cat.drivers}")

    if args.structure_only:
        # Pure graph view: who's on the chain and is the link verified — no API calls.
        g = build_graph()
        nodes = propagate(g, cat.seeds, args.max_hops)
        for c in (score_candidate(n, "", []) for n in nodes):
            n = c.node
            flag = "" if n.fully_verified else "  <NARRATIVE ASSUMPTION (unverified link)>"
            print(f"  {n.node_id:<10} hop={n.hops}  path={'->'.join(n.path)}{flag}")
            if n.hops > 0:
                src = n.edge_source or "(no source)"
                print(f"             via: {n.relation}  | source: {src}")
        return 0

    cat, candidates = analyse_chain(cat, max_hops=args.max_hops)
    print("\n## Demand-chain candidates (by propagation score)\n")
    for c in candidates:
        tag = "NARRATIVE" if c.narrative_assumption else "verified "
        print(f"  {c.node_id:<10} {c.name[:16]:<16} prop={c.propagation_score:6.2f} "
              f"[{tag}] strength={c.evidence_strength:.2f} hop={c.node.hops}")
        for e in c.evidence:
            if e.available:
                print(f"       - {e.metric:22} {e.value:+.2%}  ({e.period}, {e.source})")
        if c.data_gaps:
            print(f"       gaps: {', '.join(c.data_gaps)}")
    return 0


def _cmd_catalysts(args: argparse.Namespace) -> int:
    heats = detect_catalysts()
    print("# Catalyst heat (driver + seed momentum)\n")
    for h in heats:
        print(f"  {h.catalyst.id:<12} heat={h.heat:6.2f}  {h.catalyst.name}")
        for nid, evs in h.signals.items():
            parts = [f"{e.metric}={e.value:+.1%}" for e in evs if e.available]
            print(f"     {nid:<10} {'  '.join(parts) if parts else '(no data)'}")
        if h.data_gaps:
            print(f"     gaps: {', '.join(h.data_gaps)}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from trade.notify.notifier import notify, summarise
    from trade.pipeline import run_pipeline
    from trade.report.render import write_report

    result = run_pipeline(catalyst=args.catalyst, max_hops=args.max_hops,
                          run_analyst=not args.no_analyst)
    out = write_report(result, REPORTS_DIR)
    print(f"# Report written -> {out}")
    print(f"  catalyst={result.catalyst.id}  recommendations={len(result.recommendations)}  "
          f"watchlist={len(result.watchlist)}")
    for r in result.recommendations:
        p = r.packet
        print(f"  - {p.market}:{p.ticker:<8} {p.name[:16]:<16} confidence={p.confidence}")

    if not args.no_notify:
        sent = notify(summarise(result))
        print(f"  notified: {sent or 'none configured'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade", description="Value-investing screener")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch & cache fundamentals for one ticker")
    p_fetch.add_argument("--market", required=True, choices=["tw", "us"])
    p_fetch.add_argument("--ticker", required=True)
    p_fetch.set_defaults(func=_cmd_fetch)

    p_screen = sub.add_parser("screen", help="Rank a market's universe by fundamentals score")
    p_screen.add_argument("--market", required=True, choices=["tw", "us"])
    p_screen.add_argument("--limit", type=int, default=0, help="Only screen the first N names")
    p_screen.set_defaults(func=_cmd_screen)

    p_chain = sub.add_parser("chain", help="Walk a catalyst's demand chain + score nodes")
    p_chain.add_argument("--catalyst", required=True, help="catalyst id (see config/supply_chain.yml)")
    p_chain.add_argument("--max-hops", type=int, default=4)
    p_chain.add_argument("--structure-only", action="store_true",
                         help="Show graph propagation + verified flags without fetching evidence")
    p_chain.set_defaults(func=_cmd_chain)

    p_cat = sub.add_parser("catalysts", help="Rank configured catalysts by demand heat")
    p_cat.set_defaults(func=_cmd_catalysts)

    p_run = sub.add_parser("run", help="Full pipeline: screen -> chain -> analyse -> report -> notify")
    p_run.add_argument("--catalyst", default=None, help="catalyst id (default: hottest detected)")
    p_run.add_argument("--max-hops", type=int, default=4)
    p_run.add_argument("--no-analyst", action="store_true", help="Skip the Claude analysis layer")
    p_run.add_argument("--no-notify", action="store_true", help="Skip Discord/Telegram push")
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
