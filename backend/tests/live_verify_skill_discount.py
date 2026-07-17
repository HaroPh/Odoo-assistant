#!/usr/bin/env python3
# coding: utf-8
"""E2E live-verify: discount_quote skill. Dùng lại Azure Interior (partner_id=15)
+ Large Cabinet (product_id=20, đơn giá 320) — đã verify thật ở live-verify Đợt 3
tier2-retirement. 3 kịch bản: happy_path, refusal, strategic_tier.
Cần: start-dev.ps1 đang chạy (backend :8000 + MCP :8001), write-toggle Odoo bật."""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.tests.live_verify_common import (
    odoo_transport, drive_conversation, Scenario, print_result)

PARTNER_ID = 15
PRODUCT_ID = 20
UNIT_PRICE = 320.0

# Model đôi khi mất 1-2 lượt để resolve đúng tên sản phẩm (Đợt 3 finding) —
# responder này lặp lại tên sản phẩm khi gặp đúng dạng câu hỏi disambiguation.
_PRODUCT_RESPONDERS = [
    (lambda low: "chọn" in low and ("sản phẩm" in low or "danh sách sau" in low),
     "Large Cabinet"),
]


def _count_draft_quotations(odoo) -> int:
    return odoo.call("sale.order", "search_count",
                     [[("partner_id", "=", PARTNER_ID), ("state", "=", "draft")]], {})


def _newest_draft_price_unit(odoo) -> float | None:
    ids = odoo.call("sale.order", "search",
                    [[("partner_id", "=", PARTNER_ID), ("state", "=", "draft")]],
                    {"order": "id desc", "limit": 1})
    if not ids:
        return None
    order = odoo.call("sale.order", "read", [ids], {"fields": ["order_line"]})[0]
    lines = odoo.call("sale.order.line", "read", [order["order_line"]],
                      {"fields": ["product_id", "price_unit"]})
    for l in lines:
        if l["product_id"][0] == PRODUCT_ID:
            return l["price_unit"]
    return None


def scenario_happy_path(odoo) -> Scenario:
    history, sid = [], "live-verify-discount-happy-" + uuid.uuid4().hex[:8]
    before = _count_draft_quotations(odoo)
    result = drive_conversation(
        history, sid,
        opening_msg=("báo giá chiết khấu cho Azure Interior, 2 Large Cabinet, "
                     "khách thuộc cấp thân thiết"),
        responders=_PRODUCT_RESPONDERS, final_answer="có, tôi xác nhận")
    if not result.completed:
        return Scenario("happy_path", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt: {result.final_answer[:200]}")
    after = _count_draft_quotations(odoo)
    if after != before + 1:
        return Scenario("happy_path", False, result.turns,
                        f"số quotation draft {before}->{after}, kỳ vọng +1")
    price = _newest_draft_price_unit(odoo)
    expected = round(UNIT_PRICE * 0.95, 2)
    if price is None or abs(price - expected) > 0.01:
        return Scenario("happy_path", False, result.turns,
                        f"price_unit={price}, kỳ vọng {expected}")
    return Scenario("happy_path", True, result.turns, f"price_unit={price} đúng (5%)")


def scenario_refusal(odoo) -> Scenario:
    history, sid = [], "live-verify-discount-refuse-" + uuid.uuid4().hex[:8]
    before = _count_draft_quotations(odoo)
    result = drive_conversation(
        history, sid,
        opening_msg=("báo giá chiết khấu cho Azure Interior, 2 Large Cabinet, "
                     "khách thuộc cấp thường"),
        responders=_PRODUCT_RESPONDERS, final_answer="không")
    if not result.completed:
        return Scenario("refusal", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    after = _count_draft_quotations(odoo)
    if after != before:
        return Scenario("refusal", False, result.turns,
                        f"số quotation draft {before}->{after}, kỳ vọng không đổi")
    return Scenario("refusal", True, result.turns, "không tạo quotation, đúng")


def scenario_strategic_tier(odoo) -> Scenario:
    history, sid = [], "live-verify-discount-strategic-" + uuid.uuid4().hex[:8]
    result = drive_conversation(
        history, sid,
        opening_msg=("báo giá chiết khấu cho Azure Interior, 2 Large Cabinet, "
                     "khách thuộc cấp đối tác chiến lược"),
        responders=_PRODUCT_RESPONDERS, final_answer="có, tôi xác nhận")
    if not result.completed:
        return Scenario("strategic_tier", False, result.turns,
                        f"không hoàn thành sau {result.turns} lượt")
    price = _newest_draft_price_unit(odoo)
    expected = round(UNIT_PRICE * 0.90, 2)
    if price is None or abs(price - expected) > 0.01:
        return Scenario("strategic_tier", False, result.turns,
                        f"price_unit={price}, kỳ vọng {expected}")
    return Scenario("strategic_tier", True, result.turns, f"price_unit={price} đúng (10%)")


def main():
    odoo = odoo_transport()
    scenarios = [scenario_happy_path(odoo), scenario_refusal(odoo),
                scenario_strategic_tier(odoo)]
    ok = print_result("e2e-skill-discount", scenarios)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
