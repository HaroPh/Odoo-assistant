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
    assert set(NEXT_STEPS) == {"create_quotation", "confirm_sale_order",
                               "create_invoice_from_order"}
    lw = {"tool": "create_quotation", "ok": True, "ref": "S00031",
          "model": "sale.order", "res_id": 42, "state": "draft", "display": "x"}
    step = NEXT_STEPS["create_quotation"]
    assert step.tool == "confirm_sale_order"
    assert step.label == "Xác nhận báo giá"
    assert step.args(lw) == {"order_ref": "S00031"}
    step2 = NEXT_STEPS["confirm_sale_order"]
    assert step2.tool == "create_invoice_from_order"
    assert step2.label == "Tạo hóa đơn"
    assert step2.args({**lw, "tool": "confirm_sale_order"}) == {"order_ref": "S00031"}
    step3 = NEXT_STEPS["create_invoice_from_order"]
    assert step3.tool == "post_invoice"
    assert step3.label == "Phát hành hóa đơn"
    assert step3.args({**lw, "tool": "create_invoice_from_order",
                       "ref": None, "res_id": 61}) == {"invoice_id": 61}
    assert "post_invoice" not in NEXT_STEPS      # terminal
