# backend/src/agents/nodes.py
from langchain_core.messages import SystemMessage, HumanMessage

from .state import ERPAgentState
from .prompts import INTENT_ROUTER_PROMPT

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
