"""Shared confirm-gate primitives for tier-2 agentic SOP skills.

The safety contract has ONE definition: a write tool is only reachable
through a same-named wrapper that parks on interrupt(kind="confirm") and
requires a literal True resume (fail-closed). Each skill keeps its own
thin typed wrappers (explicit signatures let @tool infer the schema the
model sees); what they share lives here.

_ttl_expiry is deliberately imported from create_order: tier 1 and tier 2
share one confirm contract (same interrupt shape, same _decide_resume
classifier, same TTL) — the user cannot tell which tier is asking."""

from langchain_core.tools import tool
from langgraph.types import interrupt as _interrupt

from .create_order import _ttl_expiry

REFUSED_MSG = ("Người dùng TỪ CHỐI xác nhận — KHÔNG thực hiện thao tác. "
               "Hãy hỏi người dùng muốn làm gì tiếp.")


@tool
def ask_human(question: str) -> str:
    """Hỏi người dùng một câu hỏi mở và chờ câu trả lời. Dùng khi cần thông
    tin chỉ con người mới biết được (số liệu đã kiểm đếm, kết quả kiểm tra
    chất lượng, mã đơn còn thiếu...). KHÔNG được tự suy đoán thay cho việc
    hỏi."""
    return _interrupt({"kind": "free_text", "question": question})


def _confirm_write(question: str) -> bool:
    """Cổng xác nhận cứng tại ranh giới tool ghi — model không bao giờ thấy
    tool ghi thô nên không có đường vòng nào bỏ qua cổng này. kind="confirm"
    đi qua erp_agent._decide_resume (phân loại có/không → resume bool); để
    quá TTL → resume False. Chỉ True tuyệt đối mới cho ghi."""
    answer = _interrupt({"kind": "confirm", "question": question,
                         "expires_at": _ttl_expiry()})
    return answer is True
