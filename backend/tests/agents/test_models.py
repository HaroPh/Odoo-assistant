import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from unittest.mock import MagicMock, patch

from backend.src.agents.models import (
    ROLES, CLOUD_ALLOWED, default_model, model_for, is_qwen,
    make_llm, make_llms, llms_from_single,
)


def test_all_roles_default_to_local(monkeypatch):
    for r in ROLES:
        monkeypatch.delenv(f"MODEL_{r.upper()}", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    assert {model_for(r) for r in ROLES} == {"qwen3:8b"}


def test_cloud_allowed_roles_respect_env(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTER", "gemini-flash-lite")
    monkeypatch.setenv("MODEL_EVALUATOR", "gemini-flash-lite")
    monkeypatch.setenv("MODEL_CHITCHAT", "gemma-cloud")
    assert model_for("router") == "gemini-flash-lite"
    assert model_for("evaluator") == "gemini-flash-lite"
    assert model_for("chitchat") == "gemma-cloud"


def test_data_bearing_roles_ignore_env_M2_enforcement(monkeypatch):
    # QĐ M2 ép ở tầng thực thi: env trỏ cloud cho role mang dữ liệu bị BỎ QUA.
    for r in ("read", "planner", "fusion", "synthesis"):
        monkeypatch.setenv(f"MODEL_{r.upper()}", "gemini-flash-lite")
        assert model_for(r) == default_model(), r


def test_agent_model_env_moves_all_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "qwen3:14b")
    for r in ROLES:
        monkeypatch.delenv(f"MODEL_{r.upper()}", raising=False)
    assert model_for("planner") == "qwen3:14b"
    assert model_for("router") == "qwen3:14b"


def test_is_qwen():
    assert is_qwen("qwen3:8b") and is_qwen("Qwen3:14B")
    assert not is_qwen("gemini-flash-lite") and not is_qwen("gemma-cloud")
    assert not is_qwen("") and not is_qwen(None)


def test_make_llm_timeout_by_family(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTER", "gemini-flash-lite")
    monkeypatch.delenv("MODEL_EVALUATOR", raising=False)
    with patch("backend.src.agents.models.ChatOpenAI") as mock_llm:
        mock_instance = MagicMock()
        mock_llm.return_value = mock_instance
        make_llm("router")
        assert mock_llm.call_args[1]["timeout"] == 30
        make_llm("evaluator")
        assert mock_llm.call_args[1]["timeout"] == 120


def test_make_llms_covers_all_roles():
    with patch("backend.src.agents.models.ChatOpenAI") as mock_llm:
        mock_llm.return_value = MagicMock()
        llms = make_llms()
        assert set(llms) == set(ROLES)


def test_llms_from_single():
    m = MagicMock()
    d = llms_from_single(m)
    assert set(d) == set(ROLES) and all(v is m for v in d.values())


import pytest
from langchain_core.messages import HumanMessage, AIMessage


@pytest.mark.asyncio
async def test_respond_unknown_sends_only_last_human_message_M5():
    # M5 (ADR-009): history có assistant-turn chứa dữ liệu ERP ("Tồn kho ... 42")
    # → node chit-chat (được phép chạy cloud theo M2) không được thấy nó.
    from backend.src.agents.nodes import make_respond_unknown_node
    captured = {}

    class FakeLLM:
        async def ainvoke(self, msgs):
            captured["msgs"] = msgs
            return AIMessage(content="Không có gì!")

    node = make_respond_unknown_node(FakeLLM())
    out = await node({"messages": [
        HumanMessage(content="tồn kho Desk Pad?"),
        AIMessage(content="Tồn kho Desk Pad: 42 cái."),
        HumanMessage(content="cảm ơn nhé"),
    ]})
    assert len(captured["msgs"]) == 1
    assert captured["msgs"][0].content == "cảm ơn nhé"
    payload = " ".join(m.content for m in captured["msgs"])
    assert "42" not in payload and "Tồn kho" not in payload
    assert out["messages"][0].content == "Không có gì!"


@pytest.mark.asyncio
async def test_respond_unknown_filters_by_type_not_position_M5():
    # Regression case: an AIMessage sits AFTER the newest human message. A
    # buggy "just take messages[-1:]" would forward it (or fail to filter by
    # type at all); the correct behavior is to filter by m.type == "human"
    # and use the newest one regardless of what trails it.
    from backend.src.agents.nodes import make_respond_unknown_node
    captured = {}

    class FakeLLM:
        async def ainvoke(self, msgs):
            captured["msgs"] = msgs
            return AIMessage(content="Không có gì!")

    node = make_respond_unknown_node(FakeLLM())
    out = await node({"messages": [
        HumanMessage(content="turn1"),
        AIMessage(content="ERP-A leak"),
        HumanMessage(content="turn2 newest"),
        AIMessage(content="stray trailing ERP-B leak"),
    ]})
    assert len(captured["msgs"]) == 1
    assert captured["msgs"][0].content == "turn2 newest"
    payload = " ".join(m.content for m in captured["msgs"])
    assert "ERP-A leak" not in payload
    assert "stray trailing ERP-B leak" not in payload
    assert out["messages"][0].content == "Không có gì!"


@pytest.mark.asyncio
async def test_respond_unknown_zero_human_messages_never_calls_llm_M5():
    # Critical fix: when state has NO human message at all (a rare but
    # reachable route via make_intent_router_node), the node must NOT call
    # the LLM at all — it must return a fixed canned reply, so an AIMessage
    # carrying real ERP data can never be forwarded to the (cloud-eligible)
    # chit-chat LLM.
    from backend.src.agents.nodes import make_respond_unknown_node

    class FakeLLM:
        async def ainvoke(self, msgs):
            raise AssertionError("LLM must not be invoked when there is no human message")

    node = make_respond_unknown_node(FakeLLM())
    out = await node({"messages": [
        AIMessage(content="Tồn kho Desk Pad: 42 cái."),
    ]})
    assert out["messages"][0].content == "Xin lỗi, bạn cần hỗ trợ gì?"
    payload = " ".join(m.content for m in out["messages"])
    assert "42" not in payload and "Desk Pad" not in payload
