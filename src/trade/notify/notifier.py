"""Push a short report summary to Discord and/or Telegram.

Both channels are optional and best-effort: a missing webhook/token is simply skipped, and
a failed send is reported but never crashes the weekly run (the full report is always
committed back to the repo regardless). Only a concise summary is pushed — the full
Markdown lives in reports/.
"""

from __future__ import annotations

import requests

from trade.settings import DISCORD_WEBHOOK, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_DISCORD_LIMIT = 2000
_TELEGRAM_LIMIT = 4096


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send_discord(summary: str) -> bool:
    if not DISCORD_WEBHOOK:
        return False
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": _clip(summary, _DISCORD_LIMIT)}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as exc:
        print(f"[notify] Discord failed: {exc}")
        return False


def send_telegram(summary: str) -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": _clip(summary, _TELEGRAM_LIMIT),
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=20)
        r.raise_for_status()
        return True
    except Exception as exc:
        print(f"[notify] Telegram failed: {exc}")
        return False


def notify(summary: str) -> list[str]:
    """Send to every configured channel; return the names that succeeded."""
    sent = []
    if send_discord(summary):
        sent.append("discord")
    if send_telegram(summary):
        sent.append("telegram")
    return sent


def summarise(result) -> str:
    """Build a compact push summary from a PipelineResult."""
    lines = [
        f"📈 投資研究週報 {result.period}",
        f"催化劑：{result.catalyst.name}",
        f"推薦 {len(result.recommendations)} 檔｜觀察 {len(result.watchlist)} 檔",
        "",
    ]
    for r in result.recommendations[:5]:
        p = r.packet
        flag = " ⚠敘事假設" if p.narrative_assumption else ""
        lines.append(
            f"• {p.name} ({p.market.upper()}:{p.ticker}) "
            f"信心 {p.confidence:.0f} ｜基本面 {p.fundamentals_score:.0f}｜傳導 {p.propagation_score:.0f}{flag}"
        )
    if not result.recommendations:
        lines.append("（本期無標的同時通過兩引擎）")
    lines.append("\n完整報告已存入 repo reports/。")
    return "\n".join(lines)
