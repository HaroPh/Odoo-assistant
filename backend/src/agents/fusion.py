# backend/src/agents/fusion.py
"""Fusion sub-agent (agents layer) — the `mixed` intent.

A bounded-agentic tool-calling agent that answers Group-3 questions needing both
internal documents and live ERP data. It calls search_documents (a thin wrapper
over rag.retrieve) and the erp_query business read tools, reasons over both, and
the node appends a deterministic citation footer for the documents the model
reports having used this turn. The WRITE_TOOL_NAMES filter keeps fusion
read-only as a defense-in-depth guard even though the graph now hands it only
read tools. rag/ stays synthesis-free; all answer/citation logic lives here.
"""
import asyncio
import logging

from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from langchain.agents import create_agent as _create_agent

from .state import ERPAgentState
from .prompts import FUSION_PROMPT
from .synthesis import passes_floor, _format_context, extract_used_citations, SAFE_MSG
from ..rag.retrieve import retrieve

logger = logging.getLogger(__name__)

WRITE_TOOL_NAMES = frozenset({
    "confirm_sale_order", "confirm_purchase_order", "post_invoice",
    "validate_picking", "create_quotation", "create_rfq",
    "inventory_adjustment", "internal_transfer", "scrap_product",
})


def _make_search_documents_tool(collected: list):
    """A search_documents tool bound to this turn's chunk collector.

    Returns labeled chunk text for the agent to read; records the Chunk objects
    into `collected` so the node can build a deterministic citation footer.
    Empty/off-topic retrieval (cosine pre-filter) → sentinel, collects nothing.
    """
    @tool
    async def search_documents(query: str) -> str:
        """Tìm trong tài liệu nội bộ (chính sách, SLA, quy trình, SOP, bảng giá)
        để lấy điều khoản hoặc thông tin liên quan đến câu hỏi."""
        result = await asyncio.to_thread(retrieve, query)
        if result.is_empty() or not passes_floor(result):
            return "Không tìm thấy tài liệu liên quan."
        # Assumes sequential tool calls (ReAct-style) — two concurrent calls
        # would both snapshot the same `start` and collide. Degrades safely
        # via extract_used_citations()'s fallback (never crashes, never
        # cites zero) if that assumption is ever broken.
        start = len(collected) + 1
        collected.extend(result.chunks)
        return _format_context(result.chunks, start=start)

    return search_documents


def make_fusion_node(llm, tools):
    """Group-3 fusion: a bounded-agentic agent over the erp_query READ tools +
    search_documents. Write tools are filtered out (read-only). The node appends
    a deterministic citation footer for whatever documents were retrieved this
    turn. Any failure degrades to SAFE_MSG — the graph never crashes.
    """
    read_tools = [t for t in tools if t.name not in WRITE_TOOL_NAMES]

    async def fusion_node(state: ERPAgentState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"), None)
        if last_human is None:
            return {"messages": [AIMessage(content=SAFE_MSG)]}
        collected: list = []
        search_tool = _make_search_documents_tool(collected)
        agent = _create_agent(llm, [*read_tools, search_tool],
                              system_prompt=FUSION_PROMPT)
        try:
            result = await agent.ainvoke({"messages": state["messages"]})
            answer = (result["messages"][-1].content or "").strip()
            if not answer:
                return {"messages": [AIMessage(content=SAFE_MSG)]}
            clean, footer = extract_used_citations(answer, collected)
            answer = clean + footer
        except Exception:
            logger.exception("fusion_node failed")
            answer = SAFE_MSG
        return {"messages": [AIMessage(content=answer)]}

    return fusion_node
