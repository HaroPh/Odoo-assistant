# backend/src/agents/returns_write.py
"""Deterministic Sales-Returns coordinators (tier-1): return_order (RMA) +
create_credit_memo. Resolve đơn/hóa đơn + sản phẩm bằng slot-filling +
disambig interrupt (pattern create_order.py/bom_write.py). CÓ NEXT_STEPS
(khác BoM's master-data writes) — return_order → validate_picking (tool
có sẵn, phiếu trả hàng đã 'assigned' ngay sau khi tạo — probe 2026-07-22);
create_credit_memo → post_invoice (tool round 1, đã mở rộng domain chấp
nhận refund). Xem docs/superpowers/specs/2026-07-22-rma-credit-memo-design.md."""
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import parse_write_result
from .create_order import (resolve_entity_for_order, _by_id, _ttl_expiry, _msg,
                           _disambig_q, WRITE_DISABLED_MSG)
from . import write_gate
from ..erp_query import inventory, accounting


def _finish(tool_name: str, result) -> dict:
    display, env = parse_write_result(result)
    return {**_msg(display), "pending_action": None,
            "last_write": {"tool": tool_name, **env} if env else None}


def _resolve_product(ref: str):
    """Resolve 1 tên sản phẩm → ('ok', {'id','name'}) | ('msg', <dict>).
    Lặp pattern bom_write.py's _resolve_one có chủ đích (round 1's tiền lệ
    không import chéo bounded context — xem accounting.py's
    _resolve_product comment)."""
    kind, val = resolve_entity_for_order(inventory.find_product(ref), ref)
    if kind == "error":
        return "msg", _msg(val)
    if kind == "none":
        return "msg", _msg(f"Không tìm thấy sản phẩm '{ref}'.")
    if kind == "ambiguous":
        chosen = _interrupt({"kind": "disambiguation",
                             "question": _disambig_q(f"sản phẩm '{ref}'", val),
                             "options": val, "expires_at": _ttl_expiry()})
        picked = _by_id(val, chosen)
        if picked is None:
            return "msg", _msg("Đã hủy.")
        return "ok", picked
    return "ok", val


def make_return_order_node(tools):
    by_name = {t.name: t for t in tools}

    async def return_order_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)
        args = (state.get("pending_action") or {}).get("args") or {}
        order_ref = str(args.get("order_ref") or "").strip()
        raw_lines = args.get("lines") or []
        if not order_ref:
            return _msg("Bạn cần cho biết mã đơn bán cần trả hàng.")

        denv = inventory.find_done_deliveries_for_order(order_ref)
        if denv.get("status") != "success":
            return _msg(denv.get("display") or "Lỗi tra cứu đơn bán.")
        data = denv.get("data") or {}
        pickings = data.get("pickings") or []
        if not pickings:
            return _msg(f"Đơn {order_ref} chưa có phiếu giao nào hoàn tất "
                        f"— không có gì để trả.")
        if len(pickings) == 1:
            picking = pickings[0]
        else:
            options = [{"id": p["id"],
                       "name": f"{p['name']} ({(p.get('date_done') or '?')[:10]})"}
                      for p in pickings]
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q("phiếu giao", options),
                                 "options": options, "expires_at": _ttl_expiry()})
            picking = _by_id(pickings, chosen)
            if picking is None:
                return _msg("Đã hủy trả hàng.")

        lines, lines_txt = [], []
        for l in raw_lines:
            ref = str(l.get("product") or "").strip()
            try:
                q = float(l.get("qty") or 0)
            except (TypeError, ValueError):
                q = 0.0
            if not ref or q <= 0:
                return _msg("Mỗi sản phẩm cần trả cần tên và số lượng lớn hơn 0.")
            kind, product = _resolve_product(ref)
            if kind == "msg":
                return product
            lines.append({"product_id": product["id"], "qty": q})
            lines_txt.append(f"  - {product['name']} × {q:g}")

        body = "\n".join(lines_txt) if lines_txt else "  (toàn bộ số lượng đã giao)"
        draft = (f"Trả hàng cho đơn {order_ref} (phiếu {picking['name']}):\n"
                 f"{body}\nXác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy trả hàng.")

        tool = by_name.get("return_order")
        if tool is None:
            return _msg("Công cụ trả hàng không khả dụng.")
        try:
            result = await tool.ainvoke({"picking_id": picking["id"], "lines": lines})
        except Exception as e:  # noqa: BLE001
            return _msg(f"Lỗi khi trả hàng: {e}")
        return _finish("return_order", result)

    return return_order_node


def make_create_credit_memo_node(tools):
    by_name = {t.name: t for t in tools}

    async def create_credit_memo_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)
        args = (state.get("pending_action") or {}).get("args") or {}
        invoice_ref = str(args.get("invoice_ref") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not invoice_ref:
            return _msg("Bạn cần cho biết số hóa đơn cần tạo credit memo.")

        ienv = accounting.find_posted_invoice(invoice_ref)
        if ienv.get("status") != "success":
            return _msg(ienv.get("display") or "Lỗi tra cứu hóa đơn.")
        inv = (ienv.get("data") or {}).get("invoice") or {}

        partner = inv["partner_id"][1] if inv.get("partner_id") else "?"
        reason_txt = f" (lý do: {reason})" if reason else ""
        draft = (f"Tạo credit memo cho hóa đơn {invoice_ref} của {partner}: "
                f"{inv.get('amount_total', 0):,.0f}{reason_txt}.\n"
                f"Xác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy tạo credit memo.")

        tool = by_name.get("create_credit_memo")
        if tool is None:
            return _msg("Công cụ tạo credit memo không khả dụng.")
        try:
            result = await tool.ainvoke({"invoice_id": inv["id"], "reason": reason})
        except Exception as e:  # noqa: BLE001
            return _msg(f"Lỗi khi tạo credit memo: {e}")
        return _finish("create_credit_memo", result)

    return create_credit_memo_node
