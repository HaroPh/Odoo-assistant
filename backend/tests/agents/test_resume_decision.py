import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock, AsyncMock
from langgraph.types import Command
from backend.src.agents.erp_agent import (_decide_resume, _pending_kind,
                                          _pending_options, _is_parked)


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


def test_is_parked_detects_pending_interrupt_when_next_empty():
    # The double-interrupt bug: after a disambiguation resume, the graph suspends
    # at the confirm interrupt but snapshot.next comes back empty. Parked must be
    # detected via the pending interrupt, or the user's "có" is dropped to fresh.
    snap = _Snap({"kind": "confirm", "question": "Xác nhận?", "expires_at": 0})
    assert not getattr(snap, "next", None)   # _Snap has no `next` → falsy
    assert _is_parked(snap) is True


def test_is_parked_false_when_idle():
    class _Idle:
        tasks = ()
    assert _is_parked(_Idle()) is False


def test_is_parked_true_when_next_present():
    class _Next:
        tasks = ()
        next = ("erp_write_executor",)
    assert _is_parked(_Next()) is True


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


def test_pending_options_returned_for_next_action_kind():
    # Anti-regression: the old filter accepted only kind=="disambiguation",
    # which silently disabled index/label picking for the continuation menu.
    snap = _Snap({"kind": "next_action", "question": "menu?",
                  "options": [{"id": True, "name": "Xác nhận báo giá"},
                              {"id": False, "name": "Dừng"}]})
    assert _pending_options(snap) == [{"id": True, "name": "Xác nhận báo giá"},
                                      {"id": False, "name": "Dừng"}]


NEXT_OPTS = [{"id": True, "name": "Xác nhận báo giá"},
             {"id": False, "name": "Dừng"}]


@pytest.mark.asyncio
async def test_next_action_pick_label_resumes_true():
    out = await _decide_resume("next_action", NEXT_OPTS, "q",
                               "xác nhận báo giá", MagicMock())
    assert isinstance(out, Command) and out.resume is True


@pytest.mark.asyncio
async def test_next_action_pick_stop_resumes_false_not_reask():
    # id=False is a VALID pick — `chosen is not None` must accept it.
    out = await _decide_resume("next_action", NEXT_OPTS, "q", "dừng", MagicMock())
    assert isinstance(out, Command) and out.resume is False


@pytest.mark.asyncio
async def test_next_action_index_pick():
    out = await _decide_resume("next_action", NEXT_OPTS, "q", "1", MagicMock())
    assert isinstance(out, Command) and out.resume is True


@pytest.mark.asyncio
async def test_next_action_yes_fallback_to_confirmation():
    llm = MagicMock()   # "có" resolves deterministically in classify_confirmation
    out = await _decide_resume("next_action", NEXT_OPTS, "q", "có", llm)
    assert isinstance(out, Command) and out.resume is True


@pytest.mark.asyncio
async def test_next_action_unclear_reasks():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="UNCLEAR"))
    out = await _decide_resume("next_action", NEXT_OPTS, "MENU?", "hmm?", llm)
    assert out == "MENU?"
