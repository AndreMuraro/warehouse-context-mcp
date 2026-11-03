"""Snowflake adapter (``snowflake`` extra).

Connect with a role that only holds SELECT, such as ``WCM_READER`` from
``examples/snowflake_demo.sql``: that role is the connection-level read-only layer, and
admin roles are refused. Settings come from ``SNOWFLAKE_*`` variables, from a Snowflake CLI
connection (``SNOWFLAKE_CONNECTION_NAME``), or both.
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

ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "ORGADMIN", "SECURITYADMIN", "SYSADMIN", "USERADMIN"})

_SESSION_FIELDS = {
    "account": "account",
    "user": "user",
    "role": "role",
    "warehouse": "warehouse",
    "database": "database",
    "schema_name": "schema",
}


def connect_arguments(settings: SnowflakeSettings) -> dict[str, object]:
    """Keyword arguments for ``snowflake.connector.connect``, kept free of I/O for testing.

    With a connection name, only values actually set in the environment are passed, so they
    override the named connection while this package's defaults never do.
    """
    args: dict[str, object] = {
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": settings.statement_timeout_s,
            "QUERY_TAG": "warehouse-context-mcp",
        },
        # Explicit, because connector 5.x warns when it is left to default. The server
        # never runs async queries, so always log the session out on close.
        "enable_server_session_keep_alive_auto_detection": False,
    }
    if settings.connection_name:
        args["connection_name"] = settings.connection_name
    for field, argument in _SESSION_FIELDS.items():
        if settings.is_set(field) or not settings.connection_name:
            args[argument] = getattr(settings, field)

    if settings.private_key_path:
        args["private_key_file"] = str(settings.private_key_path)
        if settings.private_key_passphrase:
            args["private_key_file_pwd"] = settings.private_key_passphrase.get_secret_value()
    elif settings.password:
        args["password"] = settings.password.get_secret_value()
    elif not settings.connection_name:
        args["authenticator"] = "externalbrowser"
    return args


class SnowflakeWarehouse(DbApiWarehouse[Positional]):
    dialect = "snowflake"

    def __init__(
        self,
        connection: Connection[Positional],
        database: str,
        schema: str,
        *,
        require_pii_tags: bool = True,
    ) -> None:
        self._database = validate_identifier(database).upper()
        self._schema = validate_identifier(schema).upper()
        super().__init__(
            connection,
            Namespace(schema=schema, catalog=database),
            require_pii_tags=require_pii_tags,
        )

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

        # The driver types cursor() as SnowflakeCursor | DictCursor; DictCursor is only
        # returned when cursor_class=DictCursor is passed, which this package never does.
        connection = cast(
            "Connection[Positional]", snowflake.connector.connect(**connect_arguments(settings))
        )
        return cls.from_session(connection, require_pii_tags=settings.require_pii_tags)

    @classmethod
    def from_session(
        cls, connection: Connection[Positional], *, require_pii_tags: bool = True
    ) -> Self:
        """Adopt the role, database and schema the session actually has.

        A Snowflake CLI connection may carry its own defaults, so the session is the source
        of truth. Admin roles are refused: the read-only layer is a SELECT-only role.
        """
        cursor = connection.cursor()
        try:
            # Only the primary role's privileges: secondary roles (often ALL by default)
            # would add every other role the user holds, including write access.
            cursor.execute("use secondary roles none")
            cursor.execute("select current_role(), current_database(), current_schema()")
            role, database, schema = (str(v or "") for v in cursor.fetchall()[0])
        finally:
            cursor.close()
        problem = None
        if role.upper() in ADMIN_ROLES:
            problem = (
                f"the session role is {role}; connect with a SELECT-only role such as "
                "WCM_READER (set SNOWFLAKE_ROLE)"
            )
        elif not (database and schema):
            problem = (
                "the session has no current database and schema; set SNOWFLAKE_DATABASE and "
                "SNOWFLAKE_SCHEMA or add them to the connection"
            )
        if problem:
            connection.close()
            raise ConfigurationError(problem)
        return cls(connection, database, schema, require_pii_tags=require_pii_tags)

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
            rows = self._fetch(
                Statement(
                    "select column_name, tag_name, tag_value from table("
                    f"{self._database}.information_schema.tag_references_all_columns"
                    "(%s, 'table'))",
                    (self._qualified(table),),
                )
            )
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
