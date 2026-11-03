"""Snowflake adapter (``snowflake`` extra).

Connect with a role that only holds SELECT, such as ``WCM_READER`` from
``examples/snowflake_demo.sql``: that role is the connection-level read-only layer.
"""

# Catalog SQL interpolates only identifiers checked by validate_identifier; values are bound.
# ruff: noqa: S608

from collections.abc import Callable
from typing import Self, cast, override

from warehouse_context_mcp.domain.errors import ConfigurationError
from warehouse_context_mcp.domain.namespace import Namespace
from warehouse_context_mcp.settings import SnowflakeSettings

from .base import validate_identifier
from .dbapi import Connection, Cursor, DbApiWarehouse, Positional, Statement

_TRUTHY = frozenset({"true", "yes", "1"})


class SnowflakeWarehouse(DbApiWarehouse[Positional]):
    dialect = "snowflake"

    def __init__(self, connection: Connection[Positional], database: str, schema: str) -> None:
        self._database = validate_identifier(database).upper()
        self._schema = validate_identifier(schema).upper()
        super().__init__(connection, Namespace(schema=schema, catalog=database))

    @classmethod
    def connect(cls, settings: SnowflakeSettings) -> Self:
        try:
            import snowflake.connector  # noqa: PLC0415  (optional dependency)
        except ImportError as err:
            msg = (
                "the Snowflake backend needs the 'snowflake' extra "
                "(uv sync --extra snowflake); it is not yet available on Python 3.15"
            )
            raise ConfigurationError(msg) from err

        auth: dict[str, object]
        if settings.private_key_path:
            auth = {"private_key_file": str(settings.private_key_path)}
        elif settings.password:
            auth = {"password": settings.password.get_secret_value()}
        else:
            auth = {"authenticator": "externalbrowser"}
        connection = snowflake.connector.connect(
            account=settings.account,
            user=settings.user,
            role=settings.role,
            warehouse=settings.warehouse,
            database=settings.database,
            schema=settings.schema_name,
            session_parameters={
                "STATEMENT_TIMEOUT_IN_SECONDS": settings.statement_timeout_s,
                "QUERY_TAG": "warehouse-context-mcp",
            },
            # Explicit, because connector 5.x warns when it is left to default. The server
            # never runs async queries, so always log the session out on close.
            enable_server_session_keep_alive_auto_detection=False,
            **auth,
        )
        # The driver types cursor() as SnowflakeCursor | DictCursor; DictCursor is only
        # returned when cursor_class=DictCursor is passed, which this package never does.
        return cls(
            cast("Connection[Positional]", connection), settings.database, settings.schema_name
        )

    @override
    def _qualified(self, table: str) -> str:
        return f"{self._database}.{self._schema}.{table}"

    @override
    def _tables_statement(self) -> Statement[Positional]:
        return Statement(
            f"select table_name from {self._database}.information_schema.tables "
            "where table_schema = %s and table_type = 'BASE TABLE'",
            (self._schema,),
        )

    @override
    def _columns_statement(self, table: str) -> Statement[Positional]:
        return Statement(
            "select column_name, data_type, is_nullable "
            f"from {self._database}.information_schema.columns "
            "where table_schema = %s and lower(table_name) = %s order by ordinal_position",
            (self._schema, table),
        )

    @override
    def _table_comments_statement(self) -> Statement[Positional]:
        return Statement(
            f"select table_name, comment from {self._database}.information_schema.tables "
            "where table_schema = %s",
            (self._schema,),
        )

    @override
    def _column_comments_statement(self) -> Statement[Positional]:
        return Statement(
            "select table_name, column_name, comment "
            f"from {self._database}.information_schema.columns "
            "where table_schema = %s and comment is not null",
            (self._schema,),
        )

    @override
    def _pii_columns(self) -> dict[str, set[str]]:
        pii: dict[str, set[str]] = {}
        for table in self.list_tables():
            try:
                rows = self._fetch(
                    Statement(
                        "select column_name, tag_name, tag_value from table("
                        f"{self._database}.information_schema.tag_references_all_columns"
                        "(%s, 'table'))",
                        (self._qualified(table),),
                    )
                )
            except Exception:  # noqa: BLE001  tagging needs Enterprise edition; degrade gracefully
                return pii
            for column, tag, value in rows:
                if str(tag).lower() == "pii" and str(value).lower() in _TRUTHY:
                    pii.setdefault(table, set()).add(str(column).lower())
        return pii

    @override
    def _canceller(self, cursor: Cursor[Positional]) -> Callable[[], object]:
        def cancel() -> None:
            if query_id := getattr(cursor, "sfqid", None):
                self._fetch(Statement("select system$cancel_query(%s)", (query_id,)))

        return cancel
