"""Guardrails applied to every query an agent sends.

Read-only is enforced by parsing, not by string matching: the SQL must parse into exactly one
statement whose root is a SELECT (or a set operation of SELECTs), and no node in the tree may
be a write, DDL, or session-altering expression. A row limit is always applied, and columns
marked as PII in the catalog are masked in results.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # anything sqlglot cannot model, e.g. COPY, ATTACH, PRAGMA, SET
    exp.Into,  # SELECT ... INTO
)

READ_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)

MASK = "***"


class QueryRejected(ValueError):
    """Raised when a query is not provably read-only."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    tables: frozenset[str]
    # (catalog, schema, table) as written, lower-cased; empty strings when unqualified.
    qualified: frozenset[tuple[str, str, str]] = frozenset()


def guard(
    sql: str,
    *,
    dialect: str = "duckdb",
    max_rows: int = 200,
    pii: dict[str, set[str]] | None = None,
) -> GuardedQuery:
    """Return a rewritten, row-limited query, or raise QueryRejected.

    `pii` maps table name to its PII column names. Explicit references to those columns are
    rejected (aliasing would otherwise defeat result masking), except inside COUNT, which
    reveals no values. `SELECT *` is allowed; its PII columns are masked by `mask_rows`.
    """
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as err:
        raise QueryRejected(f"could not parse SQL: {err}") from err

    if len(statements) != 1:
        raise QueryRejected("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, READ_ROOTS):
        raise QueryRejected(f"only SELECT queries are allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise QueryRejected(f"forbidden construct: {node.key.upper()}")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    physical = [t for t in tree.find_all(exp.Table) if t.name.lower() not in ctes or t.db]
    tables = frozenset(t.name.lower() for t in physical)
    sensitive = set().union(*((pii or {}).get(t, set()) for t in tables)) if tables else set()
    for column in tree.find_all(exp.Column):
        if column.name.lower() in sensitive and not _inside_count(column):
            raise QueryRejected(
                f"column {column.name!r} is marked PII; only COUNT() over it is allowed"
            )

    assert isinstance(tree, exp.Query)  # narrowed by the READ_ROOTS check above
    limited = _apply_limit(tree, max_rows)
    qualified = frozenset((t.catalog.lower(), t.db.lower(), t.name.lower()) for t in physical)
    return GuardedQuery(sql=limited.sql(dialect=dialect), tables=tables, qualified=qualified)


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


def mask_rows(
    columns: list[str], rows: list[tuple[object, ...]], pii_columns: set[str]
) -> list[dict[str, object]]:
    """Convert rows to dicts, replacing values in PII columns."""
    lowered = [c.lower() for c in columns]
    return [
        {
            col: (MASK if low in pii_columns and val is not None else val)
            for col, low, val in zip(columns, lowered, row, strict=True)
        }
        for row in rows
    ]
