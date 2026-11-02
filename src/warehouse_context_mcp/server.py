"""MCP transport: exposes WarehouseContext as tools over stdio."""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .adapters import DuckDBWarehouse
from .catalog import Catalog
from .context import Limits, WarehouseContext

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def build_server(ctx: WarehouseContext) -> MCPServer:
    mcp = MCPServer(
        "warehouse-context",
        instructions=(
            "Governed context about a data warehouse. Call list_tables and describe_table "
            "before writing SQL; prefer tables with an owner and fresh data."
        ),
    )

    @mcp.tool(annotations=READ_ONLY)
    def list_tables() -> list[dict[str, Any]]:
        """List warehouse tables with their documented purpose and owner."""
        return ctx.list_tables()

    @mcp.tool(annotations=READ_ONLY)
    def describe_table(table: str) -> dict[str, Any]:
        """Columns, types, documentation, PII flags, grain and row count for one table.
        Call this before writing SQL against a table."""
        return ctx.describe_table(table)

    @mcp.tool(annotations=READ_ONLY)
    def table_lineage(table: str, depth: int = 3) -> dict[str, Any]:
        """Upstream sources and downstream consumers of a table, from the curated catalog."""
        return ctx.lineage(table, depth=max(1, min(depth, 5)))

    @mcp.tool(annotations=READ_ONLY)
    def table_freshness(table: str) -> dict[str, Any]:
        """When the table was last updated and whether that meets its documented expectation."""
        return ctx.freshness(table)

    @mcp.tool(annotations=READ_ONLY)
    def sample_rows(table: str, n: int = 5) -> dict[str, Any]:
        """A few example rows, with PII columns masked."""
        return ctx.sample_rows(table, n)

    @mcp.tool(annotations=READ_ONLY)
    def run_query(sql: str, max_rows: int = 100) -> dict[str, Any]:
        """Run ONE read-only SELECT. Writes, DDL and multiple statements are rejected,
        a row limit is always applied, and PII columns can only be counted, never read."""
        return ctx.query(sql, max_rows=max_rows)

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="warehouse-context-mcp")
    parser.add_argument("--duckdb", required=True, help="path to a DuckDB database file")
    parser.add_argument("--catalog", help="path to the catalog YAML file")
    parser.add_argument("--max-rows", type=int, default=200)
    args = parser.parse_args(argv)

    ctx = WarehouseContext(
        DuckDBWarehouse(args.duckdb), Catalog.load(args.catalog), Limits(max_rows=args.max_rows)
    )
    build_server(ctx).run()


if __name__ == "__main__":
    main()
