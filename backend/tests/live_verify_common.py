# backend/tests/live_verify_common.py
"""Helper dùng chung cho 3 script live-verify skill agentic (warehouse/delivery/
discount). Tái dùng erp_query.transport.XmlRpcTransport thay vì hand-roll
xmlrpc.client riêng — tránh lặp lại cách làm thủ công của các script scratchpad
live-verify trước đây. Xem docs/superpowers/specs/2026-07-17-agentic-skill-eval-jobs-design.md."""
import json
import os
import sys
from dataclasses import dataclass, field

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.src.erp_query.transport import XmlRpcTransport

BASE_URL = "http://localhost:8000"
CHAT_ENDPOINT = f"{BASE_URL}/v1/chat/completions"


def load_env(env_path: str | None = None) -> None:
    """Đọc .env, setdefault vào os.environ (idempotent — an toàn gọi nhiều lần,
    không ghi đè biến đã set sẵn trong môi trường gọi). Test script tự đọc .env
    thay vì bắt caller export tay."""
    path = env_path or os.path.join(os.path.dirname(__file__), "../../.env")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def odoo_transport() -> XmlRpcTransport:
    load_env()
    return XmlRpcTransport(os.environ["ODOO_URL"], os.environ["ODOO_DB"],
                           os.environ["ODOO_USERNAME"], os.environ["ODOO_PASSWORD"])


def chat(history: list[dict], sid: str, msg: str) -> str:
    history.append({"role": "user", "content": msg})
    body = {"model": "erp-assistant", "session_id": sid,
           "messages": history, "stream": False}
    r = requests.post(CHAT_ENDPOINT, json=body, timeout=150)
    r.raise_for_status()
    answer = r.json()["choices"][0]["message"]["content"]
    history.append({"role": "assistant", "content": answer})
    return answer


# Cùng tinh thần HALLUCINATION_MARKERS (backend/evals/cases.py) — nhưng bắt việc
# lộ tên tool thô ra user thay vì bịa hành động.
TOOL_NAME_LEAK_MARKERS = (
    "receive_order", "flag_order_for_review", "deliver_order",
    "create_discount_quote", "get_purchase_order_detail",
    "get_sale_order_detail", "ask_human(",
)


def has_tool_leak(text: str) -> list[str]:
    low = text.lower()
    return [m for m in TOOL_NAME_LEAK_MARKERS if m.lower() in low]


@dataclass
class DriveResult:
    final_answer: str
    turns: int
    completed: bool
    all_answers: list[str] = field(default_factory=list)


def _looks_like_confirm_gate(low: str, confirm_markers: tuple[str, ...]) -> bool:
    # Task 2 live-run (e2e-skill-discount, 2026-07-17) found a false positive:
    # a product-disambiguation clarification ("...Vui lòng xác nhận chính xác
    # tên sản phẩm...") contains "xác nhận" as an ordinary verb, mid-sentence,
    # with no "?" anywhere — NOT the real money-confirm gate. Every real
    # confirm-gate question in this codebase (agentic_gate._confirm_write's
    # callers, create_order.render_draft) ends in "?" (either "...cho đơn mua
    # P00021?" or "...Xác nhận? (có / không)"); ordinary clarification prose
    # in this codebase does not. Requiring "?" alongside the marker closes
    # this specific, real, evidence-based gap without needing an LLM-judge.
    return any(marker in low for marker in confirm_markers) and "?" in low


def drive_conversation(history: list[dict], sid: str, opening_msg: str,
                       responders: list[tuple], final_answer: str,
                       confirm_markers: tuple[str, ...] = ("xác nhận",),
                       max_turns: int = 8) -> DriveResult:
    """Lái hội thoại đa lượt chịu được model trôi tham số (Đợt 3 tier2-retirement
    live-verify). responders: [(predicate: str->bool, reply: str), ...] xét theo
    thứ tự trên câu trả lời agent (lowercase) tới khi khớp; reply được gửi lại.
    Khi câu trả lời chứa 1 trong confirm_markers VÀ có "?" (xem
    _looks_like_confirm_gate) → gửi final_answer, DỪNG (dùng HÀM NÀY CHO CẢ
    happy-path lẫn refusal — chỉ khác final_answer truyền vào). Không câu nào
    khớp → dừng, completed=False. all_answers tích luỹ MỌI câu trả lời agent
    trong phiên (kể cả khi completed=False)."""
    ans = chat(history, sid, opening_msg)
    all_answers = [ans]
    turns = 1
    while turns < max_turns:
        low = ans.lower()
        if _looks_like_confirm_gate(low, confirm_markers):
            ans = chat(history, sid, final_answer)
            all_answers.append(ans)
            return DriveResult(ans, turns + 1, True, all_answers)
        for predicate, reply in responders:
            if predicate(low):
                ans = chat(history, sid, reply)
                all_answers.append(ans)
                turns += 1
                break
        else:
            return DriveResult(ans, turns, False, all_answers)
    return DriveResult(ans, turns, False, all_answers)


def drive_fixed_turns(history: list[dict], sid: str, opening_msg: str,
                      followups: list[str]) -> list[str]:
    """Lái hội thoại theo kịch bản CỐ ĐỊNH (không tìm confirm-gate) — dùng cho
    kịch bản không kỳ vọng chạm write thật. Trả list MỌI câu trả lời agent, theo
    đúng thứ tự."""
    answers = [chat(history, sid, opening_msg)]
    for msg in followups:
        answers.append(chat(history, sid, msg))
    return answers


@dataclass
class Scenario:
    name: str
    passed: bool
    turns: int
    detail: str


def print_result(job: str, scenarios: list[Scenario]) -> bool:
    """In JSON kết quả có cấu trúc (job wrapper parse) + summary người đọc.
    Trả True nếu tất cả pass."""
    n, passed = len(scenarios), sum(1 for s in scenarios if s.passed)
    result = {"job": job, "n": n, "passed": passed,
             "scenarios": [{"name": s.name, "passed": s.passed,
                            "turns": s.turns, "detail": s.detail}
                           for s in scenarios]}
    print("\n=== RESULT_JSON ===")
    print(json.dumps(result, ensure_ascii=False))
    print("=== END_RESULT_JSON ===\n")
    for s in scenarios:
        status = "PASS" if s.passed else "FAIL"
        print(f"[{status}] {s.name} (turns={s.turns}): {s.detail}")
    print(f"\n{passed}/{n} scenarios passed")
    return passed == n
