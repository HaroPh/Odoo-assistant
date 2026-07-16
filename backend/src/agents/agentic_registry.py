# backend/src/agents/agentic_registry.py
"""Single source of truth for agentic SOP skills (tier-2 of the two-tier
architecture). Adding an agentic skill = one module + one row here; the
router trigger check and graph registration all read it. Mirrors
write_registry.py.

Import direction is one-way: this module imports the skill modules; the
skill modules never import this one."""

from dataclasses import dataclass
from typing import Callable

from . import skill_agentic_warehouse_receiving
from . import skill_agentic_delivery


# Trần bước cho ReAct loop của một skill (mỗi chu kỳ agent→tools = 2 bước).
# Flow hợp lệ dài nhất hiện tại (warehouse_receiving: hỏi PO → hỏi số lượng
# → tra PO → [flag | hỏi QC → receive] → chốt) ≈ 6 lượt model ≈ 13 bước;
# 15 cho headroom. Spike v10b (2026-07-16): KHÔNG có trần này thì subgraph
# chạy KHÔNG GIỚI HẠN — mặc định 25 của LangGraph không truyền vào
# subgraph-as-node, chỉ giá trị tường minh trong config mới kế thừa.
AGENTIC_RECURSION_LIMIT = 15


@dataclass(frozen=True)
class AgenticSkillSpec:
    node: str                    # graph node name
    triggers: tuple[str, ...]    # pre-folded (lowercase, no diacritics)
    build: Callable              # (llm, mcp_tools) -> CompiledStateGraph


AGENTIC_SKILLS = {
    "warehouse_receiving": AgenticSkillSpec(
        "skill_agentic_warehouse_receiving",
        skill_agentic_warehouse_receiving.TRIGGERS,
        skill_agentic_warehouse_receiving.make_node),
    "delivery": AgenticSkillSpec(
        "skill_agentic_delivery",
        skill_agentic_delivery.TRIGGERS,
        skill_agentic_delivery.make_node),
}
