import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents import skill_gate


def test_unset_defaults_on(monkeypatch):
    # Default ON since 2026-07-16 (graduated from pilot flag after live
    # verification of both agentic skills + the intent-gate in
    # _route_by_intent that stops trigger phrases hijacking read/RAG
    # questions).
    monkeypatch.delenv("ERP_SKILLS_ENABLED", raising=False)
    assert skill_gate.skills_enabled() is True


def test_explicit_zero_is_off(monkeypatch):
    # "0" is the ONLY recognized off-value — the emergency kill-switch.
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "0")
    assert skill_gate.skills_enabled() is False


def test_explicit_one_is_on(monkeypatch):
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "1")
    assert skill_gate.skills_enabled() is True


def test_garbage_value_is_on(monkeypatch):
    # Routing flag, not a security gate (write_gate is): default-on
    # semantics means any value other than the documented "0" enables.
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "true")
    assert skill_gate.skills_enabled() is True


def test_fold_lowercases_and_strips_diacritics():
    from backend.src.agents.skill_gate import _fold
    assert _fold("BÁO GIÁ Chiết Khấu") == "bao gia chiet khau"
    assert _fold("") == ""
    assert _fold(None) == ""


def test_fold_strips_dd_stroke_letter():
    # Regression (found 2026-07-16 via feat/agentic-delivery's wiring
    # tests): đ/Đ have no NFD decomposition (unlike á/ơ/ậ...), so plain
    # combining-mark stripping used to leave them untouched — a trigger
    # phrase containing "đơn" silently failed to match naturally-typed
    # diacritic input ("giao hàng cho đơn bán" folded to "...đon ban...",
    # not "...don ban...").
    from backend.src.agents.skill_gate import _fold
    assert _fold("đơn hàng") == "don hang"
    assert _fold("Đơn Hàng") == "don hang"
    assert _fold("giao hàng cho đơn bán") == "giao hang cho don ban"
