"""Read-only enforcement by parsing, not string matching.

A query passes only if it parses into exactly one statement whose root is a SELECT or a
set operation, contains no write, DDL or unmodelled command anywhere in its tree, touches
only tables inside the configured namespace, and does not read PII columns. The accepted
query is rewritten with fully qualified table names and a row limit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from warehouse_context_mcp.domain.errors import QueryRejectedError
from warehouse_context_mcp.domain.namespace import Namespace

type PiiMap = Mapping[str, frozenset[str]]
"""Table name to the names of its PII columns, all lower-case."""

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # anything sqlglot cannot model: COPY, ATTACH, PRAGMA, SET, ...
    exp.Into,  # SELECT ... INTO
)

READ_ROOTS: tuple[type[exp.Query], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)


@dataclass(frozen=True, slots=True)
class GuardedQuery:
    sql: str
    """The rewritten SQL that will actually run."""
    tables: frozenset[str]
    """Physical tables the query reads, lower-case, CTE names excluded."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryPolicy:
    dialect: str = "duckdb"
    max_rows: int = 200
    pii: PiiMap = field(default_factory=dict)
    namespace: Namespace | None = None
    """When set, tables outside it are refused and unqualified tables are qualified."""

    def __post_init__(self) -> None:
        if self.max_rows < 1:
            msg = "max_rows must be positive"
            raise ValueError(msg)

    def check(self, sql: str) -> GuardedQuery:
        """Return the rewritten query, or raise :class:`QueryRejectedError`."""
        tree = self._parse_single_read(sql)
        physical = _physical_tables(tree)
        self._require_in_namespace(physical)
        tables = frozenset(t.name.lower() for t in physical)
        self._forbid_pii_reads(tree, tables)
        if self.namespace is not None:
            _qualify(physical, self.namespace)
        limited = _apply_limit(tree, self.max_rows)
        return GuardedQuery(sql=limited.sql(dialect=self.dialect), tables=tables)

    def _parse_single_read(self, sql: str) -> exp.Query:
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except sqlglot.errors.ParseError as err:
            msg = f"could not parse SQL: {err}"
            raise QueryRejectedError(msg) from err
        if len(statements) != 1:
            msg = "exactly one statement is allowed"
            raise QueryRejectedError(msg)
        tree = statements[0]
        if not isinstance(tree, READ_ROOTS):
            msg = f"only SELECT queries are allowed, got {tree.key.upper()}"
            raise QueryRejectedError(msg)
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                msg = f"forbidden construct: {node.key.upper()}"
                raise QueryRejectedError(msg)
        return tree

    def _require_in_namespace(self, physical: list[exp.Table]) -> None:
        if self.namespace is None:
            return
        for table in physical:
            if not self.namespace.contains(table.catalog, table.db):
                msg = f"table {table.sql()!r} is outside the configured schema {self.namespace}"
                raise QueryRejectedError(msg)

    def _forbid_pii_reads(self, tree: exp.Query, tables: frozenset[str]) -> None:
        """Explicit PII references are refused, since aliasing would defeat result masking.

        COUNT over a PII column reveals no values and is allowed. ``SELECT *`` is allowed;
        its PII columns are masked in the results.
        """
        sensitive = frozenset[str]().union(*(self.pii.get(t, frozenset()) for t in tables))
        for column in tree.find_all(exp.Column):
            if column.name.lower() in sensitive and not _inside_count(column):
                msg = f"column {column.name!r} is marked PII; only COUNT() over it is allowed"
                raise QueryRejectedError(msg)


def guard(
    sql: str,
    *,
    dialect: str = "duckdb",
    max_rows: int = 200,
    pii: PiiMap | None = None,
    namespace: Namespace | None = None,
) -> GuardedQuery:
    """One-shot form of :meth:`QueryPolicy.check`."""
    policy = QueryPolicy(dialect=dialect, max_rows=max_rows, pii=pii or {}, namespace=namespace)
    return policy.check(sql)


def _physical_tables(tree: exp.Query) -> list[exp.Table]:
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return [t for t in tree.find_all(exp.Table) if t.db or t.name.lower() not in ctes]


def _qualify(tables: list[exp.Table], namespace: Namespace) -> None:
    for table in tables:
        if not table.db:
            table.set("db", exp.to_identifier(namespace.schema))
        if namespace.catalog and not table.catalog:
            table.set("catalog", exp.to_identifier(namespace.catalog))


def _inside_count(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Count):
            return True
        parent = parent.parent
    return False


def _apply_limit(tree: exp.Query, max_rows: int) -> exp.Query:
    existing = tree.args.get("limit")
    if isinstance(existing, exp.Limit):
        value = existing.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
            return tree
    if isinstance(tree, exp.Select):
        return tree.limit(max_rows)
    # Set operations: wrap so the limit applies to the combined result.
    return exp.select("*").from_(tree.subquery("q")).limit(max_rows)
