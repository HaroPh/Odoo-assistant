# backend/src/agents/nodes.py
import os
import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent as _create_agent
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .prompts import INTENT_ROUTER_PROMPT, SYSTEM_PROMPT, WRITE_PLANNER_PROMPT, WRITE_CONFIRM_PREFIX

logger = logging.getLogger(__name__)

VALID_INTENTS = {"erp_read", "erp_write", "rag", "unknown"}


def make_intent_router_node(llm):
    async def intent_router(state: ERPAgentState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"),
            None,
        )
        if not last_human:
            return {"intent": "unknown"}

        response = await llm.ainvoke([
            SystemMessage(content=INTENT_ROUTER_PROMPT),
            HumanMessage(content=last_human.content),
        ])
        intent = response.content.strip().lower()
        if intent not in VALID_INTENTS:
            intent = "unknown"
        return {"intent": intent}

    return intent_router


# ── erp_read ─────────────────────────────────────────────────────────────────

def make_erp_read_node(llm, tools):
    agent = _create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)

    async def erp_read(state: ERPAgentState) -> dict:
        result = await agent.ainvoke({"messages": state["messages"]})
        # Return only messages added by the agent (skip the input messages)
        new_msgs = result["messages"][len(state["messages"]):]
        return {"messages": new_msgs}

    return erp_read


# ── rag (stub) ────────────────────────────────────────────────────────────────

async def rag_node(state: ERPAgentState) -> dict:
    return {"messages": [AIMessage(
        content=(
            "Tính năng tìm kiếm tài liệu (RAG) chưa khả dụng trong phiên bản này. "
            "Tính năng này sẽ ra mắt ở Phase 2."
        )
    )]}


# ── respond_unknown ───────────────────────────────────────────────────────────

def make_respond_unknown_node(llm):
    async def respond_unknown(state: ERPAgentState) -> dict:
        response = await llm.ainvoke(state["messages"])
        return {"messages": [response]}

    return respond_unknown


# ── erp_write_planner ─────────────────────────────────────────────────────────

def make_erp_write_planner_node(llm):
    async def erp_write_planner(state: ERPAgentState) -> dict:
        if os.environ.get("WRITE_ACTIONS_ENABLED", "false").lower() != "true":
            return {"messages": [AIMessage(
                content=(
                    "Tính năng ghi (tạo/sửa đơn hàng, cập nhật tồn kho) "
                    "chưa được kích hoạt trong phiên bản này."
                )
            )]}

        # Plan the action
        response = await llm.ainvoke([
            SystemMessage(content=WRITE_PLANNER_PROMPT),
            *state["messages"],
        ])
        try:
            plan = json.loads(response.content.strip())
        except json.JSONDecodeError:
            logger.warning("Write planner returned non-JSON: %s", response.content)
            return {"messages": [AIMessage(content="Không thể xác định thao tác cần thực hiện. Vui lòng mô tả rõ hơn.")]}

        question = WRITE_CONFIRM_PREFIX + f"**{plan['summary']}**\n\nXác nhận? (có / không)"
        confirmed = _interrupt({"question": question, "action": plan})
        return {"pending_action": plan, "confirmed": confirmed}

    return erp_write_planner


# ── erp_write_executor ────────────────────────────────────────────────────────

async def erp_write_executor_node(state: ERPAgentState) -> dict:
    if not state.get("confirmed"):
        return {"messages": [AIMessage(content="Đã hủy thao tác.")]}

    action = state.get("pending_action") or {}
    # STUB: real MCP write tool will replace this when WRITE_ACTIONS_ENABLED=true
    logger.info("STUB write: tool=%s args=%s", action.get("tool"), action.get("args"))
    return {"messages": [AIMessage(
        content=f"[STUB] Đã thực hiện thành công: {action.get('summary', action.get('tool', '?'))}. "
                "(Chế độ mô phỏng — chưa ghi vào Odoo thật.)"
    )]}
