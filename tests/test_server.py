"""End-to-end: tools are registered, read-only, typed, and callable through the MCP server."""

import asyncio

import pytest

from warehouse_context_mcp.server import build_server
from warehouse_context_mcp.service import WarehouseContext

EXPECTED = {
    "list_tables",
    "describe_table",
    "table_lineage",
    "table_freshness",
    "sample_rows",
    "run_query",
}


def test_tools_are_registered_read_only_and_typed(ctx: WarehouseContext) -> None:
    tools = asyncio.run(build_server(ctx).list_tools())
    assert {t.name for t in tools} == EXPECTED
    for tool in tools:
        assert tool.annotations
        assert tool.annotations.read_only_hint
        assert tool.output_schema, f"{tool.name} has no output schema"


def test_argument_bounds_are_in_the_input_schema(ctx: WarehouseContext) -> None:
    tools = {t.name: t for t in asyncio.run(build_server(ctx).list_tools())}
    depth = tools["table_lineage"].input_schema["properties"]["depth"]
    assert (depth["minimum"], depth["maximum"]) == (1, 5)


def test_run_query_refusal_is_structured(ctx: WarehouseContext) -> None:
    server = build_server(ctx)
    result = asyncio.run(server.call_tool("run_query", {"sql": "drop table dim_vessels"}))
    assert "only SELECT queries are allowed" in str(result)


def test_out_of_range_arguments_are_rejected(ctx: WarehouseContext) -> None:
    server = build_server(ctx)
    with pytest.raises(Exception, match="less than or equal"):
        asyncio.run(server.call_tool("table_lineage", {"table": "dim_vessels", "depth": 50}))
