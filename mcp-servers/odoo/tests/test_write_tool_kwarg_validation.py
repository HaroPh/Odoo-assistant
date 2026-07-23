"""Tests for forbid_extra_kwargs (mcp-servers/odoo/security.py), which
patches FastMCP's per-tool Pydantic arg model to reject unrecognized
tool-call kwargs instead of silently dropping them (Pydantic's default
extra='ignore'). Mirrors the erp_query fix from Round 3 (a075060), adapted
for FastMCP's own tool registration instead of LangChain's args_schema."""
import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.tool_manager import ToolManager

from security import forbid_extra_kwargs


def dummy_write_tool(order_ref: str, amount: float = 0.0) -> str:
    return f"{order_ref}:{amount}"


def test_forbid_extra_kwargs_sets_extra_forbid_on_isolated_tool_manager():
    tm = ToolManager()
    tm.add_tool(dummy_write_tool)
    tool = tm.get_tool("dummy_write_tool")
    assert tool.fn_metadata.arg_model.model_config.get("extra") != "forbid"

    forbid_extra_kwargs(tm)

    assert tool.fn_metadata.arg_model.model_config["extra"] == "forbid"


def test_forbid_extra_kwargs_refreshes_advertised_json_schema():
    tm = ToolManager()
    tm.add_tool(dummy_write_tool)
    tool = tm.get_tool("dummy_write_tool")
    assert tool.parameters.get("additionalProperties") is not False

    forbid_extra_kwargs(tm)

    assert tool.parameters["additionalProperties"] is False


def test_forbid_extra_kwargs_rejects_bogus_kwarg_via_real_dispatch():
    tm = ToolManager()
    tm.add_tool(dummy_write_tool)
    forbid_extra_kwargs(tm)

    async def _run():
        with pytest.raises(ToolError):
            await tm.call_tool("dummy_write_tool",
                               {"order_ref": "S00012", "bogus_field": "x"})
    asyncio.run(_run())


def test_forbid_extra_kwargs_still_allows_valid_kwargs_via_real_dispatch():
    tm = ToolManager()
    tm.add_tool(dummy_write_tool)
    forbid_extra_kwargs(tm)

    async def _run():
        result = await tm.call_tool("dummy_write_tool",
                                    {"order_ref": "S00012", "amount": 1.5})
        assert result == "S00012:1.5"
    asyncio.run(_run())
