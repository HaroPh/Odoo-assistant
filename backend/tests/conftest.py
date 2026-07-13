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


@pytest.fixture(autouse=True)
def friction_log_path(tmp_path, monkeypatch):
    """Mọi test ghi friction vào tmp — không làm bẩn logs/planner_friction.jsonl
    thật. File thật là telemetry dùng để ra quyết định (spec 2026-07-12);
    event từ test (model='mock') sẽ làm sai lệch tỷ lệ nếu lọt vào."""
    p = tmp_path / "friction.jsonl"
    monkeypatch.setenv("FRICTION_LOG_PATH", str(p))
    return p


@pytest.fixture(autouse=True)
def semantic_resolve_off(monkeypatch):
    """resolve_entity đi đường legacy từng bit trong test — không PG/Ollama,
    không bao giờ chạm reranker 2.3GB (spec 2026-07-13 §11). Test nào bật
    "1" phải mock cả semantic.semantic_candidates lẫn reranker.score_pairs."""
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "0")
