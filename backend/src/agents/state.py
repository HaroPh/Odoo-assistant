# backend/src/agents/state.py
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ERPAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    intent: str | None            # "erp_read" | "erp_write" | "rag" | "unknown"
    pending_action: dict | None   # {"tool": str, "args": dict, "summary": str}
    confirmed: bool | None        # None=not asked, True=yes, False=no
    last_write: dict | None       # last write result handle:
                                  # {"tool", "ok", "ref", "model", "res_id", "state", "display"}
    working_context: dict | None  # bản ghi ĐƠN đang làm việc (cross-turn):
                                  # {"ref","model","display"}. PERSISTENT — NGƯỢC với
                                  # pending_action/confirmed/last_write (clear mọi path):
                                  # không node nào set None/clear key này; node chỉ THÊM
                                  # key khi có giá trị mới (omit-vs-None) → giá trị sống
                                  # xuyên lượt nhờ channel semantics của LangGraph.
