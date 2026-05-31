"""Load and lightly validate the YAML configs."""

from __future__ import annotations

from functools import lru_cache

import yaml

from trade.settings import CONFIG_DIR


@lru_cache(maxsize=None)
def _load(name: str) -> dict | list:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def universe(market: str) -> list[dict]:
    market = market.lower()
    if market not in ("tw", "us"):
        raise ValueError(f"Unknown market {market!r}")
    return _load(f"universe_{market}.yml")


def screening_rules() -> dict:
    return _load("screening_rules.yml")


def supply_chain() -> dict:
    return _load("supply_chain.yml")


def node_id(market: str, ticker: str) -> str:
    return f"{market.lower()}:{ticker}"
