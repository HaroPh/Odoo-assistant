import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END
from backend.tests.conftest import make_mock_llm, make_mock_llm_seq
from backend.src.agents.skills import make_skill_extract_node, route_after_skill_extract


@pytest.mark.asyncio
async def test_extract_success_sets_prefixed_pending_action():
    llm = make_mock_llm(json.dumps({"customer": "Azur", "lines": [{"product": "Tủ", "qty": 2}]}))
    node = make_skill_extract_node(llm)
    out = await node({"messages": [HumanMessage(content="báo giá chiết khấu cho Azur, 2 Tủ")]})
    assert out["pending_action"]["tool"] == "skill:discount_quote"
    assert out["pending_action"]["args"]["customer"] == "Azur"


@pytest.mark.asyncio
async def test_extract_failure_after_retry_clears_pending_action_with_message():
    llm = make_mock_llm_seq(["not json", "still not json"])
    node = make_skill_extract_node(llm)
    out = await node({"messages": [HumanMessage(content="báo giá chiết khấu cho Azur")]})
    assert out["pending_action"] is None
    assert "Không đọc được" in out["messages"][0].content


def test_route_after_extract_dispatches_to_matched_skill_node():
    state = {"pending_action": {"tool": "skill:discount_quote", "args": {}}}
    assert route_after_skill_extract(state) == "skill_discount_quote"


def test_route_after_extract_ends_on_no_pending_action():
    assert route_after_skill_extract({"pending_action": None}) == END


def test_route_after_extract_ends_on_non_skill_tool():
    assert route_after_skill_extract({"pending_action": {"tool": "create_quotation"}}) == END
