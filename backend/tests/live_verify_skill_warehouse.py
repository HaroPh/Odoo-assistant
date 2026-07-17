#!/usr/bin/env python3
# coding: utf-8
"""E2E live-verify: warehouse_receiving skill. 5 kịch bản, mỗi kịch bản tự tạo
PO mới (search_read supplier/product động, KHÔNG hardcode entity) qua XML-RPC
trực tiếp rồi lái hội thoại qua chat thật.
Cần: start-dev.ps1 đang chạy, write-toggle Odoo bật."""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.tests.live_verify_common import (
    odoo_transport, drive_conversation, drive_fixed_turns,
    has_tool_leak, Scenario, print_result)


def _make_fresh_confirmed_po(odoo, qty_per_line: int) -> tuple[str, int]:
    """Field set {"product_id", "product_qty"} đã verify thật khớp
    mcp-servers/odoo/server.py::create_rfq (tool ghi thật đang chạy production) —
    không đoán, Odoo tự điền uom/price qua default ở tầng ORM."""
    supplier_ids = odoo.call("res.partner", "search",
                             [[("supplier_rank", ">", 0)]],
                             {"order": "id asc", "limit": 1})
    # is_storable=True required in addition to purchase_ok=True: without it,
    # search picks the first purchase_ok=True product by id (id=1 "Restaurant
    # Expenses", type=service on this instance) — Odoo never generates an
    # incoming stock.picking for a confirmed PO on a service product
    # (verified: PO confirmed, picking_ids=[]). receive_order (mcp-servers/
    # odoo/server.py:456-461, _validate_order_pickings status "none") then
    # correctly treats "no picking to receive" as already-complete — so
    # _picking_done() can never observe True regardless of what the agent
    # does, breaking the premise of every warehouse-receiving scenario.
    product_ids = odoo.call("product.product", "search",
                            [[("purchase_ok", "=", True),
                              ("is_storable", "=", True)]],
                            {"order": "id asc", "limit": 1})
    po_id = odoo.call("purchase.order", "create",
                      [{"partner_id": supplier_ids[0],
                        "order_line": [(0, 0, {"product_id": product_ids[0],
                                               "product_qty": qty_per_line})]}], {})
    # button_confirm (not action_confirm) — verified against this Odoo instance's
    # purchase_order.py (def button_confirm at line 625) AND the real production
    # tool mcp-servers/odoo/server.py::confirm_purchase_order (line 179), which
    # calls odoo("purchase.order", "button_confirm", ...). action_confirm doesn't
    # exist on this model — that was an unverified generic-Odoo-API assumption.
    odoo.call("purchase.order", "button_confirm", [[po_id]], {})
    po = odoo.call("purchase.order", "read", [[po_id]], {"fields": ["name"]})[0]
    return po["name"], qty_per_line


def _picking_done(odoo, po_name: str) -> bool:
    rows = odoo.call("purchase.order", "search_read",
                     [[("name", "=", po_name)]],
                     {"fields": ["picking_ids"]})
    if not rows or not rows[0]["picking_ids"]:
        return False
    pickings = odoo.call("stock.picking", "read", [rows[0]["picking_ids"]],
                         {"fields": ["state"]})
    return any(p["state"] == "done" for p in pickings)


def _message_count(odoo, po_name: str) -> int:
    rows = odoo.call("purchase.order", "search", [[("name", "=", po_name)]], {})
    if not rows:
        return 0
    return odoo.call("mail.message", "search_count",
                     [[("model", "=", "purchase.order"), ("res_id", "=", rows[0])]], {})


_QTY_RESPONDER = lambda answer: (
    lambda low: "số lượng" in low or "kiểm đếm" in low, answer)
_QC_RESPONDER = (lambda low: "qc" in low or "chất lượng" in low, "đạt")


def scenario_happy_path(odoo) -> Scenario:
    po_name, total_qty = _make_fresh_confirmed_po(odoo, 10)
    history, sid = [], "live-verify-wh-happy-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=f"làm quy trình nhập kho cho đơn mua {po_name}",
        responders=[_QTY_RESPONDER(str(total_qty)), _QC_RESPONDER],
        final_answer="có, tôi xác nhận")
    if not result.completed:
        return Scenario("happy_path", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    if not _picking_done(odoo, po_name):
        return Scenario("happy_path", False, result.turns,
                        f"picking của {po_name} chưa done")
    return Scenario("happy_path", True, result.turns, f"{po_name} picking done")


def scenario_qty_mismatch(odoo) -> Scenario:
    po_name, total_qty = _make_fresh_confirmed_po(odoo, 10)
    history, sid = [], "live-verify-wh-mismatch-" + uuid.uuid4().hex[:8]
    before_msgs = _message_count(odoo, po_name)
    result = drive_conversation(
        history, sid,
        opening_msg=f"làm quy trình nhập kho cho đơn mua {po_name}",
        responders=[_QTY_RESPONDER(str(total_qty - 5))],
        final_answer="có, tôi xác nhận")
    if not result.completed:
        return Scenario("qty_mismatch", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    if _picking_done(odoo, po_name):
        return Scenario("qty_mismatch", False, result.turns,
                        f"picking của {po_name} đã done — LẼ RA KHÔNG ĐƯỢC (số lượng lệch)")
    after_msgs = _message_count(odoo, po_name)
    if after_msgs <= before_msgs:
        return Scenario("qty_mismatch", False, result.turns,
                        f"không có note mới trên {po_name} ({before_msgs}->{after_msgs})")
    return Scenario("qty_mismatch", True, result.turns,
                    f"picking không done, có note mới ({before_msgs}->{after_msgs})")


def scenario_qc_fail(odoo) -> Scenario:
    po_name, total_qty = _make_fresh_confirmed_po(odoo, 10)
    history, sid = [], "live-verify-wh-qcfail-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=f"làm quy trình nhập kho cho đơn mua {po_name}",
        responders=[_QTY_RESPONDER(str(total_qty)),
                    (lambda low: "qc" in low or "chất lượng" in low, "không đạt")],
        final_answer="có, tôi xác nhận")
    # completed=False LÀ kết quả ĐÚNG kỳ vọng ở kịch bản này (SOP không gọi tool
    # ghi nào khi QC fail — không có confirm-gate nào để hoàn thành). PASS dựa
    # HOÀN TOÀN vào state assertion, không dựa result.completed.
    if _picking_done(odoo, po_name):
        return Scenario("qc_fail", False, result.turns,
                        f"picking của {po_name} đã done — LẼ RA KHÔNG ĐƯỢC (QC không đạt)")
    return Scenario("qc_fail", True, result.turns,
                    f"picking không done, đúng (turns={result.turns}, completed={result.completed})")


def scenario_no_po_tool_leak(odoo) -> Scenario:
    history, sid = [], "live-verify-wh-nopo-" + uuid.uuid4().hex[:8]
    answers = drive_fixed_turns(
        history, sid,
        opening_msg=("thực hiện quy trình nhập kho cho sản phẩm Kìm điện cách "
                     "điện với 50 sản phẩm"),
        followups=["tôi chưa tạo đơn mua, giúp tôi được không?",
                  "tôi cần Nhập kho từ nhà cung cấp trực tiếp"])
    leaks = {i: has_tool_leak(a) for i, a in enumerate(answers) if has_tool_leak(a)}
    if leaks:
        return Scenario("no_po_tool_leak", False, len(answers),
                        f"lộ tool name ở lượt {list(leaks.keys())}: {leaks}")
    return Scenario("no_po_tool_leak", True, len(answers),
                    f"không lộ tool name qua {len(answers)} lượt")


def scenario_refusal(odoo) -> Scenario:
    po_name, total_qty = _make_fresh_confirmed_po(odoo, 10)
    history, sid = [], "live-verify-wh-refuse-" + uuid.uuid4().hex[:8]
    before_msgs = _message_count(odoo, po_name)
    result = drive_conversation(
        history, sid,
        opening_msg=f"làm quy trình nhập kho cho đơn mua {po_name}",
        responders=[_QTY_RESPONDER(str(total_qty)), _QC_RESPONDER],
        final_answer="không")
    if not result.completed:
        return Scenario("refusal", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    if _picking_done(odoo, po_name):
        return Scenario("refusal", False, result.turns,
                        f"picking của {po_name} đã done — LẼ RA KHÔNG ĐƯỢC (đã từ chối)")
    after_msgs = _message_count(odoo, po_name)
    if after_msgs != before_msgs:
        return Scenario("refusal", False, result.turns,
                        f"có note mới dù đã từ chối ({before_msgs}->{after_msgs})")
    return Scenario("refusal", True, result.turns, "không done, không note mới, đúng")


def main():
    odoo = odoo_transport()
    scenarios = [scenario_happy_path(odoo), scenario_qty_mismatch(odoo),
                scenario_qc_fail(odoo), scenario_no_po_tool_leak(odoo),
                scenario_refusal(odoo)]
    ok = print_result("e2e-skill-warehouse", scenarios)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
