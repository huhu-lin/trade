"""The constrained analyst.

Claude sees ONLY the evidence packet (serialised JSON) and must explain the causal demand
story node-by-node, citing packet field names and their sources. The system prompt forbids
it from inventing numbers, using anything from its training memory, or touching the
confidence score (that's computed deterministically upstream). If a fact is missing from
the packet, the analyst must say so — never estimate. With no API key configured the layer
degrades to `available=False` so the rest of the pipeline (and the report) still runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from trade.analysis.data_packet import CandidatePacket
from trade.settings import ANALYST_MODEL, ANTHROPIC_API_KEY

_SYSTEM_PROMPT = """\
你是一位中長期價值投資的供需傳導分析師。你只會收到一個 JSON「證據封包」，裡面是某檔
候選股的兩引擎結果：量化基本面（metrics，每項含 inputs 出處）與需求傳導證據
（demand_evidence、chain_links、chain_path）。

絕對規則（違反即視為錯誤輸出）：
1. 只能根據封包內的欄位論述。封包沒有的數字，你一律不得寫出，也不得從記憶中補上。
2. 引用數據時用「白話中文名稱＋數值（期間, 出處）」，例如
   「營收年增 +65.47%（FY2025→FY2026，來源：edgar:revenue）」。
   **不要**直接貼英文欄位名（如 revenue_yoy、chain_links、completeness_factor、
   sub_score、narrative_assumption）；請改用對應中文（營收年增率、傳導鏈、資料完整度、
   單項子分數、敘事假設）。出處字串（如 edgar:revenue）可原樣保留，那是來源標籤。
3. 沿傳導鏈逐節說明「需求如何一層層傳到這檔股票」，每一節點都要講出那段供應鏈關係
   是什麼、出處是誰。
4. 若該檔被標為敘事假設，或某段供應鏈關係未經查證，你必須明確說「此環節尚未有出處
   證實，只是合理推測」，且不得宣稱它已被證明。
5. 任何缺漏的數據（值為 null、或列在缺漏清單）要明說「資料缺漏」，並簡短說明原因
   （例如美股沒有月營收），絕不估算或自己編一個數字。
6. 信心分數由系統公式計算，你只能「解釋」它怎麼來的，不得更改、不得自行給別的數字。

寫作風格：對象是看得懂中文、但不熟財務術語的一般投資人。每個專有名詞第一次出現時，
用一句話白話帶過它代表什麼；句子簡短、口語，不要堆砌英文縮寫。

輸出格式（繁體中文）：
## 結論：<一句話：值得進一步研究 / 觀察 / 跳過，並點出最關鍵理由>
## 需求傳導路徑
<沿 chain_path 逐節敘述因果，引用每條 link 的 relation+source；標示敘事假設>
## 基本面佐證
<引用 1-3 個最關鍵 metric，含數值與 inputs 出處；點名缺漏項>
## 風險與資料缺口
<列出 data gaps、敘事假設、估值偏貴等；誠實說明不確定性>
"""

_USER_TEMPLATE = """\
這是候選股的證據封包（JSON）。請依系統規則分析：

{packet_json}

請記住：只用封包欄位、逐節引用出處、缺資料要明說、信心分數不得更改。"""


@dataclass
class AnalystVerdict:
    narrative: str
    model: str
    available: bool
    note: str = ""


def _unavailable(reason: str) -> AnalystVerdict:
    return AnalystVerdict(
        narrative="(分析未產生：Claude 分析層未啟用。)",
        model=ANALYST_MODEL, available=False, note=reason,
    )


def analyse(packet: CandidatePacket, max_tokens: int = 1600) -> AnalystVerdict:
    if not ANTHROPIC_API_KEY:
        return _unavailable("ANTHROPIC_API_KEY 未設定")
    try:
        import anthropic
    except ImportError:
        return _unavailable("anthropic SDK 未安裝")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=ANALYST_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _USER_TEMPLATE.format(packet_json=packet.model_dump_json(indent=2)),
            }],
        )
    except Exception as exc:  # network/auth/rate — never crash the weekly run
        return _unavailable(f"Claude 呼叫失敗：{exc}")

    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    return AnalystVerdict(narrative=text.strip(), model=ANALYST_MODEL, available=True)
