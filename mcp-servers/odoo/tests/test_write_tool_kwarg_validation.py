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


import server


def _mock_confirm_sale_order_odoo(monkeypatch):
    """Patch server.odoo so confirm_sale_order's business logic completes
    without touching real Odoo — order S00012 found in draft state."""
    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return [{"id": 7, "name": "S00012", "state": "draft"}]
        if method == "action_confirm":
            return True
        raise AssertionError(f"unexpected method {method}")
    monkeypatch.setattr(server, "odoo", fake_odoo)


def test_write_tool_rejects_bogus_kwarg_through_real_server_dispatch(monkeypatch):
    _mock_confirm_sale_order_odoo(monkeypatch)

    async def _run():
        with pytest.raises(ToolError):
            await server.mcp._tool_manager.call_tool(
                "confirm_sale_order",
                {"order_ref": "S00012", "bogus_field": "x"},
            )
    asyncio.run(_run())


def test_write_tool_accepts_valid_kwargs_through_real_server_dispatch(monkeypatch):
    _mock_confirm_sale_order_odoo(monkeypatch)

    async def _run():
        result = await server.mcp._tool_manager.call_tool(
            "confirm_sale_order", {"order_ref": "S00012"},
        )
        assert "đã xác nhận" in str(result).lower()
    asyncio.run(_run())


def test_all_write_tools_have_extra_forbid_after_server_import():
    tools = server.mcp._tool_manager.list_tools()
    assert len(tools) == 27
    for tool in tools:
        assert tool.fn_metadata.arg_model.model_config.get("extra") == "forbid", (
            f"{tool.name} missing extra='forbid'")
