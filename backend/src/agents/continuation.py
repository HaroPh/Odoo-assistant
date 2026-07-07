# backend/src/agents/continuation.py
"""Write-continuation: after any write, offer the next linear step from
NEXT_STEPS as a menu (interrupt kind="next_action"). "Proceed" loads the next
action into pending_action/confirmed=True and the graph loops back to the
executor — the menu IS the confirmation for that next action (no double
confirm). Cross-cutting node, not a per-flow coordinator; deterministic (no
LLM). Sole consumer of last_write: returns last_write=None on EVERY branch,
so a stale handle can never re-offer an old record's next step. Khi auto_chain
còn bước khớp NEXT_STEPS, bước kế tự chạy không interrupt — confirm đầu chuỗi
đã cover."""

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .create_order import _ttl_expiry
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

        question = (f"{lw['display']}\n\nTiếp theo bạn có muốn:\n"
                    f"• {step.label}\n• Dừng")
        proceed = _interrupt({"kind": "next_action", "question": question,
                              "options": [{"id": True, "name": step.label},
                                          {"id": False, "name": "Dừng"}],
                              "expires_at": _ttl_expiry()})
        if not proceed:
            return {"pending_action": None, "confirmed": None, "last_write": None,
                    "auto_chain": None,
                    "messages": [AIMessage(content="Đã dừng tại đây.")]}
        return {"pending_action": {"tool": step.tool, "args": step.args(lw),
                                   "summary": step.label},
                "confirmed": True, "last_write": None, "auto_chain": None}

    return write_continuation


def _route_after_continuation(state: ERPAgentState) -> str:
    if state.get("pending_action") and state.get("confirmed"):
        return "erp_write_executor"
    return END
