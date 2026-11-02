"""DuckDB adapter: a local file opened read-only."""

from pathlib import Path
from typing import Self

import duckdb

from warehouse_context_mcp.domain.catalog import Catalog
from warehouse_context_mcp.domain.errors import UnknownTableError
from warehouse_context_mcp.domain.namespace import Namespace

from .base import Column, ResultSet, deadline


class DuckDBWarehouse:
    """Opening the file with ``read_only=True`` is the connection-level read-only layer."""

    dialect = "duckdb"
    namespace = Namespace(schema="main")

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._con = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        return cls(duckdb.connect(str(path), read_only=True))

    def list_tables(self) -> list[str]:
        rows = self._con.execute(
            "select table_name from information_schema.tables where table_schema = ?",
            [self.namespace.schema],
        ).fetchall()
        return sorted(str(name).lower() for (name,) in rows)

    def columns(self, table: str) -> list[Column]:
        name = self._require(table)
        rows = self._con.execute(
            "select column_name, data_type, is_nullable from information_schema.columns "
            "where table_schema = ? and lower(table_name) = ? order by ordinal_position",
            [self.namespace.schema, name],
        ).fetchall()
        return [Column(str(n).lower(), str(t), str(null) == "YES") for n, t, null in rows]

    def row_count(self, table: str) -> int:
        name = self._require(table)  # allow-listed, so safe to interpolate
        row = self._con.execute(f'select count(*) from main."{name}"').fetchone()  # noqa: S608
        return int(row[0]) if row else 0

    def run(self, sql: str, timeout_s: float) -> ResultSet:
        # DuckDB has no statement timeout; interrupt the connection from a timer instead.
        with deadline(timeout_s, self._con.interrupt):
            cursor = self._con.execute(sql)
            columns = tuple(str(d[0]) for d in cursor.description or ())
            return ResultSet(columns=columns, rows=[tuple(r) for r in cursor.fetchall()])

    def native_catalog(self) -> Catalog:
        return Catalog()

    def close(self) -> None:
        self._con.close()

    def _require(self, table: str) -> str:
        name = table.lower()
        if name not in self.list_tables():
            raise UnknownTableError(table)
        return name
