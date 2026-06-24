# backend/src/agents/state.py
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ERPAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    intent: str | None            # "erp_read" | "erp_write" | "rag" | "unknown"
    pending_action: dict | None   # {"tool": str, "args": dict, "summary": str}
    confirmed: bool | None        # None=not asked, True=yes, False=no
