# backend/src/agents/mrp_write.py
"""Deterministic manufacturing coordinator (tier-1): create_manufacturing_order.
Slot-filling qua _msg (KHÔNG interrupt); resolve sản phẩm + chọn BoM (lọc
type='normal' — kit không sản xuất được; bẫy template-vs-variant nằm trong
erp_query.mrp) + preview khả dụng nguyên liệu; disambiguation + confirm qua
interrupt; rồi gọi MCP tool phẳng. confirm/complete_manufacturing_order là tool
PHẲNG (planner tự confirm-gate, nodes.py Invariant-C) — không cần coordinator.
Không LLM. Xem docs/superpowers/specs/2026-07-19-manufacturing-mo-design.md."""
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import parse_write_result
from .create_order import (resolve_entity_for_order, _by_id, _ttl_expiry, _msg,
                           _disambig_q, WRITE_DISABLED_MSG)
from . import write_gate
from ..erp_query import inventory, mrp


def _finish(tool_name: str, result) -> dict:
    """mrp.production ∉ ORDER_MODELS → derive_working_context trả None — không
    set working_context (chủ đích, nhất quán crm_write/post_invoice)."""
    display, env = parse_write_result(result)
    return {**_msg(display), "pending_action": None,
            "last_write": {"tool": tool_name, **env} if env else None}


def _bom_label(b: dict) -> str:
    return b.get("code") or f"BoM #{b['id']}"


def make_create_mo_node(tools):
    by_name = {t.name: t for t in tools}

    async def create_mo_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)
        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        product_ref = str(args.get("product_name") or "").strip()
        try:
            qty = float(args.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        bom_code = str(args.get("bom_code") or "").strip()

        # Slot-fill GỘP: mọi slot thiếu trong MỘT câu (pattern crm_write).
        missing = []
        if not product_ref:
            missing.append("sản phẩm cần sản xuất")
        if qty <= 0:
            missing.append("số lượng")
        if missing:
            return _msg("Vui lòng cho biết: " + "; ".join(missing) + ".")

        kind, val = resolve_entity_for_order(inventory.find_product(product_ref),
                                             product_ref)
        if kind == "error":
            return _msg(val)
        if kind == "none":
            return _msg(f"Không tìm thấy sản phẩm '{product_ref}'.")
        if kind == "ambiguous":
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q(f"sản phẩm '{product_ref}'", val),
                                 "options": val, "expires_at": _ttl_expiry()})
            product = _by_id(val, chosen)
            if product is None:
                return _msg("Đã hủy.")
        else:
            product = val

        benv = mrp.find_boms_for_variant(product["id"])
        if benv.get("status") != "success":
            return _msg(benv.get("display") or "Lỗi tra cứu định mức.")
        boms = (benv.get("data") or {}).get("boms") or []
        normal = [b for b in boms if b.get("type") == "normal"]
        if not normal:
            if boms:
                return _msg(f"Sản phẩm '{product['name']}' chỉ có BoM dạng Kit "
                            f"— kit không sản xuất trực tiếp được.")
            return _msg(f"Sản phẩm '{product['name']}' chưa có định mức (BoM). "
                        f"Cần tạo BoM trong Odoo trước.")
        if bom_code:
            match = [b for b in normal
                     if (b.get("code") or "").casefold() == bom_code.casefold()]
            if not match:
                codes = ", ".join(_bom_label(b) for b in normal)
                return _msg(f"Không có BoM mã '{bom_code}' cho sản phẩm này. "
                            f"BoM hiện có: {codes}.")
            bom = match[0]
        elif len(normal) == 1:
            bom = normal[0]
        else:
            options = [{"id": b["id"],
                        "name": f"{_bom_label(b)} (cho {b['product_qty']:g} đơn vị)"}
                       for b in normal]
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q("định mức (BoM)", options),
                                 "options": options, "expires_at": _ttl_expiry()})
            bom = next((b for b in normal if b["id"] == chosen), None)
            if bom is None:
                return _msg("Đã hủy.")

        # Preview nguyên liệu — read tư vấn: lỗi tra cứu KHÔNG chặn tạo nháp.
        aenv = mrp.check_bom_availability(bom["id"], qty)
        avail_lines, warn = [], ""
        if aenv.get("status") == "success":
            for r in (aenv.get("data") or {}).get("rows") or []:
                mark = "" if r["enough"] else " — THIẾU"
                avail_lines.append(f"  - {r['name']} × {r['need']:g} "
                                   f"(tồn {r['on_hand']:g}){mark}")
            if not (aenv.get("data") or {}).get("all_enough", True):
                warn = ("\n⚠ Thiếu nguyên liệu — vẫn tạo được lệnh nháp nhưng "
                        "sẽ không hoàn tất được cho tới khi nhập đủ.")
        else:
            avail_lines.append("  (không kiểm tra được tồn kho nguyên liệu)")

        note = action.get("chain_note") or ""
        draft = (f"Lệnh sản xuất: {product['name']} × {qty:g}\n"
                 f"Định mức {_bom_label(bom)}:\n" + "\n".join(avail_lines)
                 + warn + note + "\nXác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy tạo lệnh sản xuất.")

        tool = by_name.get("create_manufacturing_order")
        if tool is None:
            return _msg("Công cụ tạo lệnh sản xuất không khả dụng.")
        try:
            result = await tool.ainvoke({"product_id": product["id"],
                                         "qty": qty, "bom_id": bom["id"]})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi tạo lệnh sản xuất: {e}")
        return _finish("create_manufacturing_order", result)

    return create_mo_node
