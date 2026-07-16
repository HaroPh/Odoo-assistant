"""SOP skill pilot registry: deterministic trigger matching (never an LLM
decision — the local 8-9B model is unreliable at open-ended "which skill"
classification, spec §4.2) plus the one shared node that makes each skill's
single extraction LLM call. See
docs/superpowers/specs/2026-07-15-sop-skill-pilot-design.md §4.3-§4.4.

Import direction is one-way: this module imports the skill modules; the
skill modules never import this one (avoids a partial-init cycle)."""

import unicodedata
from dataclasses import dataclass
from typing import Callable

from langchain_core.messages import AIMessage
from langgraph.graph import END

from .nodes import _plan_json
from . import skill_discount_quote


@dataclass(frozen=True)
class SkillSpec:
    node: str                    # graph node name
    triggers: tuple[str, ...]    # pre-folded (lowercase, no diacritics)
    extract_prompt: str          # system prompt for the extraction call
    build: Callable              # (tools) -> node fn — no llm param; skill
                                 # nodes are LLM-free by design (spec §4.3)


SKILLS = {
    "discount_quote": SkillSpec(
        "skill_discount_quote", skill_discount_quote.TRIGGERS,
        skill_discount_quote.EXTRACT_PROMPT, skill_discount_quote.make_node),
}


_EXTRA_FOLD = str.maketrans("đĐ", "dD")


def _fold(s: str) -> str:
    # đ/Đ (U+0111/U+0110) are standalone Vietnamese letters with no NFD
    # decomposition — combining-mark stripping alone leaves them untouched,
    # unlike vowels with tone/horn marks (á, ơ...). Explicit translate closes
    # that gap (found 2026-07-16: a trigger phrase containing "đơn" silently
    # failed to match naturally-typed diacritic input).
    nfd = unicodedata.normalize("NFD", (s or "").lower())
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.translate(_EXTRA_FOLD)


def match_skill(text: str) -> str | None:
    t = _fold(text)
    for name, spec in SKILLS.items():
        if any(kw in t for kw in spec.triggers):
            return name
    return None


def make_skill_extract_node(llm):
    async def skill_extract(state) -> dict:
        last_human = next((m.content for m in reversed(state["messages"])
                           if m.type == "human"), "")
        name = match_skill(last_human)          # deterministic — replay-safe
        spec = SKILLS.get(name)
        if spec is None:                        # unreachable via router; total anyway
            return {"pending_action": None}
        args = await _plan_json(llm, spec.extract_prompt, state["messages"])
        if args is None:
            return {"pending_action": None,
                    "messages": [AIMessage(content=
                        "Không đọc được yêu cầu. Vui lòng mô tả rõ hơn.")]}
        return {"pending_action": {"tool": f"skill:{name}", "args": args}}
    return skill_extract


def route_after_skill_extract(state) -> str:
    tool = (state.get("pending_action") or {}).get("tool") or ""
    if tool.startswith("skill:"):
        name = tool[len("skill:"):]
        if name in SKILLS:
            return SKILLS[name].node
    return END
