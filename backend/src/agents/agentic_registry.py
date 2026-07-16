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
