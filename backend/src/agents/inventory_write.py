# backend/src/agents/inventory_write.py
"""Deterministic inventory-adjustment coordinator: resolve the product (+ optional
location by name, passed through to the MCP tool), show the target as a confirm
draft, then set the on-hand quantity to that absolute value. Re-entrant, no LLM."""

import os

from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import _tool_result_text
from .create_order import (resolve_entity_for_order, _by_id, _ttl_expiry, _msg,
                           _disambig_q, WRITE_DISABLED_MSG)
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
        if os.environ.get("WRITE_ACTIONS_ENABLED", "false").lower() != "true":
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
