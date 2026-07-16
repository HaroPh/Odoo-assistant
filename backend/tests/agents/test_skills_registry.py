import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.skills import SKILLS, match_skill, SkillSpec, _fold


def test_skills_registry_has_only_discount_quote():
    # warehouse_receiving is deliberately removed on this branch — this
    # experiment replaces its trigger with a dedicated routing check
    # (graph.py) pointed at the new agentic node instead, to avoid both
    # implementations claiming the same trigger phrases via match_skill().
    assert set(SKILLS) == {"discount_quote"}
    for spec in SKILLS.values():
        assert isinstance(spec, SkillSpec)
        assert spec.triggers and spec.extract_prompt and spec.node and spec.build


def test_match_skill_exact_trigger():
    assert match_skill("báo giá chiết khấu cho khách A") == "discount_quote"


def test_match_skill_diacritic_insensitive():
    assert match_skill("bao gia chiet khau cho khach A") == "discount_quote"


def test_match_skill_case_insensitive():
    assert match_skill("BÁO GIÁ CHIẾT KHẤU cho khách A") == "discount_quote"


def test_match_skill_no_trigger_returns_none():
    assert match_skill("xác nhận đơn S00012") is None
    assert match_skill("") is None
    assert match_skill(None) is None


def test_fold_strips_dd_stroke_letter():
    # Regression (found 2026-07-16 via feat/agentic-delivery's wiring
    # tests): đ/Đ have no NFD decomposition (unlike á/ơ/ậ...), so plain
    # combining-mark stripping used to leave them untouched — a trigger
    # phrase containing "đơn" silently failed to match naturally-typed
    # diacritic input ("giao hàng cho đơn bán" folded to "...đon ban...",
    # not "...don ban...").
    assert _fold("đơn hàng") == "don hang"
    assert _fold("Đơn Hàng") == "don hang"
    assert _fold("giao hàng cho đơn bán") == "giao hang cho don ban"


def test_match_skill_no_longer_resolves_warehouse_receiving():
    # Was "warehouse_receiving" before this branch; now None, because the
    # deterministic entry is gone from SKILLS. The phrase is still a valid
    # trigger overall — Task 3's dedicated check in graph.py picks it up
    # for the new agentic node instead, outside match_skill() entirely.
    assert match_skill("làm quy trình nhập kho cho P00003") is None
    assert match_skill("quy trinh nhap kho cho P00003") is None
