import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock, AsyncMock
from langgraph.types import Command
from backend.src.agents.erp_agent import _decide_resume, _pending_kind, _pending_options


class _Itr:
    def __init__(self, value): self.value = value


class _Task:
    def __init__(self, interrupts): self.interrupts = interrupts


class _Snap:
    def __init__(self, value): self.tasks = [_Task([_Itr(value)])]


def test_pending_kind_and_options():
    snap = _Snap({"kind": "disambiguation", "question": "?",
                  "options": [{"id": 1, "name": "A"}]})
    assert _pending_kind(snap) == "disambiguation"
    assert _pending_options(snap) == [{"id": 1, "name": "A"}]


@pytest.mark.asyncio
async def test_disambiguation_valid_pick_resumes_id():
    opts = [{"id": 41, "name": "Azur Interior"}, {"id": 52, "name": "Azur Furniture"}]
    out = await _decide_resume("disambiguation", opts, "q", "2", MagicMock())
    assert isinstance(out, Command) and out.resume == 52


@pytest.mark.asyncio
async def test_disambiguation_unclear_reasks():
    opts = [{"id": 41, "name": "Azur Interior"}, {"id": 52, "name": "Azur Furniture"}]
    out = await _decide_resume("disambiguation", opts, "PICK ONE", "azur", MagicMock())
    assert out == "PICK ONE"


@pytest.mark.asyncio
async def test_confirm_yes_resumes_true():
    out = await _decide_resume("confirm", [], "q", "có", MagicMock())
    assert isinstance(out, Command) and out.resume is True


@pytest.mark.asyncio
async def test_confirm_unclear_reasks():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="UNCLEAR"))
    out = await _decide_resume("confirm", [], "CONFIRM?", "tại sao?", llm)
    assert out == "CONFIRM?"


@pytest.mark.asyncio
async def test_absent_kind_defaults_to_confirm():
    out = await _decide_resume(None, [], "q", "không", MagicMock())
    assert isinstance(out, Command) and out.resume is False
