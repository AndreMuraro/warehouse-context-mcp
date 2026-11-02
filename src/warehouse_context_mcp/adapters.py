"""Warehouse adapters.

An adapter exposes the physical facts of a warehouse: which tables exist, their columns,
and how to run an already-guarded read-only query. Everything semantic lives in the
catalog. v0.1 ships DuckDB; Snowflake and Databricks Unity Catalog follow the same Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import duckdb


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[object, ...]]


class Warehouse(Protocol):
    dialect: str

    def list_tables(self) -> list[str]: ...

    def columns(self, table: str) -> list[Column]: ...

    def row_count(self, table: str) -> int: ...

    def run(self, sql: str, timeout_s: float) -> QueryResult: ...


class DuckDBWarehouse:
    """Read-only DuckDB connection. The file is opened read_only, a second safety layer."""

    dialect = "duckdb"

    def __init__(self, path: str) -> None:
        self._con = duckdb.connect(path, read_only=True)

    def list_tables(self) -> list[str]:
        rows = self._con.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'main' order by table_name"
        ).fetchall()
        return [str(r[0]).lower() for r in rows]

    def columns(self, table: str) -> list[Column]:
        self._require(table)
        rows = self._con.execute(
            "select column_name, data_type, is_nullable from information_schema.columns "
            "where table_schema = 'main' and lower(table_name) = ? order by ordinal_position",
            [table.lower()],
        ).fetchall()
        return [Column(str(n).lower(), str(t), str(nl) == "YES") for n, t, nl in rows]

    def row_count(self, table: str) -> int:
        self._require(table)
        row = self._con.execute(f'select count(*) from "{table}"').fetchone()
        return int(row[0]) if row else 0

    def run(self, sql: str, timeout_s: float) -> QueryResult:
        # DuckDB has no per-statement timeout; interrupt from a timer thread instead.
        import threading

        timer = threading.Timer(timeout_s, self._con.interrupt)
        timer.start()
        try:
            cur = self._con.execute(sql)
            columns = [d[0] for d in cur.description or []]
            return QueryResult(columns=columns, rows=cur.fetchall())
        finally:
            timer.cancel()

    def _require(self, table: str) -> None:
        # Table names reach f-strings above only after this allow-list check.
        if table.lower() not in self.list_tables():
            raise KeyError(f"unknown table: {table}")
