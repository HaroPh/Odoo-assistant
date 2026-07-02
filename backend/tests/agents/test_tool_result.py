import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.tool_result import _tool_result_text


def test_plain_string_passthrough():
    assert _tool_result_text("hello") == "hello"


def test_content_block_list_joined():
    blocks = [{"type": "text", "text": "Đã tạo "}, {"type": "text", "text": "S00042."}]
    assert _tool_result_text(blocks) == "Đã tạo S00042."


def test_nodes_still_reexports_it():
    from backend.src.agents import nodes
    assert nodes._tool_result_text("x") == "x"
