"""The three conditions an agent is evaluated under, all on the same demo database.

- ``sql``: one raw SQL tool on a read-only connection, the usual text-to-SQL setup. The
  agent learns the schema from ``information_schema``.
- ``sql+docs``: the same tool, with the curated catalog pasted into the system prompt.
  It separates what documentation alone buys from what the server's guardrails add.
- ``mcp``: the warehouse-context-mcp tools, called in-process through the MCP server, so
  the agent sees the same schemas and result text a real MCP client receives.
"""

import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import duckdb

from warehouse_context_mcp.domain import Catalog
from warehouse_context_mcp.server import INSTRUCTIONS, build_server
from warehouse_context_mcp.service import WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse

from .providers import ToolCall, ToolSpec
from .scoring import connect_readonly

MAX_ROWS = 100


class ArmName(StrEnum):
    SQL = "sql"
    SQL_DOCS = "sql+docs"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class Outcome:
    content: str
    """What the model is shown."""
    queried: frozenset[str] = frozenset()
    """Tables this call read data from (not metadata lookups)."""
    ok: bool = True


class Arm(Protocol):
    name: ArmName
    system: str

    def tools(self) -> list[ToolSpec]: ...

    def call(self, call: ToolCall) -> Outcome: ...

    def close(self) -> None: ...


def open_arm(name: ArmName, database: Path, catalog: Path) -> Arm:
    match name:
        case ArmName.SQL:
            return RawSqlArm(database, docs=None)
        case ArmName.SQL_DOCS:
            return RawSqlArm(database, docs=catalog.read_text(encoding="utf-8"))
        case ArmName.MCP:
            return McpArm(database, catalog)


def tables_in(sql: str, known: Iterable[str]) -> frozenset[str]:
    """Known table names mentioned in a query, matched as whole words."""
    lowered = sql.lower()
    return frozenset(t for t in known if re.search(rf"\b{re.escape(t)}\b", lowered))


class RawSqlArm:
    def __init__(self, database: Path, *, docs: str | None) -> None:
        self.name = ArmName.SQL_DOCS if docs else ArmName.SQL
        self._con = connect_readonly(database)
        rows = self._con.execute(
            "select table_name from information_schema.tables where table_schema = 'main'"
        ).fetchall()
        self._tables = sorted(str(r[0]) for r in rows)
        self.system = (
            "You have read-only SQL access to a DuckDB data warehouse through the run_sql "
            "tool. Explore the schema with information_schema before answering."
        )
        if docs:
            self.system += f"\n\nThe data team's catalog for this warehouse:\n\n{docs}"

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="run_sql",
                description=(
                    "Run one SQL query against the DuckDB warehouse and return up to "
                    f"{MAX_ROWS} rows as JSON."
                ),
                parameters={
                    "type": "object",
                    "properties": {"sql": {"type": "string", "description": "DuckDB SQL"}},
                    "required": ["sql"],
                },
            )
        ]

    def call(self, call: ToolCall) -> Outcome:
        if call.name != "run_sql":
            return Outcome(f"error: unknown tool {call.name!r}", ok=False)
        sql = str(call.arguments.get("sql", ""))
        queried = tables_in(sql, self._tables)
        try:
            cursor = self._con.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except duckdb.Error as err:
            return Outcome(f"error: {err}", queried, ok=False)
        payload = {
            "columns": columns,
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "truncated": len(rows) > MAX_ROWS,
        }
        return Outcome(json.dumps(payload, default=str), queried)

    def close(self) -> None:
        self._con.close()


class McpArm:
    def __init__(self, database: Path, catalog: Path) -> None:
        self.name = ArmName.MCP
        self._warehouse = DuckDBWarehouse.open(database)
        self._ctx = WarehouseContext(self._warehouse, Catalog.load(catalog))
        self._server = build_server(self._ctx)
        self._tables = self._warehouse.list_tables()
        # MCP clients pass the server's instructions to the model.
        self.system = (
            f"You have tools from a warehouse MCP server. Server instructions: {INSTRUCTIONS}"
        )

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(t.name, t.description or "", dict(t.input_schema))
            for t in asyncio.run(self._server.list_tools())
        ]

    def call(self, call: ToolCall) -> Outcome:
        queried: frozenset[str] = frozenset()
        if call.name == "run_query":
            queried = tables_in(str(call.arguments.get("sql", "")), self._tables)
        elif call.name == "sample_rows":
            queried = tables_in(str(call.arguments.get("table", "")), self._tables)
        try:
            result = asyncio.run(self._server.call_tool(call.name, dict(call.arguments)))
        except Exception as err:  # noqa: BLE001  the model sees the error and can retry
            return Outcome(f"error: {err}", queried, ok=False)
        content: list[Any] = getattr(result, "content", []) or []
        text = "\n".join(getattr(c, "text", "") for c in content)
        structured = getattr(result, "structured_content", None) or {}
        inner = structured.get("result") if isinstance(structured, dict) else None
        refused = isinstance(inner, dict) and inner.get("ok") is False
        return Outcome(text, queried, ok=not getattr(result, "is_error", False) and not refused)

    def close(self) -> None:
        self._warehouse.close()
