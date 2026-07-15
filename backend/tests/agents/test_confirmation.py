# backend/tests/agents/test_confirmation.py
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.confirmation import (
    CONFIRM, CANCEL, UNCLEAR,
    classify_keyword, classify_confirmation,
)


# ── classify_keyword ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["có", "Có", "có!", "yes", "ok", "đồng ý", "xác nhận"])
def test_keyword_clear_yes_returns_confirm(text):
    assert classify_keyword(text) == CONFIRM


@pytest.mark.parametrize("text", ["không", "Không", "no", "hủy", "thôi", "đừng"])
def test_keyword_clear_no_returns_cancel(text):
    assert classify_keyword(text) == CANCEL


def test_keyword_yes_phrase_with_extra_words_returns_confirm():
    assert classify_keyword("ừ làm đi") == CONFIRM


def test_keyword_no_phrase_with_extra_words_returns_cancel():
    assert classify_keyword("thôi khỏi") == CANCEL


def test_keyword_negation_has_both_signals_returns_unclear():
    # "không đồng ý" = do NOT agree — contains a cancel word and a confirm word
    assert classify_keyword("không đồng ý") == UNCLEAR


def test_keyword_neither_signal_returns_unclear():
    assert classify_keyword("nó sẽ làm gì?") == UNCLEAR


@pytest.mark.parametrize("text", ["ừm", "um", "ừm để tôi xem lại đã, chưa chắc lắm"])
def test_keyword_hesitation_filler_returns_unclear(text):
    # Regression (found 2026-07-16 via a live confirm-gate probe on
    # feat/agentic-wr-guardrails): "ừm"/"um" are Vietnamese hesitation
    # fillers ("um..."), not affirmatives. They used to sit in
    # _CONFIRM_WORDS, so a hedging reply like "ừm để tôi xem lại đã, chưa
    # chắc lắm" ("um, let me check again, not quite sure") matched the
    # confirm keyword alone (no cancel word present) and short-circuited
    # straight to CONFIRM at the keyword fast-path — skipping the LLM
    # fallback entirely, which would have classified it UNCLEAR. Reproduced
    # 2/2 live model runs (qwen3:8b, qwythos-9b), same root cause both
    # times. A write-confirmation gate must never treat a hesitation filler
    # as a clean one-sided yes (see module docstring: "the danger is
    # asymmetric").
    assert classify_keyword(text) == UNCLEAR


async def test_hybrid_hesitation_filler_falls_back_to_llm_not_auto_confirm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNCLEAR"))
    result = await classify_confirmation("ừm để tôi xem lại đã, chưa chắc lắm", llm)
    assert result == UNCLEAR
    llm.ainvoke.assert_awaited_once()


# ── classify_confirmation (hybrid) ────────────────────────────────────────────

async def test_hybrid_keyword_hit_skips_llm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="CONFIRM"))
    result = await classify_confirmation("có", llm)
    assert result == CONFIRM
    llm.ainvoke.assert_not_awaited()


async def test_hybrid_keyword_miss_falls_back_to_llm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="CONFIRM"))
    result = await classify_confirmation("sao cũng được, bạn quyết đi", llm)
    assert result == CONFIRM
    llm.ainvoke.assert_awaited_once()


async def test_hybrid_llm_garbage_returns_unclear():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="tôi không chắc lắm"))
    result = await classify_confirmation("ờ thì tùy bạn vậy", llm)
    assert result == UNCLEAR
