# backend/src/agents/agentic_context_sync.py
"""Post-skill state handover (tier-2 → tier-1): sau khi một agentic skill
kết thúc, quét ToolMessage của LƯỢT HIỆN TẠI tìm envelope write thành công
mới nhất và set working_context — để lượt sau, tầng 1 (erp_read/
erp_write_planner đọc working_context trong system prompt) hiểu "đơn đó".

KHÔNG set last_write (consumer duy nhất là write_continuation — không chạy
sau skill; auto-chain sau skill bị cấm chủ đích, spec agentic-delivery §2).

Quét dừng ở HumanMessage gần nhất: _invoke_fresh reset channel về đúng
history client gửi (chỉ user/assistant) nên ToolMessage lượt cũ không tồn
đọng; ranh giới này bảo đảm chỉ đọc kết quả tool của lượt đang xử lý."""
import json

from .tool_result import _tool_result_text
from .working_context import derive_working_context


def make_agentic_context_sync_node():
    async def agentic_context_sync(state) -> dict:
        try:
            for msg in reversed(state.get("messages") or []):
                if getattr(msg, "type", "") == "human":
                    break
                if getattr(msg, "type", "") != "tool":
                    continue
                # ToolMessage.content từ MCP tool là list content-block dict
                # ([{"type":"text","text":"..."}]), không phải chuỗi trần —
                # _tool_result_text (đã dùng ở tầng 1, tool_result.py) chuẩn
                # hoá trước khi parse JSON. Thiếu bước này: json.loads(list)
                # raise TypeError, bị nuốt bởi except bên dưới, sync KHÔNG
                # BAO GIỜ chạy được với write thật (chỉ pass với fixture
                # chuỗi test cũ) — bug thật, tìm ra ở final review Đợt 2.
                try:
                    env = json.loads(_tool_result_text(msg.content))
                except (TypeError, ValueError):
                    continue          # REFUSED_MSG / text thường — bỏ qua
                wc = derive_working_context(env)
                if wc is not None:
                    return {"working_context": wc}
            return {}
        except Exception:              # total — không bao giờ phá một flow đã xong
            return {}

    return agentic_context_sync
