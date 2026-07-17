#!/usr/bin/env python3
# coding: utf-8
"""E2E live-verify: delivery skill. 3 kịch bản, mỗi kịch bản (trừ draft_order_
refused) tự tạo + xác nhận sale.order mới qua XML-RPC trực tiếp.
Cần: start-dev.ps1 đang chạy, write-toggle Odoo bật."""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.tests.live_verify_common import (
    odoo_transport, drive_conversation, has_tool_leak, Scenario, print_result)


def _make_so(odoo, qty: int = 1, confirm: bool = True) -> str:
    """confirm=False → để nguyên draft (dùng cho kịch bản 3). Field set
    {"product_id", "product_uom_qty"} đã verify thật khớp
    mcp-servers/odoo/server.py::create_quotation.

    is_storable=True required in addition to sale_ok=True: without it, search
    picks the first sale_ok=True product by id (id=1 "Restaurant Expenses",
    type=service on this instance — verified via direct XML-RPC read before
    writing this script) — Odoo never generates an outgoing stock.picking for
    a confirmed SO on a service product. _outgoing_picking_done() would then
    never observe a picking at all regardless of what the agent does,
    breaking the premise of every scenario below (mirrors the identical
    warehouse_receiving fix in live_verify_skill_warehouse.py::
    _make_fresh_confirmed_po, itself found by the same live-run diagnostic)."""
    customer_ids = odoo.call("res.partner", "search",
                             [[("customer_rank", ">", 0)]],
                             {"order": "id asc", "limit": 1})
    product_ids = odoo.call("product.product", "search",
                            [[("sale_ok", "=", True), ("is_storable", "=", True)]],
                            {"order": "id asc", "limit": 1})
    so_id = odoo.call("sale.order", "create",
                      [{"partner_id": customer_ids[0],
                        "order_line": [(0, 0, {"product_id": product_ids[0],
                                               "product_uom_qty": qty})]}], {})
    if confirm:
        # action_confirm (not button_confirm) — verified against the real
        # production tool mcp-servers/odoo/server.py::confirm_sale_order
        # (line ~150), which calls odoo("sale.order", "action_confirm", ...).
        # sale.order differs from purchase.order here (purchase.order's real
        # method is button_confirm, per Task 3's finding) — grep-verified,
        # not assumed by analogy.
        odoo.call("sale.order", "action_confirm", [[so_id]], {})
    so = odoo.call("sale.order", "read", [[so_id]], {"fields": ["name"]})[0]
    return so["name"]


def _outgoing_picking_done(odoo, so_name: str) -> bool:
    rows = odoo.call("sale.order", "search_read",
                     [[("name", "=", so_name)]], {"fields": ["picking_ids"]})
    if not rows or not rows[0]["picking_ids"]:
        return False
    pickings = odoo.call("stock.picking", "read", [rows[0]["picking_ids"]],
                         {"fields": ["state"]})
    return any(p["state"] == "done" for p in pickings)


def scenario_happy_path(odoo) -> Scenario:
    so_name = _make_so(odoo)
    history, sid = [], "live-verify-delivery-happy-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=f"giao hàng cho đơn bán {so_name}",
        responders=[], final_answer="có, tôi xác nhận")
    if not result.completed:
        return Scenario("happy_path", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    if not _outgoing_picking_done(odoo, so_name):
        return Scenario("happy_path", False, result.turns,
                        f"picking của {so_name} chưa done")
    return Scenario("happy_path", True, result.turns, f"{so_name} picking done")


def scenario_refusal(odoo) -> Scenario:
    so_name = _make_so(odoo)
    history, sid = [], "live-verify-delivery-refuse-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=f"giao hàng cho đơn bán {so_name}",
        responders=[], final_answer="không")
    if not result.completed:
        return Scenario("refusal", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    if _outgoing_picking_done(odoo, so_name):
        return Scenario("refusal", False, result.turns,
                        f"picking của {so_name} đã done — LẼ RA KHÔNG ĐƯỢC")
    return Scenario("refusal", True, result.turns, "không done, đúng")


def scenario_draft_order_refused(odoo) -> Scenario:
    # Đã grep-verify mcp-servers/odoo/server.py::deliver_order: tool tự kiểm
    # state not in ("sale","done") và trả envelope lỗi rõ ràng, không crash.
    # SOP_PROMPT không dặn tra state trước khi gọi deliver_order_gated → confirm
    # gate vẫn xuất hiện bình thường (mirror happy_path's final_answer).
    so_name = _make_so(odoo, confirm=False)
    history, sid = [], "live-verify-delivery-draft-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=f"giao hàng cho đơn bán {so_name}",
        responders=[], final_answer="có, tôi xác nhận")
    if _outgoing_picking_done(odoo, so_name):
        return Scenario("draft_order_refused", False, result.turns,
                        f"picking của {so_name} đã done — LẼ RA KHÔNG ĐƯỢC (đơn còn nháp)")
    leaks = [l for a in result.all_answers for l in has_tool_leak(a)]
    if leaks:
        return Scenario("draft_order_refused", False, result.turns,
                        f"lộ tool name: {leaks}")
    return Scenario("draft_order_refused", True, result.turns,
                    f"không tạo picking, không lộ tool (turns={result.turns}, "
                    f"completed={result.completed})")


def main():
    odoo = odoo_transport()
    scenarios = [scenario_happy_path(odoo), scenario_refusal(odoo),
                scenario_draft_order_refused(odoo)]
    ok = print_result("e2e-skill-delivery", scenarios)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
