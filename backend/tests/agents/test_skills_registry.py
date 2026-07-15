import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.skills import SKILLS, match_skill, SkillSpec


def test_skills_registry_has_both_pilot_skills():
    assert set(SKILLS) == {"discount_quote", "warehouse_receiving"}
    for spec in SKILLS.values():
        assert isinstance(spec, SkillSpec)
        assert spec.triggers and spec.extract_prompt and spec.node and spec.build


def test_match_skill_exact_trigger():
    assert match_skill("báo giá chiết khấu cho khách A") == "discount_quote"
    assert match_skill("làm quy trình nhập kho cho P00003") == "warehouse_receiving"


def test_match_skill_diacritic_insensitive():
    assert match_skill("bao gia chiet khau cho khach A") == "discount_quote"
    assert match_skill("quy trinh nhap kho cho P00003") == "warehouse_receiving"


def test_match_skill_case_insensitive():
    assert match_skill("BÁO GIÁ CHIẾT KHẤU cho khách A") == "discount_quote"


def test_match_skill_no_trigger_returns_none():
    assert match_skill("xác nhận đơn S00012") is None
    assert match_skill("") is None
    assert match_skill(None) is None


def test_match_skill_does_not_hijack_plain_receive_order():
    # bare "nhập kho" (no "quy trình") must NOT trigger the skill — it would
    # hijack the existing plain receive_order chain (spec §6.2).
    assert match_skill("nhập kho đơn P00003") is None
