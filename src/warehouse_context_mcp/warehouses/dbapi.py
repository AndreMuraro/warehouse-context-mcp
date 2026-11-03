"""Shared adapter logic for warehouses reached through a DB-API 2.0 (PEP 249) driver.

Subclasses only describe *where* things live (information-schema statements, how PII tags
are stored, how to cancel a running query). Fetching, caching, allow-listing and catalog
assembly happen here once.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from warehouse_context_mcp.domain.catalog import Catalog, ColumnDoc, TableDoc
from warehouse_context_mcp.domain.errors import ConfigurationError, UnknownTableError
from warehouse_context_mcp.domain.namespace import Namespace

from .base import Column, ResultSet, deadline

type Positional = Sequence[object]
"""Parameters for ``qmark``/``format``/``pyformat`` drivers (Snowflake: ``%s``)."""
type Named = Mapping[str, object]
"""Parameters for ``named`` drivers (Databricks: ``:name``)."""
type FetchedRow = Sequence[object]


class Cursor[P](Protocol):
    """The subset of a PEP 249 cursor the adapters use, generic over its paramstyle.

    ``P`` only appears as an argument, so the protocol is contravariant in it: a driver
    that accepts ``Sequence[Any]`` satisfies ``Cursor[Positional]``.
    """

    @property
    def description(self) -> Sequence[Sequence[object]] | None: ...

    def execute(self, operation: str, parameters: P = ..., /) -> object: ...

    def fetchall(self) -> Sequence[FetchedRow]: ...

    def close(self) -> object: ...


class Connection[P](Protocol):
    def cursor(self) -> Cursor[P]: ...

    def close(self) -> object: ...


@dataclass(frozen=True, slots=True)
class Statement[P]:
    """SQL text plus bound parameters in the driver's own paramstyle."""

    sql: str
    params: P | None = None


class DbApiWarehouse[P: (Positional, Named)](ABC):
    """Base adapter for a driver whose parameters have type ``P``."""

    dialect: ClassVar[str]

    def __init__(
        self, connection: Connection[P], namespace: Namespace, *, require_pii_tags: bool = True
    ) -> None:
        self._con = connection
        self.namespace = namespace
        self._require_pii_tags = require_pii_tags
        self._tables: list[str] | None = None

    # -- what subclasses describe ---------------------------------------------------------

    @abstractmethod
    def _tables_statement(self) -> Statement[P]: ...

    @abstractmethod
    def _columns_statement(self, table: str) -> Statement[P]:
        """Rows of (column_name, data_type, is_nullable) in ordinal order."""

    @abstractmethod
    def _table_comments_statement(self) -> Statement[P]:
        """Rows of (table_name, comment)."""

    @abstractmethod
    def _column_comments_statement(self) -> Statement[P]:
        """Rows of (table_name, column_name, comment) for commented columns."""

    @abstractmethod
    def _pii_columns(self) -> dict[str, set[str]]:
        """Table name to columns tagged as PII. Raises if the tags cannot be read."""

    @abstractmethod
    def _qualified(self, table: str) -> str: ...

    def _canceller(self, cursor: Cursor[P]) -> Callable[[], object]:
        cancel: Callable[[], object] | None = getattr(cursor, "cancel", None)
        return cancel or (lambda: None)

    # -- Warehouse protocol -----------------------------------------------------------------

    def list_tables(self) -> list[str]:
        if self._tables is None:
            rows = self._fetch(self._tables_statement())
            self._tables = sorted(str(row[0]).lower() for row in rows)
        return self._tables

    def columns(self, table: str) -> list[Column]:
        rows = self._fetch(self._columns_statement(self._require(table)))
        return [Column(str(n).lower(), str(t), str(null).upper() == "YES") for n, t, null in rows]

    def row_count(self, table: str) -> int:
        name = self._require(table)  # allow-listed, so safe to interpolate
        sql = f"select count(*) from {self._qualified(name)}"  # noqa: S608
        rows = self._fetch(Statement[P](sql))
        return int(str(rows[0][0])) if rows else 0

    def run(self, sql: str, timeout_s: float) -> ResultSet:
        cursor = self._con.cursor()
        try:
            with deadline(timeout_s, self._canceller(cursor)):
                cursor.execute(sql)
                columns = tuple(str(d[0]).lower() for d in cursor.description or ())
                return ResultSet(columns=columns, rows=[tuple(r) for r in cursor.fetchall()])
        finally:
            cursor.close()

    def native_catalog(self) -> Catalog:
        descriptions = {
            str(table).lower(): str(comment or "")
            for table, comment in self._fetch(self._table_comments_statement())
        }
        column_docs: dict[str, dict[str, str]] = {}
        for table, column, comment in self._fetch(self._column_comments_statement()):
            column_docs.setdefault(str(table).lower(), {})[str(column).lower()] = str(comment)
        try:
            pii = self._pii_columns()
        except Exception as err:
            # Fail closed: silently missing tags would mean silently unmasked PII.
            if self._require_pii_tags:
                msg = (
                    "could not read PII tags from the warehouse; fix the grants, or set "
                    "*_REQUIRE_PII_TAGS=false and flag PII columns in the curated catalog"
                )
                raise ConfigurationError(msg) from err
            pii = {}

        tables: dict[str, TableDoc] = {}
        for table in sorted(descriptions.keys() | column_docs.keys() | pii.keys()):
            docs = column_docs.get(table, {})
            tagged = pii.get(table, set())
            tables[table] = TableDoc(
                description=descriptions.get(table, ""),
                columns={
                    col: ColumnDoc(description=docs.get(col, ""), pii=col in tagged)
                    for col in sorted(docs.keys() | tagged)
                },
            )
        return Catalog(tables=tables)

    def refresh(self) -> None:
        self._tables = None

    def close(self) -> None:
        self._con.close()

    # -- helpers ------------------------------------------------------------------------------

    def _fetch(self, statement: Statement[P]) -> list[FetchedRow]:
        cursor = self._con.cursor()
        try:
            if statement.params is None:
                cursor.execute(statement.sql)
            else:
                cursor.execute(statement.sql, statement.params)
            return list(cursor.fetchall())
        finally:
            cursor.close()

    def _require(self, table: str) -> str:
        name = table.lower()
        if name not in self.list_tables():
            raise UnknownTableError(table)
        return name
