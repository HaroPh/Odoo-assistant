import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.agentic_registry import AGENTIC_SKILLS, AgenticSkillSpec
from backend.src.agents.skills import SKILLS


def test_registry_entries_complete():
    assert set(AGENTIC_SKILLS) == {"warehouse_receiving", "delivery"}
    for spec in AGENTIC_SKILLS.values():
        assert isinstance(spec, AgenticSkillSpec)
        assert spec.node.startswith("skill_agentic_")
        assert spec.triggers and all(isinstance(t, str) for t in spec.triggers)
        assert callable(spec.build)


def test_agentic_trigger_sets_mutually_disjoint():
    # Router chọn skill bằng substring match trên cùng một câu — 2 skill
    # không được có phrase chồng nhau (một phrase là substring của phrase
    # skill khác sẽ gây double-claim tùy thứ tự dict).
    specs = list(AGENTIC_SKILLS.values())
    for i, a in enumerate(specs):
        for b in specs[i + 1:]:
            for ta in a.triggers:
                for tb in b.triggers:
                    assert ta not in tb and tb not in ta, (ta, tb)


def test_agentic_triggers_disjoint_from_deterministic_skills():
    # Đóng finding Minor #2 của final review nhánh delivery: assert tường
    # minh thay vì chỉ kiểm tra hành vi — trigger agentic không được chồng
    # với trigger của SKILLS (#2, còn sống tới Đợt 3).
    det_triggers = [t for spec in SKILLS.values() for t in spec.triggers]
    ag_triggers = [t for spec in AGENTIC_SKILLS.values() for t in spec.triggers]
    for ta in ag_triggers:
        for td in det_triggers:
            assert ta not in td and td not in ta, (ta, td)
