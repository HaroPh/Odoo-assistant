import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
from backend.src.agents.tool_result import parse_write_result
from backend.src.agents.write_registry import NEXT_STEPS


def _env(ok=True, **kw):
    base = {"ok": ok, "ref": "S00031", "model": "sale.order", "res_id": 42,
            "state": "draft", "display": "Đã tạo báo giá S00031 (nháp)."}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def test_envelope_ok_true_returns_display_and_env():
    display, env = parse_write_result(_env())
    assert display == "Đã tạo báo giá S00031 (nháp)."
    assert env["ref"] == "S00031" and env["res_id"] == 42


def test_envelope_ok_false_returns_display_only():
    display, env = parse_write_result(_env(ok=False))
    assert "S00031" in display
    assert env is None


def test_plain_string_passthrough():
    display, env = parse_write_result("Đã tạo RFQ P00013 cho Gemini (2 dòng).")
    assert display.startswith("Đã tạo RFQ")
    assert env is None


def test_mcp_content_blocks_with_json_parse():
    blocks = [{"type": "text", "text": _env()}]
    display, env = parse_write_result(blocks)
    assert env is not None and env["model"] == "sale.order"


def test_non_envelope_json_is_plain_text():
    display, env = parse_write_result(json.dumps({"foo": 1}))
    assert env is None


def test_next_steps_chain_is_linear_and_terminal():
    assert set(NEXT_STEPS) == {
        "create_quotation", "confirm_sale_order", "deliver_order",
        "create_invoice_from_order", "post_invoice",
        "create_rfq", "confirm_purchase_order", "receive_order",
        "create_bill_from_po",
        "update_quotation_lines", "update_rfq_lines",
    }
    lw = {"tool": "x", "ok": True, "ref": "S00031", "model": "sale.order",
          "res_id": 42, "state": "draft", "display": "x"}
    # ── sale chain ──
    assert (NEXT_STEPS["create_quotation"].tool, NEXT_STEPS["create_quotation"].label) \
        == ("confirm_sale_order", "Xác nhận báo giá")
    assert NEXT_STEPS["create_quotation"].args(lw) == {"order_ref": "S00031"}
    # editing a draft's lines re-enters the same "confirm" next step as creation
    assert (NEXT_STEPS["update_quotation_lines"].tool,
            NEXT_STEPS["update_quotation_lines"].label) \
        == ("confirm_sale_order", "Xác nhận báo giá")
    assert NEXT_STEPS["update_quotation_lines"].args(lw) == {"order_ref": "S00031"}
    assert (NEXT_STEPS["confirm_sale_order"].tool, NEXT_STEPS["confirm_sale_order"].label) \
        == ("deliver_order", "Giao hàng")
    assert NEXT_STEPS["confirm_sale_order"].args(lw) == {"order_ref": "S00031"}
    assert (NEXT_STEPS["deliver_order"].tool, NEXT_STEPS["deliver_order"].label) \
        == ("create_invoice_from_order", "Tạo hóa đơn")
    assert NEXT_STEPS["deliver_order"].args(lw) == {"order_ref": "S00031"}
    assert (NEXT_STEPS["create_invoice_from_order"].tool,
            NEXT_STEPS["create_invoice_from_order"].label) \
        == ("post_invoice", "Phát hành hóa đơn")
    assert NEXT_STEPS["create_invoice_from_order"].args(
        {**lw, "ref": None, "res_id": 61}) == {"invoice_id": 61}
    # ── purchase chain ──
    plw = {**lw, "ref": "P00040", "model": "purchase.order", "res_id": 15}
    assert (NEXT_STEPS["create_rfq"].tool, NEXT_STEPS["create_rfq"].label) \
        == ("confirm_purchase_order", "Xác nhận đơn mua")
    assert NEXT_STEPS["create_rfq"].args(plw) == {"order_ref": "P00040"}
    assert (NEXT_STEPS["update_rfq_lines"].tool,
            NEXT_STEPS["update_rfq_lines"].label) \
        == ("confirm_purchase_order", "Xác nhận đơn mua")
    assert NEXT_STEPS["update_rfq_lines"].args(plw) == {"order_ref": "P00040"}
    assert (NEXT_STEPS["confirm_purchase_order"].tool,
            NEXT_STEPS["confirm_purchase_order"].label) \
        == ("receive_order", "Nhận hàng")
    assert NEXT_STEPS["confirm_purchase_order"].args(plw) == {"order_ref": "P00040"}
    assert (NEXT_STEPS["receive_order"].tool, NEXT_STEPS["receive_order"].label) \
        == ("create_bill_from_po", "Tạo hóa đơn NCC")
    assert NEXT_STEPS["receive_order"].args(plw) == {"order_ref": "P00040"}
    assert (NEXT_STEPS["create_bill_from_po"].tool,
            NEXT_STEPS["create_bill_from_po"].label) \
        == ("post_invoice", "Phát hành hóa đơn")
    assert NEXT_STEPS["create_bill_from_po"].args(
        {**plw, "ref": None, "res_id": 65}) == {"invoice_id": 65}
    # ── shared post_invoice → register_payment ──
    assert (NEXT_STEPS["post_invoice"].tool,
            NEXT_STEPS["post_invoice"].label) \
        == ("register_payment", "Ghi nhận thanh toán")
    assert NEXT_STEPS["post_invoice"].args(lw) == {"invoice_id": 42}
