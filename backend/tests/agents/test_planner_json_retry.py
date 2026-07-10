# backend/tests/agents/test_planner_json_retry.py
"""A5 redefined: parse pipeline 2 tầng (loads → salvage tất định strip
<think>/fence) + corrective retry đúng 1 lần cho write planner.
Spec: docs/superpowers/specs/2026-07-10-a5-planner-json-retry-design.md"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.nodes import _parse_plan

VALID = ('{"tool": "create_quotation", "args": {"customer": "Azure"}, '
         '"summary": "tạo báo giá"}')
EXPECTED = {"tool": "create_quotation", "args": {"customer": "Azure"},
            "summary": "tạo báo giá"}


def test_clean_json():
    assert _parse_plan(VALID) == EXPECTED


def test_fenced_json_with_label():
    assert _parse_plan(f"```json\n{VALID}\n```") == EXPECTED


def test_fenced_json_no_label():
    assert _parse_plan(f"```\n{VALID}\n```") == EXPECTED


def test_think_block_then_json():
    raw = f"<think>user muốn tạo báo giá... chọn tool nào đây</think>\n{VALID}"
    assert _parse_plan(raw) == EXPECTED


def test_draft_json_inside_think_not_picked():
    # JSON nháp nằm TRONG khối think phải bị strip cùng khối think —
    # tuyệt đối không được vớ nhầm nó (spec §3.1 cấm brace-extract).
    draft = '{"tool": "WRONG_TOOL", "args": {}}'
    raw = f"<think>nháp: {draft} — không, sửa lại</think>\n{VALID}"
    assert _parse_plan(raw) == EXPECTED


def test_think_plus_fence_combined():
    raw = f"<think>suy nghĩ</think>\n```json\n{VALID}\n```"
    assert _parse_plan(raw) == EXPECTED


def test_prose_returns_none():
    assert _parse_plan("Tôi không chắc bạn muốn làm gì.") is None


def test_empty_returns_none():
    assert _parse_plan("") is None


def test_broken_json_returns_none():
    assert _parse_plan('{"tool": "x", args}') is None


def test_json_array_returns_none():
    # JSON hợp lệ nhưng không phải dict — trước đây sẽ crash AttributeError
    # ở plan.get(...); giờ đi đường fallback sạch.
    assert _parse_plan("[1, 2]") is None


def test_prose_around_json_not_salvaged():
    # Văn xuôi xen kẽ KHÔNG cứu tất định được (fence phải bọc TOÀN BỘ phần
    # còn lại) — đây là việc của corrective retry, không phải salvage.
    assert _parse_plan(f"Đây là JSON bạn cần: {VALID} nhé!") is None


from langchain_core.messages import HumanMessage

from backend.src.agents import write_gate
from backend.src.agents.nodes import (_JSON_CORRECTION, _plan_json,
                                      make_erp_write_planner_node)
from backend.tests.conftest import make_mock_llm_seq


async def test_valid_first_try_single_call():
    llm = make_mock_llm_seq([VALID])
    plan = await _plan_json(llm, "SYS", [HumanMessage(content="tạo báo giá")])
    assert plan == EXPECTED
    assert llm.ainvoke.call_count == 1   # không retry thừa


async def test_retry_succeeds_and_carries_correction_context():
    llm = make_mock_llm_seq(["ừm để tôi nghĩ đã", VALID])
    plan = await _plan_json(llm, "SYS", [HumanMessage(content="tạo báo giá")])
    assert plan == EXPECTED
    assert llm.ainvoke.call_count == 2
    second_msgs = llm.ainvoke.call_args_list[1].args[0]
    # 2 message cuối của lần gọi 2: AIMessage(raw lỗi) + HumanMessage(correction)
    assert second_msgs[-2].type == "ai"
    assert second_msgs[-2].content == "ừm để tôi nghĩ đã"
    assert second_msgs[-1].type == "human"
    assert second_msgs[-1].content == _JSON_CORRECTION


async def test_both_attempts_fail_returns_none():
    llm = make_mock_llm_seq(["hỏng lần 1", "hỏng lần 2"])
    assert await _plan_json(llm, "SYS", [HumanMessage(content="x")]) is None
    assert llm.ainvoke.call_count == 2   # bounded — đúng 1 retry, không hơn


async def test_node_retry_messages_do_not_leak_into_state(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    valid = '{"tool": "create_quotation", "args": {"customer_name": "Azure"}}'
    llm = make_mock_llm_seq(["not json at all", valid])
    node = make_erp_write_planner_node(llm)
    out = await node({"messages": [HumanMessage(content="tạo báo giá cho Azure")],
                      "working_context": None})
    # create_quotation là coordinated tool → node trả pending_action và KHÔNG
    # có key "messages" — 2 message sửa lỗi sống chết trong _plan_json,
    # không rò vào state (spec §3.2).
    assert out["pending_action"]["tool"] == "create_quotation"
    assert "messages" not in out
    assert llm.ainvoke.call_count == 2
