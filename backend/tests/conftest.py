# backend/tests/conftest.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage


def make_mock_llm(response_text: str):
    """Return a mock LLM that always responds with response_text."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=response_text))
    return llm


def make_mock_llm_seq(responses):
    """Mock LLM trả lần lượt từng phần tử — cho test corrective retry (A5).
    Gọi quá số phần tử sẽ raise StopIteration → lộ ngay lỗi gọi thừa."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[AIMessage(content=r) for r in responses])
    return llm
