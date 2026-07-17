# backend/src/agents/agentic_context_sync.py
"""Post-skill state handover (tier-2 → tier-1): sau khi một agentic skill
kết thúc, quét ToolMessage của LƯỢT HIỆN TẠI tìm envelope write thành công
mới nhất và set working_context — để lượt sau, tầng 1 (erp_read/
erp_write_planner đọc working_context trong system prompt) hiểu "đơn đó".
Đồng thời scrub tất định câu trả lời CUỐI (message duy nhất user thấy —
xem erp_agent.chat: result["messages"][-1].content) nếu lộ tên tool MCP
thô (finding #3/#4, 2026-07-17): ưu tiên thay bằng câu "display" thật của
write vừa xảy ra (nếu có — không nói dối là "vướng mắc" khi write đã
thành công); không có write nào thì dùng TOOL_LEAK_FALLBACK_MSG chung.

KHÔNG set last_write (consumer duy nhất là write_continuation — không chạy
sau skill; auto-chain sau skill bị cấm chủ đích, spec agentic-delivery §2).

Quét dừng ở HumanMessage gần nhất: _invoke_fresh reset channel về đúng
history client gửi (chỉ user/assistant) nên ToolMessage lượt cũ không tồn
đọng; ranh giới này bảo đảm chỉ đọc kết quả tool của lượt đang xử lý.

Residual risk (chủ đích không vá): đường interrupt (ask_human/confirm đang
parked) KHÔNG chạy qua node này — graph chưa hoàn thành lượt đó nên cạnh
skill_node → agentic_context_sync chưa được đi tới. Lộ tool-name trong một
câu hỏi ask_human đang parked sẽ không bị scrub. Không vá naive được: thay
câu hỏi đang parked bằng fallback sẽ khiến user trả lời một câu hỏi họ
không thấy → phá contract resume, tệ hơn để nguyên leak."""
import json

from langchain_core.messages import AIMessage

from .tool_result import _tool_result_text
from .tool_leak_guard import has_tool_leak, TOOL_LEAK_FALLBACK_MSG
from .working_context import derive_working_context


def make_agentic_context_sync_node():
    async def agentic_context_sync(state) -> dict:
        try:
            messages = state.get("messages") or []
            wc = None
            for msg in reversed(messages):
                if getattr(msg, "type", "") == "human":
                    break
                if getattr(msg, "type", "") != "tool":
                    continue
                # ToolMessage.content từ MCP tool là list content-block dict
                # ([{"type":"text","text":"..."}]), không phải chuỗi trần —
                # _tool_result_text (đã dùng ở tầng 1, tool_result.py) chuẩn
                # hoá trước khi parse JSON.
                try:
                    env = json.loads(_tool_result_text(msg.content))
                except (TypeError, ValueError):
                    continue          # REFUSED_MSG / text thường — bỏ qua
                candidate = derive_working_context(env)
                if candidate is not None:
                    wc = candidate    # ToolMessage mới nhất có write thắng
                    break

            result: dict = {}
            if wc is not None:
                result["working_context"] = wc

            last = messages[-1] if messages else None
            if last is not None and getattr(last, "type", "") == "ai":
                if has_tool_leak(last.content or ""):
                    fallback = (wc["display"] if wc is not None and wc.get("display")
                               else TOOL_LEAK_FALLBACK_MSG)
                    result["messages"] = [AIMessage(id=last.id, content=fallback)]

            return result
        except Exception:              # total — không bao giờ phá một flow đã xong
            return {}

    return agentic_context_sync
