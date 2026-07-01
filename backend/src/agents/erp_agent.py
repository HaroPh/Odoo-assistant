# backend/src/agents/erp_agent.py
import os
import uuid
import time

from langchain_openai import ChatOpenAI
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .graph import build_graph
from .confirmation import CONFIRM, UNCLEAR, classify_confirmation
from .disambiguation import parse_selection

LITELLM_URL  = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
LITELLM_KEY  = os.environ.get("LITELLM_MASTER_KEY", "")
MCP_ODOO_URL = os.environ.get("MCP_ODOO_URL", "http://localhost:8001/sse")
MODEL        = os.environ.get("AGENT_MODEL", "qwen3:8b")
PG_CONN      = os.environ.get(
    "DATABASE_URL",
    "postgresql://admin:changeme@localhost:5433/ai_assistant",
)


def _question_from_interrupts(interrupts) -> str | None:
    """Pull the confirmation question out of a tuple of Interrupt objects."""
    for it in interrupts or ():
        value = getattr(it, "value", None)
        if isinstance(value, dict) and value.get("question"):
            return value["question"]
    return None


def _pending_question(snapshot) -> str | None:
    """Question of the interrupt a parked thread is currently waiting on."""
    for task in getattr(snapshot, "tasks", ()) or ():
        question = _question_from_interrupts(getattr(task, "interrupts", ()))
        if question:
            return question
    return None


def _pending_expiry(snapshot) -> float | None:
    """Epoch-seconds expiry of the interrupt a parked thread waits on, or None."""
    for task in getattr(snapshot, "tasks", ()) or ():
        for it in getattr(task, "interrupts", ()) or ():
            value = getattr(it, "value", None)
            if isinstance(value, dict) and "expires_at" in value:
                return value["expires_at"]
    return None


def _is_parked(snapshot) -> bool:
    """Is the thread waiting on the user (a pending interrupt)? Check the pending
    interrupt directly, not just `snapshot.next`: after resuming one interrupt and
    hitting a SECOND in the same node (disambiguation → confirm), LangGraph leaves
    `snapshot.next` empty while the confirm interrupt is still pending — so relying
    on `next` alone drops the user's confirm into the fresh-request path."""
    return bool(_pending_question(snapshot)) or bool(getattr(snapshot, "next", None))


def _pending_kind(snapshot) -> str | None:
    """Kind of the interrupt a parked thread waits on: 'confirm'|'disambiguation'."""
    for task in getattr(snapshot, "tasks", ()) or ():
        for it in getattr(task, "interrupts", ()) or ():
            value = getattr(it, "value", None)
            if isinstance(value, dict) and value.get("question"):
                return value.get("kind", "confirm")
    return None


def _pending_options(snapshot) -> list:
    """Candidate options of a parked disambiguation interrupt (else [])."""
    for task in getattr(snapshot, "tasks", ()) or ():
        for it in getattr(task, "interrupts", ()) or ():
            value = getattr(it, "value", None)
            if isinstance(value, dict) and value.get("kind") == "disambiguation":
                return value.get("options") or []
    return []


async def _decide_resume(kind, options, question, reply, llm):
    """Turn the user's reply into a resume Command, or a re-ask string.

    disambiguation → parse the selection (deterministic) → resume the chosen id;
    confirm (or unspecified) → classify yes/no → resume a bool. Ambiguous → re-ask."""
    if kind == "disambiguation":
        chosen = parse_selection(reply, options)
        if chosen is None:
            return question or "Vui lòng chọn một mục trong danh sách."
        return Command(resume=chosen)
    verdict = await classify_confirmation(reply, llm)
    if verdict == UNCLEAR:
        return question or "Bạn xác nhận thực hiện thao tác này? (có / không)"
    return Command(resume=verdict == CONFIRM)


class ERPAgent:
    def __init__(self) -> None:
        self.graph = None
        self.tool_names: list[str] = []
        self._pool = None
        self._llm = None

    async def setup(self) -> None:
        llm = ChatOpenAI(
            model=MODEL, base_url=LITELLM_URL, api_key=LITELLM_KEY,
            temperature=0, timeout=120,
        )
        self._llm = llm
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

        # If the thread is parked at a write-confirmation interrupt, this turn is
        # the user's answer — classify it and resume instead of starting over.
        snapshot = await self.graph.aget_state(config)
        if _is_parked(snapshot):
            expires_at = _pending_expiry(snapshot)
            if expires_at is not None and time.time() > expires_at:
                # Stale confirmation: discard it (resume=False is a no-op write,
                # result ignored) and process this turn as a fresh request.
                await self.graph.ainvoke(Command(resume=False), config=config)
                result = await self._invoke_fresh(messages, config)
            else:
                reply = messages[-1]["content"]
                decision = await _decide_resume(
                    _pending_kind(snapshot), _pending_options(snapshot),
                    _pending_question(snapshot), reply, self._llm)
                if isinstance(decision, str):
                    # Unclear reply: re-ask, leave the thread parked.
                    return decision
                result = await self.graph.ainvoke(decision, config=config)
        else:
            result = await self._invoke_fresh(messages, config)

        # A write planner that called interrupt() surfaces as "__interrupt__" with
        # no final AI message — return its confirmation question to the user.
        question = _question_from_interrupts(result.get("__interrupt__"))
        if question:
            return question

        return result["messages"][-1].content.strip()

    async def _invoke_fresh(self, messages: list[dict], config: dict):
        """Run a non-resume turn, overwriting the persisted message channel.

        Open WebUI resends the full conversation every turn, so appending it to
        the checkpointer (the add_messages default) duplicates history without
        bound. Prepending RemoveMessage(REMOVE_ALL_MESSAGES) clears the channel
        first, leaving state["messages"] == exactly the incoming history.
        """
        reset = [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]
        return await self.graph.ainvoke({"messages": reset}, config=config)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
