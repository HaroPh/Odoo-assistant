import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.agentic_registry import AGENTIC_SKILLS, AgenticSkillSpec


def test_registry_entries_complete():
    assert set(AGENTIC_SKILLS) == {"warehouse_receiving", "delivery",
                                   "discount_quote"}
    for spec in AGENTIC_SKILLS.values():
        assert isinstance(spec, AgenticSkillSpec)
        assert spec.node.startswith("skill_agentic_")
        assert spec.triggers and all(isinstance(t, str) for t in spec.triggers)
        assert callable(spec.build)


def test_agentic_trigger_sets_mutually_disjoint():
    # Router chọn skill bằng substring match trên cùng một câu — các skill
    # không được có phrase chồng nhau (một phrase là substring của phrase
    # skill khác sẽ gây double-claim tùy thứ tự dict). Từ Đợt 3 check này
    # phủ cả 3 skill pairwise (test so với SKILLS tất định đã xóa cùng
    # hạ tầng tầng-2).
    specs = list(AGENTIC_SKILLS.values())
    for i, a in enumerate(specs):
        for b in specs[i + 1:]:
            for ta in a.triggers:
                for tb in b.triggers:
                    assert ta not in tb and tb not in ta, (ta, tb)
