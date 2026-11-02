"""The service layer: combines physical facts (adapter) with curated meaning (catalog).

Kept separate from the MCP transport so it can be tested and reused without a server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .adapters import Warehouse
from .catalog import Catalog
from .guardrails import QueryRejected, guard, mask_rows


@dataclass
class Limits:
    max_rows: int = 200
    sample_rows: int = 5
    timeout_s: float = 10.0


class WarehouseContext:
    def __init__(self, warehouse: Warehouse, catalog: Catalog, limits: Limits | None = None):
        self.wh = warehouse
        self.catalog = catalog
        self.limits = limits or Limits()

    def _pii_map(self) -> dict[str, set[str]]:
        return {t: self.catalog.pii_columns(t) for t in self.wh.list_tables()}

    def list_tables(self) -> list[dict[str, Any]]:
        out = []
        for name in self.wh.list_tables():
            doc = self.catalog.table(name)
            out.append({"table": name, "description": doc.description, "owner": doc.owner})
        return out

    def describe_table(self, table: str) -> dict[str, Any]:
        doc = self.catalog.table(table)
        cols = []
        for col in self.wh.columns(table):
            cdoc = doc.columns.get(col.name)
            cols.append(
                {
                    "name": col.name,
                    "type": col.data_type,
                    "nullable": col.nullable,
                    "description": cdoc.description if cdoc else "",
                    "pii": bool(cdoc and cdoc.pii),
                }
            )
        undocumented = [c["name"] for c in cols if not c["description"]]
        return {
            "table": table.lower(),
            "description": doc.description,
            "owner": doc.owner,
            "grain": doc.grain,
            "row_count": self.wh.row_count(table),
            "columns": cols,
            "undocumented_columns": undocumented,
        }

    def lineage(self, table: str, depth: int = 3) -> dict[str, Any]:
        self.wh.columns(table)  # validates the table exists
        return {"table": table.lower(), **self.catalog.lineage(table, depth=depth)}

    def freshness(self, table: str, now: datetime | None = None) -> dict[str, Any]:
        doc = self.catalog.table(table)
        if not doc.freshness_column:
            return {
                "table": table.lower(),
                "status": "unknown",
                "reason": "no freshness_column documented in the catalog",
            }
        col = doc.freshness_column.lower()
        if col not in {c.name for c in self.wh.columns(table)}:
            return {
                "table": table.lower(),
                "status": "unknown",
                "reason": f"freshness_column {col!r} not found in table",
            }
        res = self.wh.run(f'select max("{col}") from "{table.lower()}"', self.limits.timeout_s)
        latest = res.rows[0][0] if res.rows else None
        if not isinstance(latest, datetime):
            return {
                "table": table.lower(),
                "status": "unknown",
                "reason": "freshness column is empty or not a timestamp",
            }
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        # Naive warehouse timestamps are treated as UTC, the common warehouse convention.
        latest_aware = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        age = now - latest_aware
        expected = doc.expected_freshness
        status = "unknown" if expected is None else ("fresh" if age <= expected else "stale")
        return {
            "table": table.lower(),
            "latest": latest_aware.isoformat(),
            "age_hours": round(age.total_seconds() / 3600, 2),
            "expected_within_hours": doc.expected_freshness_hours,
            "status": status,
        }

    def sample_rows(self, table: str, n: int | None = None) -> dict[str, Any]:
        self.wh.columns(table)
        n = min(n or self.limits.sample_rows, self.limits.sample_rows)
        return self.query(f'select * from "{table.lower()}"', max_rows=n)

    def query(self, sql: str, max_rows: int | None = None) -> dict[str, Any]:
        limit = min(max_rows or self.limits.max_rows, self.limits.max_rows)
        try:
            guarded = guard(sql, dialect=self.wh.dialect, max_rows=limit, pii=self._pii_map())
        except QueryRejected as err:
            return {"ok": False, "error": str(err)}
        unknown = guarded.tables - set(self.wh.list_tables())
        if unknown:
            return {"ok": False, "error": f"unknown tables: {sorted(unknown)}"}
        result = self.wh.run(guarded.sql, self.limits.timeout_s)
        pii = set().union(*(self.catalog.pii_columns(t) for t in guarded.tables))
        return {
            "ok": True,
            "sql_executed": guarded.sql,
            "row_limit": limit,
            "rows": mask_rows(result.columns, result.rows, pii),
        }
