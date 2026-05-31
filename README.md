# trade — 中長期價值投資研究工具

由上而下、**供需傳導驅動**的中長期選股研究工具，涵蓋**台股 + 美股**。不做短線飆股，
專注於「結構性成長產業 → 沿供應鏈往下找關鍵卡位與隱形冠軍」。每個推薦與每條因果環節
都要有**真實證據與出處**，沒有來源的環節一律標記為「敘事假設」並**不得提升信心**。

## 核心理念：雙引擎交集

一檔股票要被推薦，必須同時通過兩個獨立引擎：

| 引擎 | 內容 | 特性 |
|---|---|---|
| **引擎一 · 量化基本面** | 成長 / 獲利品質 / 財務體質 / 現金流 / 估值，依 `config/screening_rules.yml` 加權成 0–100 分 | 確定性、可追溯，每個指標帶 input 出處 |
| **引擎二 · 需求傳導** | 從催化劑（如 AI capex）沿 `config/supply_chain.yml` 有向圖展開，每節點抓「需求真的在流動」的硬證據 | 鏈位置 × 證據強度，未查證的邊不計分 |

**信心分數**由公式計算（基本面 × 0.5 ＋ 傳導 × 0.5 × 資料完整度係數），Claude 分析層
只能**解釋**這個分數、引用封包欄位，**不得自行灌水或修改**。

### 反臆測設計（本工具第一要求）

- `supply_chain.yml` 每條邊帶 `source` + `verified`。未查證的邊 → 該節點標記
  `narrative_assumption`，傳導分數被 gate 成 0，**即使財報滿分也不會被推薦**。
- 證據封包（pydantic）每個數據含 `value + source + period`；缺資料明確標 `None`，**絕不填補**。
- Claude 受限分析：只能引用封包欄位、逐節說明因果並附出處、缺資料須明說、禁用記憶中的數字。

## 安裝

需要 Python 3.11+。

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 設定（本地測試）

複製 `.env.example` 為 `.env` 並填入：

```bash
SEC_USER_AGENT="你的姓名 你的email"   # SEC EDGAR 強制要求，否則拒絕呼叫
ANTHROPIC_API_KEY="sk-ant-..."        # 選用：未設時 Claude 分析層優雅略過
FINMIND_TOKEN="..."                    # 選用：提高 FinMind 速率上限
DISCORD_WEBHOOK="..."                  # 選用：報告推播
TELEGRAM_BOT_TOKEN="..."               # 選用：報告推播
TELEGRAM_CHAT_ID="..."
```

> 沒設 `ANTHROPIC_API_KEY` 與推播憑證時，pipeline 仍會完整跑完並產出確定性報告，
> 只是 Claude 分析段與推播會自動跳過。

## CLI 用法

```bash
# M1 — 抓單檔財報（煙霧測試資料層）
python -m trade.cli fetch --market us --ticker NVDA
python -m trade.cli fetch --market tw --ticker 2330

# M2 — 對某市場觀察池跑基本面評分排名，輸出 reports/screen_<market>.csv
python -m trade.cli screen --market us
python -m trade.cli screen --market tw --limit 5

# M3 — 沿催化劑需求鏈展開
python -m trade.cli chain --catalyst ai_capex --structure-only   # 純圖結構，不抓網路
python -m trade.cli chain --catalyst ai_capex                     # 含每節點需求證據
python -m trade.cli catalysts                                     # 各催化劑熱度排名

# M5 — 完整流程：篩選 → 傳導 → 分析 → 報告 → 推播
python -m trade.cli run                       # 自動挑最熱催化劑
python -m trade.cli run --catalyst ai_capex --no-notify
python -m trade.cli run --no-analyst          # 跳過 Claude 層（純確定性報告）
```

報告輸出兩種格式：純文字 `reports/<ISO年-週>.md`（例如 `reports/2026-W22.md`），以及
**互動式 HTML** `docs/<ISO年-週>.html`（同一份資料、不額外耗 API token），並自動更新
`docs/index.html` 週報索引。HTML 為自包含單檔（系統字體＋純 CSS/SVG＋原生 JS，無 CDN、
離線可開）：信心量表、雙引擎分數條、可展開個股卡片、供應鏈已查證 ✓／敘事假設 ⚠ 徽章、
可排序篩選的觀察名單。

### 透過 GitHub Pages 發佈

互動報告寫入 `docs/` 並隨每週 run 一起 commit。一次性啟用：**Settings → Pages →
Source: `Deploy from a branch` → `main` / `/docs`**。啟用後可分享網址：

```
https://<你的帳號>.github.io/trade/            # 週報索引
https://<你的帳號>.github.io/trade/2026-W22    # 單週報告
```

## 雲端排程（GitHub Actions）

`.github/workflows/screen.yml` 每週一 01:00 UTC（約台北 09:00）自動執行，亦可在 Actions
頁手動 `workflow_dispatch` 觸發。流程：抓資料 → 跑 pipeline → 報告 commit 回 repo
（同時保持 workflow 活躍，避開 60 天停用）→ 推播摘要。

**啟用前請設定 Repository Secrets**（Settings → Secrets and variables → Actions）：
`SEC_USER_AGENT`、`ANTHROPIC_API_KEY`、`FINMIND_TOKEN`（選用）、`DISCORD_WEBHOOK`
或 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`。

## 資料來源（皆免費）

- **台股**：FinMind（財報、月營收）。月營收 YoY 是最強的領先傳導訊號。
- **美股**：SEC EDGAR companyfacts XBRL（權威財報）。
- **估值**：yfinance（市值、本益比等；台股以 `.TW`/`.TWO` 後綴查詢）。

## 專案結構

```
config/        screening_rules.yml（評分門檻）· supply_chain.yml（有來源的供需圖）· universe_*.yml（觀察池）
src/trade/
  data/        finmind / edgar / yfinance client + 快取與速率限制 + loader（雙市場正規化）
  metrics/     fundamentals.py（指標+出處）· scoring.py（0–100 評分）         ← 引擎一
  industry/    supply_chain.py（圖+傳導+防呆）· demand_chain.py（證據）· catalyst_detector.py  ← 引擎二
  analysis/    data_packet.py（證據封包）· confidence.py（確定性信心）· claude_analyst.py（受限分析）
  report/      render.py + templates/（report.md.j2 · report.html.j2 · index.html.j2）
  notify/      notifier.py（Discord / Telegram）
  pipeline.py  串接全流程（雙引擎交集）
  cli.py       fetch / screen / chain / catalysts / run
tests/         loader · scoring · demand_chain · confidence · pipeline · render_html
docs/          GitHub Pages 來源：互動式 HTML 週報 + index.html
```

## 測試

```bash
pytest -q          # 全套
ruff check src/    # lint
```

重點測試：傳導鏈的 verified-gate（`test_demand_chain.py`）、確定性信心可重現性與資料缺口
懲罰（`test_confidence.py`）、雙引擎交集與「未查證節點即使財報滿分也不推薦」
（`test_pipeline.py`）。

---

*本工具產出的所有數據皆附出處；標記「敘事假設」的環節尚未經來源證實，不得視為事實。非投資建議。*
