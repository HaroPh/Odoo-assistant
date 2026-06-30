# backend/src/agents/fusion.py
"""Fusion sub-agent (agents layer) — the `mixed` intent.

A bounded-agentic tool-calling agent that answers Group-3 questions needing both
internal documents and live ERP data. It calls search_documents (a thin wrapper
over rag.retrieve) and the Odoo read tools, reasons over both, and the node
appends a deterministic citation footer. Write tools are filtered out — fusion is
read-only. rag/ stays synthesis-free; all answer/citation logic lives here.
"""
import asyncio

from langchain_core.tools import tool

from .synthesis import passes_floor, _format_context
from ..rag.retrieve import retrieve


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
        collected.extend(result.chunks)
        return _format_context(result.chunks)

    return search_documents
