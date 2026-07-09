# backend/src/agents/confirmation.py
"""Three-way confirmation classifier for the write-action HITL gate.

A user reply to "Xác nhận? (có/không)" is classified as:
    CONFIRM  — clear yes      → resume the graph with True
    CANCEL   — clear no       → resume the graph with False
    UNCLEAR  — ambiguous      → re-ask, never guess

Hybrid strategy: a zero-cost keyword fast-path handles the common short
replies; anything it can't resolve cleanly falls back to a single LLM call.
The danger is asymmetric — executing an unwanted write is hard to undo, a
cancel is recoverable — so we only CONFIRM/CANCEL on a clean one-sided signal.
"""
import logging
import re

logger = logging.getLogger(__name__)

CONFIRM = "confirm"
CANCEL = "cancel"
UNCLEAR = "unclear"

# Conservative keyword sets. These fire only when exactly one side matches;
# mixed or empty signals fall through to UNCLEAR (and then the LLM).
_CONFIRM_WORDS = {
    "có", "co", "yes", "y", "ya", "yeah", "yep", "ừ", "u", "uh", "ừm", "um",
    "ok", "oke", "okay", "okie", "đúng", "dung", "chuẩn", "chuan", "confirm",
    "đồng ý", "dong y", "xác nhận", "xac nhan", "làm đi", "lam di",
    "tiến hành", "tien hanh", "đồng ý luôn",
}
_CANCEL_WORDS = {
    "không", "khong", "ko", "no", "n", "nope", "hủy", "huy", "đừng", "dung lai",
    "thôi", "thoi", "cancel", "khoan", "khỏi", "khoi", "dừng", "dừng lại", "stop",
}

_LLM_PROMPT = (
    "Người dùng được hỏi xác nhận thực hiện một thao tác ghi dữ liệu "
    "(có/không). Phân loại câu trả lời của họ thành đúng MỘT từ:\n"
    "CONFIRM — đồng ý thực hiện ĐÚNG như đã đề xuất\n"
    "CANCEL — từ chối/hủy\n"
    "UNCLEAR — không rõ ràng, là câu hỏi, HOẶC yêu cầu THAY ĐỔI/sửa lại "
    "thao tác đã đề xuất (dù có kèm '...được chứ?'/'...nhé' nghe như xin phép "
    "— đây KHÔNG phải đồng ý với đề xuất ban đầu, cần hỏi lại trước khi làm)\n"
    "Ví dụ: \"cho mình đổi thành 5 cái được chứ?\" → UNCLEAR (yêu cầu đổi "
    "tham số, không phải xác nhận đề xuất cũ)\n"
    "Chỉ trả về MỘT từ: CONFIRM, CANCEL, hoặc UNCLEAR."
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation (keep Vietnamese letters), collapse spaces."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _match_any(padded: str, words: set[str]) -> bool:
    # padded has a leading+trailing space, so " kw " matches whole tokens/phrases
    return any(f" {w} " in padded for w in words)


def classify_keyword(text: str) -> str:
    """Fast-path classification. Returns CONFIRM/CANCEL/UNCLEAR.

    Fires CONFIRM or CANCEL only when exactly one side matches; a reply
    carrying both signals (e.g. negation "không đồng ý") or neither stays
    UNCLEAR so the caller can defer to the LLM.
    """
    padded = f" {_normalize(text)} "
    confirm = _match_any(padded, _CONFIRM_WORDS)
    cancel = _match_any(padded, _CANCEL_WORDS)
    if confirm and not cancel:
        return CONFIRM
    if cancel and not confirm:
        return CANCEL
    return UNCLEAR


async def classify_confirmation(text: str, llm) -> str:
    """Hybrid classify: keyword fast-path, LLM fallback. Returns one of the
    three constants. Any LLM output that isn't a clean CONFIRM/CANCEL is
    treated as UNCLEAR (fail-safe — never executes on ambiguity)."""
    keyword = classify_keyword(text)
    if keyword != UNCLEAR:
        return keyword

    from langchain_core.messages import SystemMessage, HumanMessage

    response = await llm.ainvoke([
        SystemMessage(content=_LLM_PROMPT),
        HumanMessage(content=text),
    ])
    verdict = response.content.strip().upper()
    if "CONFIRM" in verdict:
        return CONFIRM
    if "CANCEL" in verdict:
        return CANCEL
    return UNCLEAR
