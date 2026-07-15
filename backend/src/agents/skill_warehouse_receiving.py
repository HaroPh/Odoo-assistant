# backend/src/agents/skill_warehouse_receiving.py
"""warehouse_receiving pilot skill: SOP "Quy trình nhập kho" steps 1-4.
Non-actionable physical steps (1's count, 3's QC) are modeled as their
reported OUTCOME only, not simulated. Node is LLM-free by design — po_ref
arrives pre-extracted in pending_action, set by skills.skill_extract (spec
§4.3). See docs/superpowers/specs/2026-07-15-sop-skill-pilot-design.md §6."""

import re

from langchain_core.messages import AIMessage

from .create_order import _interrupt, _msg, _ttl_expiry, WRITE_DISABLED_MSG
from .tool_result import parse_write_result
from . import write_gate
from ..erp_query import purchase

TRIGGERS = ("quy trinh nhap kho", "nhap kho theo quy trinh")

EXTRACT_PROMPT = """Bạn là trợ lý ERP đang trích mã đơn mua từ yêu cầu thực hiện quy trình nhập kho.

Ví dụ:
- "làm quy trình nhập kho cho đơn mua P00003" → {"po_ref": "P00003"}
- "nhập kho theo quy trình cho P00007" → {"po_ref": "P00007"}

Trả lời CHỈ JSON, không giải thích, không markdown fence:
{"po_ref": "<mã đơn mua>"}"""

QC_OPTIONS = [{"id": "pass", "name": "Đạt"}, {"id": "fail", "name": "Không đạt"}]

# Groups of exactly 3 digits after . or , are a Vietnamese thousand separator
# ("1.000" = 1000); anything else after . or , is a decimal ("12,5" = 12.5).
_NUM_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+(?:[.,]\d+)?")
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


def parse_qty(reply) -> float | None:
    if not isinstance(reply, str):        # e.g. False from a TTL-expiry cancel
        return None
    m = _NUM_RE.search(reply)
    if not m:
        return None
    s = m.group(0)
    if _THOUSANDS_RE.fullmatch(s):
        return float(re.sub(r"[.,]", "", s))
    return float(s.replace(",", "."))


def make_node(tools):
    by_name = {t.name: t for t in tools}

    async def warehouse_receiving(state) -> dict:
        if not write_gate.write_actions_enabled():
            return {**_msg(WRITE_DISABLED_MSG), "pending_action": None}

        args = (state.get("pending_action") or {}).get("args") or {}
        po_ref = str(args.get("po_ref") or "").strip()
        if not po_ref:
            return {**_msg("Bạn cần cho biết mã đơn mua để mình thực hiện quy trình nhập kho."),
                    "pending_action": None}

        reply = _interrupt({"kind": "free_text",
                            "question": f"Bạn đã kiểm đếm hàng cho đơn mua {po_ref} chưa? "
                                        f"Số lượng thực nhận (tổng tất cả mặt hàng) là bao nhiêu?",
                            "expires_at": _ttl_expiry()})
        received_qty = parse_qty(reply)
        if received_qty is None:
            return {**_msg("Mình chưa hiểu số lượng thực nhận. "
                           "Vui lòng trả lời bằng một con số, ví dụ '45'."),
                    "pending_action": None}

        env = purchase.get_purchase_order_detail(po_ref)
        if env.get("status") != "success":
            return {**_msg(env.get("display") or f"Không tìm thấy đơn mua '{po_ref}'."),
                    "pending_action": None}
        po_lines = (env.get("data") or {}).get("lines") or []
        expected_qty = sum(l.get("product_qty") or 0 for l in po_lines)

        if received_qty != expected_qty:
            tool = by_name.get("flag_order_for_review")
            if tool is None:
                return {**_msg("Công cụ ghi chú đơn không khả dụng."), "pending_action": None}
            short = received_qty < expected_qty
            note = (f"{'Nhận thiếu' if short else 'Nhận thừa'} hàng: "
                    f"thực nhận {received_qty:g}, PO {expected_qty:g}.")
            try:
                result = await tool.ainvoke({"model": "purchase.order",
                                             "order_ref": po_ref, "note": note})
            except Exception as e:  # noqa: BLE001 — never crash the graph
                return {**_msg(f"Lỗi khi ghi chú đơn: {e}"), "pending_action": None}
            display, ok_env = parse_write_result(result)
            if ok_env is None:
                return {**_msg(display), "pending_action": None}
            final = (f"Đã ghi nhận thiếu hàng lên đơn mua {po_ref}, chờ phòng mua hàng xử lý."
                     if short else
                     "Đã ghi nhận nhận thừa, giữ hàng chờ xác nhận từ phòng mua hàng.")
            return {**_msg(final), "pending_action": None}

        qc = _interrupt({"kind": "disambiguation",
                         "question": f"Số lượng khớp với PO {po_ref}. "
                                     f"QC đã kiểm tra mẫu xong chưa? Kết quả?",
                         "options": QC_OPTIONS, "expires_at": _ttl_expiry()})
        if qc == "fail":
            return {**_msg(f"Đã ghi nhận QC không đạt cho đơn mua {po_ref} — "
                           f"không nhận hàng, chờ xử lý theo quy trình trả hàng."),
                    "pending_action": None}
        if qc != "pass":
            return {**_msg("Đã hủy."), "pending_action": None}

        tool = by_name.get("receive_order")
        if tool is None:
            return {**_msg("Công cụ nhận hàng không khả dụng."), "pending_action": None}
        try:
            result = await tool.ainvoke({"order_ref": po_ref})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return {**_msg(f"Lỗi khi nhận hàng: {e}"), "pending_action": None}
        display, _env = parse_write_result(result)
        return {"messages": [AIMessage(content=display)], "pending_action": None}

    return warehouse_receiving
