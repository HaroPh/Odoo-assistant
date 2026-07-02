# backend/src/agents/nodes.py
import os
import asyncio
import json
import time
import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent as _create_agent
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .prompts import INTENT_ROUTER_PROMPT, SYSTEM_PROMPT, WRITE_PLANNER_PROMPT, WRITE_CONFIRM_PREFIX
from .write_registry import COORDINATED_TOOLS
from ..rag.retrieve import retrieve
from .synthesis import synthesize, SAFE_MSG
from .tool_result import _tool_result_text, parse_write_result

logger = logging.getLogger(__name__)

VALID_INTENTS = {"erp_read", "erp_write", "rag", "mixed", "unknown"}


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


# ── rag (doc-only answering) ──────────────────────────────────────────────────

def make_rag_node(llm):
    """Document Q&A: retrieve (sync, off the loop) → grounded synthesis + citations.

    retrieve() is sync psycopg; asyncio.to_thread keeps the event loop free.
    Any failure degrades to SAFE_MSG — the graph never crashes.
    """
    async def rag_node(state: ERPAgentState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"), None)
        if last_human is None:
            return {"messages": [AIMessage(content=SAFE_MSG)]}
        query = last_human.content
        try:
            result = await asyncio.to_thread(retrieve, query)
            answer = await synthesize(query, result, llm)
        except Exception:
            logger.exception("rag_node failed")
            answer = SAFE_MSG
        return {"messages": [AIMessage(content=answer)]}

    return rag_node


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
            )], "pending_action": None}

        # Plan the action
        response = await llm.ainvoke([
            SystemMessage(content=WRITE_PLANNER_PROMPT),
            *state["messages"],
        ])
        try:
            plan = json.loads(response.content.strip())
        except json.JSONDecodeError:
            logger.warning("Write planner returned non-JSON: %s", response.content)
            return {"messages": [AIMessage(content="Không thể xác định thao tác cần thực hiện. Vui lòng mô tả rõ hơn.")],
                    "pending_action": None}

        # Coordinated writes own their own resolution + confirm; don't interrupt here.
        if plan.get("tool") in COORDINATED_TOOLS:
            return {"pending_action": plan}

        summary = plan.get("summary") or plan.get("tool") or "thao tác"
        question = WRITE_CONFIRM_PREFIX + f"**{summary}**\n\nXác nhận? (có / không)"
        ttl = int(os.environ.get("CONFIRMATION_TTL_SECONDS", "300"))
        confirmed = _interrupt({
            "question": question,
            "action": plan,
            "expires_at": time.time() + ttl,
        })
        return {"pending_action": plan, "confirmed": confirmed}

    return erp_write_planner


# ── erp_write_executor ────────────────────────────────────────────────────────

def make_erp_write_executor_node(tools):
    """Execute the confirmed write by invoking the named tool directly.

    Security (write-gate, rate-limit) lives in the MCP gateway; domain
    validation lives in the tool. Here we only route + fail safe so a bad
    plan never crashes the graph.
    """
    by_name = {t.name: t for t in tools}

    async def erp_write_executor(state: ERPAgentState) -> dict:
        cleared = {"pending_action": None, "confirmed": None, "last_write": None}
        if not state.get("confirmed"):
            return {"messages": [AIMessage(content="Đã hủy thao tác.")], **cleared}

        action = state.get("pending_action") or {}
        name = action.get("tool")
        tool = by_name.get(name)
        if tool is None:
            return {"messages": [AIMessage(
                content=f"Thao tác '{name}' không khả dụng."
            )], **cleared}
        try:
            result = await tool.ainvoke(action.get("args") or {})
        except Exception as e:
            logger.exception("write executor failed: tool=%s", name)
            return {"messages": [AIMessage(
                content=f"Lỗi khi thực hiện thao tác: {e}"
            )], **cleared}
        display, env = parse_write_result(result)
        return {"messages": [AIMessage(content=display)],
                "pending_action": None, "confirmed": None,
                "last_write": {"tool": name, **env} if env else None}

    return erp_write_executor
