"""Central paths and environment configuration.

Secrets come from environment variables (local .env, or GitHub Secrets in CI).
Nothing here ever fabricates a value: a missing credential surfaces as None so the
caller can fail loudly rather than silently guessing.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional at runtime; CI injects real env vars
    pass

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
CACHE_DIR = Path(os.getenv("TRADE_CACHE_DIR", ROOT / "data_cache"))
REPORTS_DIR = ROOT / "reports"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


FINMIND_TOKEN = _env("FINMIND_TOKEN")
SEC_USER_AGENT = _env("SEC_USER_AGENT")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANALYST_MODEL = _env("TRADE_ANALYST_MODEL") or "claude-opus-4-8"

DISCORD_WEBHOOK = _env("DISCORD_WEBHOOK")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
