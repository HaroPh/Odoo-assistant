# backend/src/agents/erp_agent.py
import os
import uuid

from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .graph import build_graph

LITELLM_URL  = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
LITELLM_KEY  = os.environ.get("LITELLM_MASTER_KEY", "")
MCP_ODOO_URL = os.environ.get("MCP_ODOO_URL", "http://localhost:8001/sse")
MODEL        = os.environ.get("AGENT_MODEL", "qwen3:8b")
PG_CONN      = os.environ.get(
    "DATABASE_URL",
    "postgresql://admin:changeme@localhost:5433/ai_assistant",
)


class ERPAgent:
    def __init__(self) -> None:
        self.graph = None
        self.tool_names: list[str] = []
        self._pool = None

    async def setup(self) -> None:
        llm = ChatOpenAI(
            model=MODEL, base_url=LITELLM_URL, api_key=LITELLM_KEY,
            temperature=0, timeout=120,
        )
        client = MultiServerMCPClient(
            {"odoo": {"url": MCP_ODOO_URL, "transport": "sse"}}
        )
        tools = await client.get_tools()
        self.tool_names = [t.name for t in tools]

        self._pool = AsyncConnectionPool(
            conninfo=PG_CONN,
            max_size=20,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await self._pool.open()
        checkpointer = AsyncPostgresSaver(self._pool)
        await checkpointer.setup()  # creates checkpoint tables if not present

        self.graph = build_graph(llm, tools, checkpointer)

    async def chat(self, messages: list[dict], thread_id: str | None = None) -> str:
        """
        messages: list of {"role", "content"} dicts (user/assistant).
        thread_id: stable ID per conversation — needed for interrupt/resume.
                   Defaults to a fresh UUID (safe when write gate is locked).
        """
        if not messages:
            return "Vui lòng nhập câu hỏi."

        tid = thread_id or uuid.uuid4().hex
        config = {"configurable": {"thread_id": tid}}

        # NOTE (Phase 3): when WRITE_ACTIONS_ENABLED=true the write planner calls
        # interrupt() and ainvoke returns with an "__interrupt__" key instead of a
        # final AI message. This method does not yet detect that or wire
        # Command(resume=...), so the confirmation question is not surfaced and
        # resume is unreachable. Safe while the write gate is locked (default).
        result = await self.graph.ainvoke({"messages": messages}, config=config)
        return result["messages"][-1].content.strip()

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
