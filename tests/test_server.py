"""End-to-end: tools are registered, read-only, and callable through the MCP server."""

from __future__ import annotations

import asyncio

from warehouse_context_mcp.context import WarehouseContext
from warehouse_context_mcp.server import build_server

EXPECTED = {
    "list_tables",
    "describe_table",
    "table_lineage",
    "table_freshness",
    "sample_rows",
    "run_query",
}


def test_tools_are_registered_and_marked_read_only(ctx: WarehouseContext) -> None:
    tools = asyncio.run(build_server(ctx).list_tools())
    assert {t.name for t in tools} == EXPECTED
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_run_query_through_the_server(ctx: WarehouseContext) -> None:
    server = build_server(ctx)
    result = asyncio.run(server.call_tool("run_query", {"sql": "drop table dim_vessels"}))
    assert "only SELECT queries are allowed" in str(result)
