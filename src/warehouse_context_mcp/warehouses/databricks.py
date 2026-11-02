"""Databricks SQL warehouse adapter reading Unity Catalog (``databricks`` extra).

The token's principal should hold only USE CATALOG, USE SCHEMA and SELECT: that grant set
is the connection-level read-only layer. A personal token on the Free Edition belongs to
the workspace admin, so there only the query policy applies.
"""

# Catalog SQL interpolates only identifiers checked by validate_identifier; values are bound.
# ruff: noqa: S608

from typing import Self, override

from warehouse_context_mcp.domain.errors import ConfigurationError
from warehouse_context_mcp.domain.namespace import Namespace
from warehouse_context_mcp.settings import DatabricksSettings

from .base import validate_identifier
from .dbapi import Connection, DbApiWarehouse, Named, Statement

_TRUTHY = frozenset({"true", "yes", "1", ""})  # a bare `pii` tag counts


class DatabricksWarehouse(DbApiWarehouse[Named]):
    dialect = "databricks"

    def __init__(self, connection: Connection[Named], catalog: str, schema: str) -> None:
        self._catalog = validate_identifier(catalog).lower()
        self._schema = validate_identifier(schema).lower()
        super().__init__(connection, Namespace(schema=schema, catalog=catalog))

    @classmethod
    def connect(cls, settings: DatabricksSettings) -> Self:
        try:
            from databricks import sql  # noqa: PLC0415  (optional dependency)
        except ImportError as err:
            msg = "the Databricks backend needs the 'databricks' extra (uv sync --extra databricks)"
            raise ConfigurationError(msg) from err

        connection = sql.connect(
            server_hostname=settings.host,
            http_path=settings.http_path,
            access_token=settings.token.get_secret_value(),
            catalog=settings.catalog,
            schema=settings.schema_name,
        )
        return cls(connection, settings.catalog, settings.schema_name)

    @override
    def _qualified(self, table: str) -> str:
        return f"{self._catalog}.{self._schema}.{table}"

    @override
    def _tables_statement(self) -> Statement[Named]:
        return Statement(
            f"select table_name from {self._catalog}.information_schema.tables "
            "where table_schema = :schema",
            {"schema": self._schema},
        )

    @override
    def _columns_statement(self, table: str) -> Statement[Named]:
        return Statement(
            "select column_name, data_type, is_nullable "
            f"from {self._catalog}.information_schema.columns "
            "where table_schema = :schema and lower(table_name) = :table "
            "order by ordinal_position",
            {"schema": self._schema, "table": table},
        )

    @override
    def _table_comments_statement(self) -> Statement[Named]:
        return Statement(
            f"select table_name, comment from {self._catalog}.information_schema.tables "
            "where table_schema = :schema",
            {"schema": self._schema},
        )

    @override
    def _column_comments_statement(self) -> Statement[Named]:
        return Statement(
            "select table_name, column_name, comment "
            f"from {self._catalog}.information_schema.columns "
            "where table_schema = :schema and comment is not null",
            {"schema": self._schema},
        )

    @override
    def _pii_columns(self) -> dict[str, set[str]]:
        pii: dict[str, set[str]] = {}
        try:
            rows = self._fetch(
                Statement(
                    "select table_name, column_name, tag_value "
                    f"from {self._catalog}.information_schema.column_tags "
                    "where schema_name = :schema and lower(tag_name) = 'pii'",
                    {"schema": self._schema},
                )
            )
        except Exception:  # noqa: BLE001  column tags need Unity Catalog; degrade gracefully
            return pii
        for table, column, value in rows:
            if str(value or "").lower() in _TRUTHY:
                pii.setdefault(str(table).lower(), set()).add(str(column).lower())
        return pii
