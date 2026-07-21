# backend/src/agents/continuation.py
"""Write-continuation: after any write, append a non-blocking, deterministic
suggestion for the next linear step from NEXT_STEPS to the reply — never an
interrupt. If the user follows up on it in a later message, that message
goes through the normal write-planner confirm gate like any other request
(no shortcut). Cross-cutting node, not a per-flow coordinator; deterministic
(no LLM). Sole consumer of last_write: returns last_write=None on EVERY
branch, so a stale handle can never re-offer an old record's next step. Khi
auto_chain còn bước khớp NEXT_STEPS, bước kế tự chạy không interrupt — user
đã tự khai báo cả chuỗi trong 1 câu, mức đồng ý mạnh hơn 1 gợi ý bình
thường."""

from langchain_core.messages import AIMessage
from langgraph.graph import END

from .state import ERPAgentState
from .write_registry import NEXT_STEPS


def make_write_continuation_node():
    async def write_continuation(state: ERPAgentState) -> dict:
        lw = state.get("last_write")
        queue = state.get("auto_chain") or []
        step = NEXT_STEPS.get((lw or {}).get("tool"))
        if not lw or not lw.get("ok") or step is None:
            # terminal / failed write / non-chain tool → end, no extra message
            # (the executor's display is already the final answer).
            upd = {"pending_action": None, "confirmed": None, "last_write": None,
                   "auto_chain": None}
            if queue and lw:
                # một write ĐÃ chạy (lỗi, hoặc rẽ khỏi chuỗi như nhánh flag) mà
                # chuỗi khai báo còn bước → báo tất định. Cancel (lw falsy):
                # im lặng — "Đã hủy." của coordinator đã đủ.
                upd["messages"] = [AIMessage(
                    content=f"{lw['display']}\n\n⚠️ Chuỗi tự động dừng: bước tiếp theo không chạy.")]
            return upd

        if queue:
            if queue[0] == step.tool:
                # Bước kế đã được user duyệt trước ở confirm đầu chuỗi
                # (chain_note) → tự chạy, KHÔNG interrupt.
                return {"pending_action": {"tool": step.tool, "args": step.args(lw),
                                           "summary": step.label},
                        "confirmed": True, "last_write": None,
                        "auto_chain": queue[1:] or None}

        suggestion = f"{lw['display']}\n\n(Bạn có thể yêu cầu \"{step.label}\" bất cứ lúc nào.)"
        return {"pending_action": None, "confirmed": None, "last_write": None,
                "auto_chain": None, "messages": [AIMessage(content=suggestion)]}

    return write_continuation


def _route_after_continuation(state: ERPAgentState) -> str:
    if state.get("pending_action") and state.get("confirmed"):
        return "erp_write_executor"
    return END
