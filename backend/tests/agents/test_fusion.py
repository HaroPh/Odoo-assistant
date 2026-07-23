# backend/tests/agents/test_fusion.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from backend.src.rag.types import Chunk, RetrievalResult
from backend.src.agents.synthesis import USED_MARKER


def _chunk(**kw):
    d = dict(chunk_id=1, doc_id="d", source_file="C:/docs/policy.docx", doc_title="P",
             section_path="Chính sách hoàn hàng › Điều 4", page=1, sheet=None,
             row_range=None, text="Hoàn hàng trong 30 ngày.", dense_score=0.7,
             sparse_score=None, rrf_score=0.02, rank=0)
    d.update(kw)
    return Chunk(**d)


def _result(chunks):
    return RetrievalResult(query="q", query_used="q", chunks=chunks,
                           top_score=(chunks[0].rrf_score if chunks else 0.0),
                           total_candidates=len(chunks), method="hybrid-rrf")


@pytest.mark.asyncio
async def test_search_documents_empty_returns_sentinel_no_collect(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "thủ đô nước Pháp?"})
    assert out == "Không tìm thấy tài liệu liên quan."
    assert collected == []


@pytest.mark.asyncio
async def test_search_documents_below_floor_returns_sentinel(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c = _chunk(dense_score=0.2, sparse_score=None)
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([c]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "câu ngoài corpus"})
    assert out == "Không tìm thấy tài liệu liên quan."
    assert collected == []


@pytest.mark.asyncio
async def test_search_documents_passing_returns_text_and_collects(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c = _chunk(dense_score=0.7)
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([c]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "chính sách hoàn hàng"})
    assert "Hoàn hàng trong 30 ngày." in out
    assert len(collected) == 1
    assert collected[0].section_path == "Chính sách hoàn hàng › Điều 4"


@pytest.mark.asyncio
async def test_search_documents_second_call_numbers_continue(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c1 = _chunk(chunk_id=1, source_file="C:/docs/policy.docx",
                section_path="Chính sách hoàn hàng › Điều 4")
    c2 = _chunk(chunk_id=2, source_file="C:/docs/sla.docx",
                section_path="SLA › Điều 2")
    results = iter([_result([c1]), _result([c2])])
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: next(results))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out1 = await tool.ainvoke({"query": "chính sách hoàn hàng"})
    out2 = await tool.ainvoke({"query": "SLA"})
    assert out1.startswith("[1] ")
    assert out2.startswith("[2] ")
    assert len(collected) == 2


from unittest.mock import MagicMock, AsyncMock
from langchain_core.messages import HumanMessage, AIMessage
from backend.src.agents.state import ERPAgentState
from backend.src.agents.synthesis import SAFE_MSG
from backend.tests.conftest import make_mock_llm


def _state(text: str) -> ERPAgentState:
    return ERPAgentState(messages=[HumanMessage(content=text)],
                         intent=None, pending_action=None, confirmed=None)


@pytest.mark.asyncio
async def test_fusion_filters_write_tools(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    captured = {}

    def fake_create_agent(llm, tools, system_prompt=None):
        captured["tool_names"] = [t.name for t in tools]
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content="ok")]})
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    read_tool = MagicMock(); read_tool.name = "get_overdue_invoices"
    write_tool = MagicMock(); write_tool.name = "post_invoice"  # ∈ WRITE_TOOL_NAMES
    node = fusion_mod.make_fusion_node(MagicMock(), tools=[read_tool, write_tool])
    await node(_state("..."))
    assert "get_overdue_invoices" in captured["tool_names"]
    assert "post_invoice" not in captured["tool_names"]
    assert "search_documents" in captured["tool_names"]


@pytest.mark.asyncio
async def test_fusion_happy_path_appends_footer(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c = _chunk(dense_score=0.7, section_path="Chính sách hoàn hàng › Điều 4",
               source_file="C:/docs/policy.docx", page=1)
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([c]))

    def fake_create_agent(llm, tools, system_prompt=None):
        search = next(t for t in tools if t.name == "search_documents")
        agent = MagicMock()

        async def ainvoke(payload):
            await search.ainvoke({"query": "chính sách hoàn hàng"})  # populate collected
            return {"messages": [AIMessage(content="Khách đã quá hạn, không được hoàn.")]}

        agent.ainvoke = ainvoke
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    node = fusion_mod.make_fusion_node(make_mock_llm("1: CÓ"), tools=[])
    out = await node(_state("theo chính sách, đơn X hoàn được không?"))
    content = out["messages"][0].content
    assert "Khách đã quá hạn, không được hoàn." in content
    assert "📄 Nguồn:" in content
    assert "policy.docx, tr.1" in content


@pytest.mark.asyncio
async def test_fusion_marker_filters_footer_to_second_call_chunk(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c1 = _chunk(chunk_id=1, source_file="C:/docs/policy.docx",
                section_path="Chính sách hoàn hàng › Điều 4")
    c2 = _chunk(chunk_id=2, source_file="C:/docs/sla.docx",
                section_path="SLA › Điều 2")
    results = iter([_result([c1]), _result([c2])])
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: next(results))

    def fake_create_agent(llm, tools, system_prompt=None):
        search = next(t for t in tools if t.name == "search_documents")
        agent = MagicMock()

        async def ainvoke(payload):
            await search.ainvoke({"query": "chính sách hoàn hàng"})
            await search.ainvoke({"query": "SLA"})
            return {"messages": [AIMessage(
                content=f"Theo SLA, xử lý trong 24h.\n\n{USED_MARKER}: 2")]}

        agent.ainvoke = ainvoke
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    node = fusion_mod.make_fusion_node(make_mock_llm("1: CÓ"), tools=[])
    out = await node(_state("SLA xử lý trong bao lâu?"))
    content = out["messages"][0].content
    assert "Theo SLA, xử lý trong 24h." in content
    assert USED_MARKER not in content
    assert "sla.docx" in content
    assert "policy.docx" not in content


@pytest.mark.asyncio
async def test_fusion_erp_only_no_footer(monkeypatch):
    import backend.src.agents.fusion as fusion_mod

    def fake_create_agent(llm, tools, system_prompt=None):
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content="Có 5 đơn trễ.")]})
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    node = fusion_mod.make_fusion_node(MagicMock(), tools=[])
    out = await node(_state("đơn nào trễ?"))
    content = out["messages"][0].content
    assert content == "Có 5 đơn trễ."
    assert "📄 Nguồn:" not in content


@pytest.mark.asyncio
async def test_fusion_empty_answer_returns_safe_msg(monkeypatch):
    import backend.src.agents.fusion as fusion_mod

    def fake_create_agent(llm, tools, system_prompt=None):
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content="")]})
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    node = fusion_mod.make_fusion_node(MagicMock(), tools=[])
    out = await node(_state("..."))
    assert out["messages"][0].content == SAFE_MSG


@pytest.mark.asyncio
async def test_fusion_agent_error_returns_safe_msg(monkeypatch):
    import backend.src.agents.fusion as fusion_mod

    def fake_create_agent(llm, tools, system_prompt=None):
        agent = MagicMock()

        async def boom(payload):
            raise RuntimeError("llm down")

        agent.ainvoke = boom
        return agent

    monkeypatch.setattr(fusion_mod, "_create_agent", fake_create_agent)

    node = fusion_mod.make_fusion_node(MagicMock(), tools=[])
    out = await node(_state("..."))
    assert out["messages"][0].content == SAFE_MSG
