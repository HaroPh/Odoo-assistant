# backend/src/agents/nodes.py
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent as _create_agent

from .state import ERPAgentState
from .prompts import INTENT_ROUTER_PROMPT, SYSTEM_PROMPT

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
