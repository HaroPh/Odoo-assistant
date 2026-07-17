import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.tool_leak_guard import has_tool_leak


def test_has_tool_leak_detects_known_tool_names():
    assert has_tool_leak("Không thể dùng receive_order vì thiếu mã đơn.") == ["receive_order"]


def test_has_tool_leak_clean_text_returns_empty():
    assert has_tool_leak("Bạn cần cung cấp mã đơn mua để tiếp tục.") == []


def test_has_tool_leak_case_insensitive():
    assert has_tool_leak("KHÔNG DÙNG FLAG_ORDER_FOR_REVIEW") == ["flag_order_for_review"]


def test_has_tool_leak_multiple_markers():
    text = "Không dùng receive_order, cũng không dùng flag_order_for_review."
    assert set(has_tool_leak(text)) == {"receive_order", "flag_order_for_review"}
