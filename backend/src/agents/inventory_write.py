# backend/src/agents/inventory_write.py
"""Deterministic inventory-adjustment coordinator: resolve the product (+ optional
location by name, passed through to the MCP tool), show the target as a confirm
draft, then set the on-hand quantity to that absolute value. Re-entrant, no LLM."""

from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import _tool_result_text
from .create_order import (resolve_entity_for_order, _by_id, _ttl_expiry, _msg,
                           _disambig_q, WRITE_DISABLED_MSG)
from . import write_gate
from ..erp_query import inventory


def _current_on_hand(product_ref: str):
    """Best-effort single-quant on-hand for the draft; None if unavailable."""
    try:
        env = inventory.get_stock(product_ref)
        if env.get("status") == "success":
            rows = (env.get("data") or {}).get("rows") or []
            if len(rows) == 1:
                return rows[0].get("available_quantity")
    except Exception:  # noqa: BLE001 — informational only, never blocks
        pass
    return None


def make_inventory_node(tools):
    by_name = {t.name: t for t in tools}

    async def inventory_adjust(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        product_ref = args.get("product_name") or ""
        new_qty = args.get("new_qty")
        location_name = args.get("location_name") or None
        if new_qty is None:
            return _msg("Vui lòng cho biết số lượng tồn kho mới.")

        kind, val = resolve_entity_for_order(inventory.find_product(product_ref), product_ref)
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
                return _msg("Đã hủy điều chỉnh tồn kho.")
        else:
            product = val

        current = _current_on_hand(product_ref)
        loc_txt = f" tại {location_name}" if location_name else ""
        cur_txt = f" (hiện tại: {current:g})" if current is not None else ""
        draft = (f"Điều chỉnh tồn kho {product['name']}{loc_txt}{cur_txt} "
                 f"về {new_qty:g}.\nXác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy điều chỉnh tồn kho.")

        tool = by_name.get("inventory_adjustment")
        if tool is None:
            return _msg("Công cụ điều chỉnh tồn kho không khả dụng.")
        try:
            result = await tool.ainvoke({"product_id": product["id"], "new_qty": new_qty,
                                         "location_name": location_name})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi điều chỉnh tồn kho: {e}")
        return _msg(_tool_result_text(result))

    return inventory_adjust


def make_internal_transfer_node(tools):
    by_name = {t.name: t for t in tools}

    async def internal_transfer(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        product_ref = args.get("product_name") or ""
        qty = args.get("qty")
        from_location = args.get("from_location") or ""
        to_location = args.get("to_location") or ""
        if not from_location or not to_location:
            return _msg("Bạn cần cho biết cả vị trí nguồn và vị trí đích để chuyển kho.")
        if not qty:
            return _msg("Bạn cần cho biết số lượng cần chuyển.")

        kind, val = resolve_entity_for_order(inventory.find_product(product_ref), product_ref)
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
                return _msg("Đã hủy chuyển kho.")
        else:
            product = val

        draft = (f"Chuyển {qty:g} {product['name']} từ {from_location} sang "
                 f"{to_location}.\nXác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy chuyển kho.")

        tool = by_name.get("internal_transfer")
        if tool is None:
            return _msg("Công cụ chuyển kho không khả dụng.")
        try:
            result = await tool.ainvoke({"product_id": product["id"], "qty": qty,
                                         "from_location": from_location,
                                         "to_location": to_location})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi chuyển kho: {e}")
        return _msg(_tool_result_text(result))

    return internal_transfer


def make_scrap_product_node(tools):
    by_name = {t.name: t for t in tools}

    async def scrap_product(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        product_ref = args.get("product_name") or ""
        qty = args.get("qty")
        location_name = args.get("location_name") or None
        reason = args.get("reason") or None
        if not qty:
            return _msg("Bạn cần cho biết số lượng phế liệu.")

        kind, val = resolve_entity_for_order(inventory.find_product(product_ref), product_ref)
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
                return _msg("Đã hủy ghi nhận phế liệu.")
        else:
            product = val

        loc_txt = f" tại {location_name}" if location_name else ""
        reason_txt = f" (lý do: {reason})" if reason else ""
        draft = (f"Ghi nhận phế liệu {qty:g} {product['name']}{loc_txt}{reason_txt}.\n"
                 f"Xác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy ghi nhận phế liệu.")

        tool = by_name.get("scrap_product")
        if tool is None:
            return _msg("Công cụ ghi nhận phế liệu không khả dụng.")
        try:
            result = await tool.ainvoke({"product_id": product["id"], "qty": qty,
                                         "location_name": location_name, "reason": reason})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi ghi nhận phế liệu: {e}")
        return _msg(_tool_result_text(result))

    return scrap_product
