# backend/tests/conftest.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage


def make_mock_llm(response_text: str):
    """Return a mock LLM that always responds with response_text."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=response_text))
    return llm
