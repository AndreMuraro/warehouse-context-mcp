"""MCP transport: a thin layer exposing :class:`WarehouseContext` as read-only tools.

Tool arguments are validated by their annotations before the service sees them, and every
tool returns a typed model, so clients get a JSON schema for each result. The same server
runs over stdio (a client launches it) or Streamable HTTP (a long-running service).
"""

from collections.abc import Sequence
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .domain.models import Freshness, Lineage, QueryResult, TableDescription, TableSummary
from .service import WarehouseContext

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

INSTRUCTIONS = (
    "Governed context about a data warehouse. Call list_tables and describe_table before "
    "writing SQL; prefer tables with an owner and fresh data. Query refusals explain how "
    "to fix the query."
)

type TableName = Annotated[str, Field(min_length=1, max_length=255, description="Table name")]

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def build_server(ctx: WarehouseContext) -> MCPServer:
    mcp = MCPServer("warehouse-context", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    def list_tables() -> list[TableSummary]:
        """List warehouse tables with their documented purpose and owner."""
        return ctx.list_tables()

    @mcp.tool(annotations=READ_ONLY)
    def describe_table(table: TableName) -> TableDescription:
        """Columns, types, documentation, PII flags, grain and row count for one table.

        Call this before writing SQL against a table.
        """
        return ctx.describe_table(table)

    @mcp.tool(annotations=READ_ONLY)
    def table_lineage(table: TableName, depth: Annotated[int, Field(ge=1, le=5)] = 3) -> Lineage:
        """Upstream sources and downstream consumers of a table, from the curated catalog."""
        return ctx.lineage(table, depth=depth)

    @mcp.tool(annotations=READ_ONLY)
    def table_freshness(table: TableName) -> Freshness:
        """When the table was last updated and whether that meets its documented expectation."""
        return ctx.freshness(table)

    @mcp.tool(annotations=READ_ONLY)
    def sample_rows(table: TableName, n: Annotated[int, Field(ge=1, le=20)] = 5) -> QueryResult:
        """A few example rows, with PII columns masked."""
        return ctx.sample_rows(table, n)

    @mcp.tool(annotations=READ_ONLY)
    def run_query(
        sql: Annotated[str, Field(min_length=1, max_length=20_000)],
        max_rows: Annotated[int, Field(ge=1, le=1_000)] = 100,
    ) -> QueryResult:
        """Run ONE read-only SELECT against the configured schema.

        Writes, DDL and multiple statements are refused, a row limit is always applied,
        and PII columns can only be counted, never read.
        """
        return ctx.query(sql, max_rows=max_rows)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> Response:
        # Liveness only: probing the warehouse on every check would spend warehouse credits.
        return JSONResponse({"status": "ok", "backend": ctx.warehouse.dialect})

    return mcp


def http_app(server: MCPServer, *, allowed_hosts: Sequence[str] = ()) -> Starlette:
    """Streamable HTTP app with stateless JSON responses.

    DNS-rebinding protection stays on even when bound to every interface (as in a
    container): only requests whose Host header names localhost or one of
    ``allowed_hosts`` are served. There is no authentication, so publish the port on
    loopback only, or put an authenticating proxy in front.
    """
    hosts = [*LOCAL_HOSTS, *allowed_hosts]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{h}:*" for h in hosts)],
        allowed_origins=[f"{scheme}://{h}:*" for h in hosts for scheme in ("http", "https")],
    )
    return server.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security
    )
