"""Engine two, part 1: the supply-chain demand graph.

This is the anti-hallucination anchor. Demand propagates by walking *forward* along
directed edges from a catalyst's seed nodes. Every edge carries `source` + `verified`;
an unverified edge is a NARRATIVE ASSUMPTION — propagation may still surface the node so
the analyst sees the hypothesis, but an unverified link can never raise confidence (see
`PropagatedNode.fully_verified`). All graph operations here are pure and network-free, so
the propagation logic is unit-testable without touching any API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from trade.config_loader import supply_chain


@dataclass
class PropagatedNode:
    node_id: str           # "<market>:<id>", e.g. "tw:2330"
    hops: int              # edges traversed from the nearest seed (0 = seed itself)
    path: list[str]        # seed -> ... -> node_id (shortest by hop count)
    relation: str          # label of the incoming edge that delivered demand here
    edge_source: str       # citable origin of that incoming edge ("" if none)
    edge_verified: bool    # verified flag of the incoming edge
    path_verified: bool    # True only if EVERY edge along `path` is verified

    @property
    def market(self) -> str:
        return self.node_id.split(":", 1)[0]

    @property
    def ticker(self) -> str:
        return self.node_id.split(":", 1)[1]

    @property
    def fully_verified(self) -> bool:
        """A seed (hops==0) needs no incoming edge; otherwise the whole path must be
        verified for this node's propagation to count toward confidence."""
        return self.hops == 0 or self.path_verified


@dataclass
class Catalyst:
    id: str
    name: str
    description: str
    seeds: list[str]
    drivers: list[str] = field(default_factory=list)
    source: str = ""


def build_graph(cfg: dict | None = None) -> nx.DiGraph:
    """Build a DiGraph from the supply-chain config. Edge attrs: relation, source,
    verified. Unknown/empty edges are skipped defensively."""
    cfg = cfg if cfg is not None else supply_chain()
    g = nx.DiGraph()
    for e in cfg.get("edges", []):
        src, dst = e.get("from"), e.get("to")
        if not src or not dst:
            continue
        g.add_edge(
            src, dst,
            relation=e.get("relation", ""),
            source=e.get("source", "") or "",
            verified=bool(e.get("verified", False)),
        )
    return g


def catalysts(cfg: dict | None = None) -> list[Catalyst]:
    cfg = cfg if cfg is not None else supply_chain()
    out = []
    for c in cfg.get("catalysts", []):
        out.append(Catalyst(
            id=c["id"], name=c.get("name", c["id"]),
            description=(c.get("description", "") or "").strip(),
            seeds=list(c.get("seeds", [])), drivers=list(c.get("drivers", [])),
            source=c.get("source", ""),
        ))
    return out


def get_catalyst(catalyst_id: str, cfg: dict | None = None) -> Catalyst | None:
    for c in catalysts(cfg):
        if c.id == catalyst_id:
            return c
    return None


def propagate(g: nx.DiGraph, seeds: list[str], max_hops: int = 4) -> list[PropagatedNode]:
    """BFS forward from seeds, returning every reachable node with its shortest-hop path
    and the verified status of both its incoming edge and its whole path.

    A node reachable by several paths is reported once, at its smallest hop count. Among
    equal-hop paths we prefer a fully-verified one, so a node isn't flagged as a narrative
    assumption when a verified route to it exists.
    """
    present_seeds = [s for s in seeds if s in g]
    best: dict[str, PropagatedNode] = {}

    for seed in present_seeds:
        if seed not in best:
            best[seed] = PropagatedNode(seed, 0, [seed], "", "", True, True)

    # Frontier of (node, path, path_verified). Plain BFS by hops.
    frontier = [(s, [s], True) for s in present_seeds]
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        nxt = []
        for node, path, pv in frontier:
            for succ in g.successors(node):
                if succ in path:  # avoid cycles
                    continue
                attrs = g.edges[node, succ]
                edge_v = bool(attrs.get("verified", False))
                new_path = path + [succ]
                new_pv = pv and edge_v
                cand = PropagatedNode(
                    succ, hop, new_path, attrs.get("relation", ""),
                    attrs.get("source", ""), edge_v, new_pv,
                )
                cur = best.get(succ)
                # Keep the strictly-closer node; at equal hops prefer a verified path.
                if cur is None or hop < cur.hops or (hop == cur.hops and new_pv and not cur.path_verified):
                    best[succ] = cand
                nxt.append((succ, new_path, new_pv))
        frontier = nxt

    return sorted(best.values(), key=lambda n: (n.hops, n.node_id))


def propose_edge(from_id: str, to_id: str, relation: str, source: str = "") -> dict:
    """Construct a candidate edge. Edges proposed programmatically (e.g. by the Claude
    analyst from a filing) ALWAYS enter unverified — a human must attach a source and flip
    `verified` in supply_chain.yml before it can influence confidence."""
    return {"from": from_id, "to": to_id, "relation": relation,
            "source": source, "verified": False}
