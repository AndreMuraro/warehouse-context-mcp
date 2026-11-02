"""Warehouse adapters.

An adapter exposes the physical facts of a warehouse: which tables exist, their columns,
the comments and tags already stored there, and how to run an already-guarded read-only
query. Curated meaning lives in the YAML catalog and is merged on top.

Snowflake and Databricks connectors are optional extras and imported lazily.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import duckdb

from .catalog import Catalog, ColumnDoc, TableDoc

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _ident(value: str) -> str:
    """Validate configured identifiers before they are interpolated into catalog SQL."""
    if not _IDENT.fullmatch(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


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
    # (catalog/database, schema) the agent may query; None means "not checked".
    namespace: tuple[str | None, str]

    def list_tables(self) -> list[str]: ...

    def columns(self, table: str) -> list[Column]: ...

    def row_count(self, table: str) -> int: ...

    def run(self, sql: str, timeout_s: float) -> QueryResult: ...

    def native_catalog(self) -> Catalog: ...


class DuckDBWarehouse:
    """Read-only DuckDB connection. The file is opened read_only, a second safety layer."""

    dialect = "duckdb"
    namespace: tuple[str | None, str] = (None, "main")

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
        timer = threading.Timer(timeout_s, self._con.interrupt)
        timer.start()
        try:
            cur = self._con.execute(sql)
            columns = [d[0] for d in cur.description or []]
            return QueryResult(columns=columns, rows=cur.fetchall())
        finally:
            timer.cancel()

    def native_catalog(self) -> Catalog:
        return Catalog()

    def _require(self, table: str) -> None:
        # Table names reach f-strings above only after this allow-list check.
        if table.lower() not in self.list_tables():
            raise KeyError(f"unknown table: {table}")


class _DbApiWarehouse:
    """Shared logic for warehouses reached through a DB-API 2.0 connection.

    Subclasses provide the information-schema SQL. All catalog queries are parameterised;
    table names only reach SQL after an allow-list check against list_tables().
    """

    dialect = ""
    schema: str
    namespace: tuple[str | None, str]

    def __init__(self, connection: Any) -> None:
        self._con = connection
        self._tables: list[str] | None = None

    # -- helpers -------------------------------------------------------------------------
    def _fetch(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        cur = self._con.cursor()
        try:
            cur.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()]
        finally:
            cur.close()

    def _require(self, table: str) -> str:
        name = table.lower()
        if name not in self.list_tables():
            raise KeyError(f"unknown table: {table}")
        return name

    # -- subclass hooks ------------------------------------------------------------------
    def _tables_sql(self) -> str:
        raise NotImplementedError

    def _columns_sql(self) -> str:
        raise NotImplementedError

    def _pii_columns(self) -> dict[str, set[str]]:
        return {}

    def _cancel(self, cursor: Any) -> Callable[[], None]:
        return lambda: cursor.cancel()

    # -- Warehouse protocol --------------------------------------------------------------
    def list_tables(self) -> list[str]:
        if self._tables is None:
            self._tables = sorted(str(r[0]).lower() for r in self._fetch(self._tables_sql()))
        return self._tables

    def columns(self, table: str) -> list[Column]:
        name = self._require(table)
        rows = self._fetch(self._columns_sql(), (name,))
        return [Column(str(n).lower(), str(t), str(nl).upper() == "YES") for n, t, nl, _ in rows]

    def row_count(self, table: str) -> int:
        name = self._require(table)
        row = self._fetch(f"select count(*) from {self.schema}.{name}")
        return int(row[0][0]) if row else 0

    def run(self, sql: str, timeout_s: float) -> QueryResult:
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, self._cancel(cur))
        timer.start()
        try:
            cur.execute(sql)
            columns = [d[0].lower() for d in cur.description or []]
            return QueryResult(columns=columns, rows=[tuple(r) for r in cur.fetchall()])
        finally:
            timer.cancel()
            cur.close()

    def native_catalog(self) -> Catalog:
        tables: dict[str, TableDoc] = {}
        for name, comment in self._fetch(self._table_comments_sql()):
            tables[str(name).lower()] = TableDoc(description=comment or "")
        for table, col, comment in self._fetch(self._column_comments_sql()):
            doc = tables.setdefault(str(table).lower(), TableDoc())
            doc.columns[str(col).lower()] = ColumnDoc(description=comment or "")
        for table, cols in self._pii_columns().items():
            doc = tables.setdefault(table, TableDoc())
            for col in cols:
                doc.columns.setdefault(col, ColumnDoc()).pii = True
        return Catalog(tables=tables)

    def _table_comments_sql(self) -> str:
        raise NotImplementedError

    def _column_comments_sql(self) -> str:
        raise NotImplementedError


class SnowflakeWarehouse(_DbApiWarehouse):
    """Snowflake via snowflake-connector-python.

    Connect with a role that only has SELECT (see examples/snowflake_demo.sql, WCM_READER):
    that role is the connection-level read-only layer.
    """

    dialect = "snowflake"

    def __init__(self, connection: Any, database: str, schema: str) -> None:
        super().__init__(connection)
        self.database = _ident(database).upper()
        self.schema_name = _ident(schema).upper()
        self.schema = f"{self.database}.{self.schema_name}"
        self.namespace = (self.database.lower(), self.schema_name.lower())

    @classmethod
    def from_env(cls) -> SnowflakeWarehouse:
        import snowflake.connector  # optional extra: pip install .[snowflake]

        kwargs: dict[str, Any] = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "role": os.environ.get("SNOWFLAKE_ROLE", "WCM_READER"),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "WCM_XS"),
            "database": os.environ.get("SNOWFLAKE_DATABASE", "WCM_DEMO"),
            "schema": os.environ.get("SNOWFLAKE_SCHEMA", "SHIPPING"),
            "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": 60, "QUERY_TAG": "wcm"},
        }
        if key_path := os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            kwargs["private_key_file"] = key_path
        elif password := os.environ.get("SNOWFLAKE_PASSWORD"):
            kwargs["password"] = password
        else:
            kwargs["authenticator"] = "externalbrowser"
        con = snowflake.connector.connect(**kwargs)
        return cls(con, kwargs["database"], kwargs["schema"])

    def _tables_sql(self) -> str:
        return (
            f"select table_name from {self.database}.information_schema.tables "
            f"where table_schema = '{self.schema_name}' and table_type = 'BASE TABLE'"
        )

    def _columns_sql(self) -> str:
        return (
            "select column_name, data_type, is_nullable, comment "
            f"from {self.database}.information_schema.columns "
            f"where table_schema = '{self.schema_name}' and lower(table_name) = %s "
            "order by ordinal_position"
        )

    def _table_comments_sql(self) -> str:
        return (
            f"select table_name, comment from {self.database}.information_schema.tables "
            f"where table_schema = '{self.schema_name}'"
        )

    def _column_comments_sql(self) -> str:
        return (
            "select table_name, column_name, comment "
            f"from {self.database}.information_schema.columns "
            f"where table_schema = '{self.schema_name}' and comment is not null"
        )

    def _pii_columns(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for table in self.list_tables():
            try:
                rows = self._fetch(
                    "select column_name, tag_name, tag_value from table("
                    f"{self.database}.information_schema.tag_references_all_columns("
                    "%s, 'table'))",
                    (f"{self.schema}.{table}",),
                )
            except Exception:  # tagging needs Enterprise edition; degrade gracefully
                return out
            for col, tag, value in rows:
                if str(tag).lower() == "pii" and str(value).lower() in {"true", "yes", "1"}:
                    out.setdefault(table, set()).add(str(col).lower())
        return out

    def _cancel(self, cursor: Any) -> Callable[[], None]:
        def cancel() -> None:
            if getattr(cursor, "sfqid", None):
                self._fetch("select system$cancel_query(%s)", (cursor.sfqid,))

        return cancel


class DatabricksWarehouse(_DbApiWarehouse):
    """Databricks SQL warehouse via databricks-sql-connector, reading Unity Catalog.

    The token's principal should hold only USE CATALOG, USE SCHEMA and SELECT: that grant
    set is the connection-level read-only layer.
    """

    dialect = "databricks"

    def __init__(self, connection: Any, catalog: str, schema: str) -> None:
        super().__init__(connection)
        self.catalog = _ident(catalog).lower()
        self.schema_name = _ident(schema).lower()
        self.schema = f"{self.catalog}.{self.schema_name}"
        self.namespace = (self.catalog, self.schema_name)

    @classmethod
    def from_env(cls) -> DatabricksWarehouse:
        from databricks import sql  # optional extra: pip install .[databricks]

        catalog = os.environ.get("DATABRICKS_CATALOG", "workspace")
        schema = os.environ.get("DATABRICKS_SCHEMA", "wcm_demo")
        con = sql.connect(
            server_hostname=os.environ["DATABRICKS_HOST"].removeprefix("https://"),
            http_path=os.environ["DATABRICKS_HTTP_PATH"],
            access_token=os.environ["DATABRICKS_TOKEN"],
        )
        return cls(con, catalog, schema)

    def _tables_sql(self) -> str:
        return (
            f"select table_name from {self.catalog}.information_schema.tables "
            f"where table_schema = '{self.schema_name}'"
        )

    def _columns_sql(self) -> str:
        return (
            "select column_name, data_type, is_nullable, comment "
            f"from {self.catalog}.information_schema.columns "
            f"where table_schema = '{self.schema_name}' and lower(table_name) = :t "
            "order by ordinal_position"
        )

    def columns(self, table: str) -> list[Column]:
        name = self._require(table)
        cur = self._con.cursor()
        try:
            cur.execute(self._columns_sql(), {"t": name})
            rows = cur.fetchall()
        finally:
            cur.close()
        return [Column(str(n).lower(), str(t), str(nl).upper() == "YES") for n, t, nl, _ in rows]

    def _table_comments_sql(self) -> str:
        return (
            f"select table_name, comment from {self.catalog}.information_schema.tables "
            f"where table_schema = '{self.schema_name}'"
        )

    def _column_comments_sql(self) -> str:
        return (
            "select table_name, column_name, comment "
            f"from {self.catalog}.information_schema.columns "
            f"where table_schema = '{self.schema_name}' and comment is not null"
        )

    def _pii_columns(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        try:
            rows = self._fetch(
                "select table_name, column_name, tag_value "
                f"from {self.catalog}.information_schema.column_tags "
                f"where schema_name = '{self.schema_name}' and lower(tag_name) = 'pii'"
            )
        except Exception:
            return out
        for table, col, value in rows:
            if str(value).lower() in {"true", "yes", "1", ""}:
                out.setdefault(str(table).lower(), set()).add(str(col).lower())
        return out
