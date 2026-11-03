"""Read-only, in-scope, PII-safe SQL by construction: everything not allowed is refused.

The policy parses the query with sqlglot and accepts it only if:

1. it is exactly one SELECT (or set operation of SELECTs);
2. every node in the tree is a construct on :data:`ALLOWED_NODES` and every function is on
   :data:`ALLOWED_FUNCTIONS`, so table functions, ``LATERAL``, ``SUMMARIZE``, vendor
   functions (``SYSTEM$…``, ``READ_FILES``, ``SECRET``, ``GETENV``…) and anything sqlglot
   cannot model are refused without having to be known in advance;
3. every table is a plain table inside the configured namespace, compared after the
   dialect's identifier normalisation;
4. every column resolves, through ``*`` expansion, aliases, CTEs and subqueries, to a real
   column of a known table;
5. PII columns are never read: in a select list they are replaced by a masking literal
   *in the SQL*, so the value never leaves the warehouse; anywhere else (filters, sorting,
   grouping, joins, function arguments) the query is refused, except a bare ``COUNT(col)``
   or ``COUNT(DISTINCT col)``.

The accepted query is rewritten fully qualified, with masking applied and a row limit.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from warehouse_context_mcp.domain.errors import QueryRejectedError
from warehouse_context_mcp.domain.namespace import Namespace

from .masking import MASK

type PiiMap = Mapping[str, frozenset[str]]
"""Table name to the names of its PII columns, all lower-case."""

type ColumnLookup = Callable[[str], Mapping[str, str] | None]
"""Columns (name to type, lower-case) of a known table, or ``None`` if it is not known."""

ALLOWED_NODES: frozenset[type[exp.Expression]] = frozenset({
    # query structure
    exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.With, exp.CTE,
    exp.From, exp.Join, exp.Where, exp.Group, exp.Having, exp.Order, exp.Ordered,
    exp.Limit, exp.Offset, exp.Distinct, exp.Window, exp.WindowSpec, exp.Filter,
    # references and values
    exp.Table, exp.TableAlias, exp.Column, exp.Identifier, exp.Alias, exp.Star,
    exp.Literal, exp.Boolean, exp.Null, exp.Interval, exp.Var, exp.DataType,
    exp.DataTypeParam, exp.Cast, exp.TryCast, exp.Tuple, exp.Paren,
    # operators and predicates
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod, exp.Neg, exp.DPipe,
    exp.EQ, exp.NEQ, exp.NullSafeEQ, exp.NullSafeNEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.And, exp.Or, exp.Not, exp.Is, exp.In, exp.Between, exp.Like, exp.ILike,
    exp.Exists, exp.Case, exp.If,
})  # fmt: skip

_FUNCTION_NAMES = (
    # aggregates
    "Count", "Sum", "Avg", "Min", "Max", "Stddev", "StddevPop", "StddevSamp", "Variance",
    "VariancePop", "ApproxDistinct", "Median", "Quantile", "PercentileCont", "PercentileDisc",
    "CountIf",
    # math
    "Abs", "Round", "Floor", "Ceil", "Sqrt", "Pow", "Ln", "Log", "Exp", "Sign", "Trunc",
    # text
    "Lower", "Upper", "Length", "Substring", "Trim", "Concat", "ConcatWs", "Left", "Right",
    "StrPosition", "Initcap", "Replace", "StartsWith", "EndsWith", "Contains",
    # conditional
    "Coalesce", "Nullif", "Greatest", "Least",
    # dates
    "CurrentDate", "CurrentTimestamp", "CurrentTime", "DateTrunc", "TimestampTrunc", "Extract",
    "DateAdd", "DateSub", "DateDiff", "TimestampDiff", "TimestampAdd", "Year", "Month", "Day",
    "Quarter", "Week", "DayOfWeek", "DayOfMonth", "DayOfYear", "Date", "TsOrDsToDate", "StrToDate",
    "LastDay",
    # window
    "RowNumber", "Rank", "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "NthValue",
    "Ntile", "PercentRank", "CumeDist",
)  # fmt: skip

ALLOWED_FUNCTIONS: frozenset[type[exp.Func]] = frozenset(
    getattr(exp, name) for name in _FUNCTION_NAMES if hasattr(exp, name)
)
"""Pure functions over values. Anything else, notably ``exp.Anonymous`` (functions sqlglot
does not model, which includes every vendor-specific system function), is refused."""

READ_ROOTS: tuple[type[exp.Query], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)


@dataclass(frozen=True, slots=True)
class GuardedQuery:
    sql: str
    """The rewritten SQL that will actually run."""
    tables: frozenset[str]
    """Physical tables the query reads, lower-case."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryPolicy:
    columns: ColumnLookup
    dialect: str = "duckdb"
    max_rows: int = 200
    pii: PiiMap = field(default_factory=dict)
    namespace: Namespace = field(default_factory=lambda: Namespace(schema="main"))

    def __post_init__(self) -> None:
        if self.max_rows < 1:
            msg = "max_rows must be positive"
            raise ValueError(msg)

    def check(self, sql: str) -> GuardedQuery:
        """Return the rewritten query, or raise :class:`QueryRejectedError`."""
        tree = self._parse_single_read(sql)
        _require_allowed_constructs(tree)
        tree = self._qualify(tree)
        # Again after qualification, which can turn references into other node types (a
        # bare table alias becomes a whole-row TableColumn, for example).
        _require_allowed_constructs(tree)
        tables = self._require_known_tables_in_namespace(tree)
        self._mask_pii_projections(tree)
        self._require_resolved_columns_and_no_pii_reads(tree)
        limited = _apply_limit(tree, self.max_rows)
        return GuardedQuery(sql=limited.sql(dialect=self.dialect), tables=tables)

    # -- steps --------------------------------------------------------------------------

    def _parse_single_read(self, sql: str) -> exp.Query:
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except ParseError as err:
            msg = f"could not parse SQL: {err}"
            raise QueryRejectedError(msg) from err
        if len(statements) != 1:
            msg = "exactly one statement is allowed"
            raise QueryRejectedError(msg)
        tree = statements[0]
        if not isinstance(tree, READ_ROOTS):
            msg = f"only SELECT queries are allowed, got {tree.key.upper()}"
            raise QueryRejectedError(msg)
        return tree

    def _qualify(self, tree: exp.Query) -> exp.Query:
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        referenced = {t.name.lower() for t in tree.find_all(exp.Table)} - ctes
        tables: dict[str, Mapping[str, str]] = {}
        for name in sorted(referenced):
            columns = self.columns(name)
            if columns is None:
                msg = f"unknown tables: [{name!r}]"
                raise QueryRejectedError(msg)
            tables[name] = columns
        nested: dict[str, object] = {self.namespace.schema: tables}
        if self.namespace.catalog:
            nested = {self.namespace.catalog: nested}
        schema = MappingSchema(nested, dialect=self.dialect, normalize=True) if tables else None
        try:
            qualified = qualify(
                tree,
                dialect=self.dialect,
                schema=schema,
                db=self._norm(self.namespace.schema),
                catalog=self._norm(self.namespace.catalog) if self.namespace.catalog else None,
                validate_qualify_columns=True,
            )
        except OptimizeError as err:
            msg = f"could not resolve the query against the schema: {err}"
            raise QueryRejectedError(msg) from err
        if not isinstance(qualified, exp.Query):  # pragma: no cover - qualify keeps the root
            msg = "only SELECT queries are allowed"
            raise QueryRejectedError(msg)
        return qualified

    def _require_known_tables_in_namespace(self, tree: exp.Query) -> frozenset[str]:
        schema = self._norm(self.namespace.schema)
        catalog = self._norm(self.namespace.catalog) if self.namespace.catalog else ""
        names: set[str] = set()
        for table in _physical_tables(tree):
            if table.db != schema or table.catalog != catalog:
                msg = f"table {table.sql()!r} is outside the configured schema {self.namespace}"
                raise QueryRejectedError(msg)
            if self.columns(table.name.lower()) is None:
                msg = f"unknown tables: [{table.name.lower()!r}]"
                raise QueryRejectedError(msg)
            names.add(table.name.lower())
        return frozenset(names)

    def _mask_pii_projections(self, tree: exp.Query) -> None:
        for scope in traverse_scope(tree):
            select = scope.expression
            if not isinstance(select, exp.Select):
                continue
            for projection in list(select.expressions):
                value = projection.this if isinstance(projection, exp.Alias) else projection
                if isinstance(value, exp.Column) and self._is_pii(scope, value):
                    projection.replace(
                        exp.alias_(exp.Literal.string(MASK), projection.alias_or_name, quoted=True)
                    )

    def _require_resolved_columns_and_no_pii_reads(self, tree: exp.Query) -> None:
        # Qualification gives every real column a source. A reference left without one is
        # something else, such as a table alias used as a whole-row value (SELECT t FROM t).
        scopes = {id(scope.expression): scope for scope in traverse_scope(tree)}
        for column in tree.find_all(exp.Column):
            if not column.table and not self._is_unambiguous_output_name(column, scopes):
                msg = (
                    f"{column.sql()!r} does not resolve to a table column, or is an output "
                    "name in ORDER BY that is also a table column; rename the output"
                )
                raise QueryRejectedError(msg)
        for scope in traverse_scope(tree):
            for column in scope.columns:
                self._check_column(scope, column)
            for star in scope.expression.find_all(exp.Star):
                if not isinstance(star.parent, exp.Count):
                    msg = "SELECT * could not be expanded against the schema"
                    raise QueryRejectedError(msg)

    def _check_column(self, scope: Scope, column: exp.Column) -> None:
        source = _source(scope, column.table)
        if source is None:
            msg = f"column {column.sql()!r} does not resolve to a table column"
            raise QueryRejectedError(msg)
        if isinstance(source, exp.Table):
            known = self.columns(source.name.lower()) or {}
            if column.name.lower() not in known:
                msg = f"unknown column {column.sql()!r}"
                raise QueryRejectedError(msg)
        if self._is_pii(scope, column) and not _is_bare_count_argument(column):
            msg = (
                f"column {column.name!r} is marked PII; it can be selected (masked) or "
                "counted with COUNT(col) / COUNT(DISTINCT col), nothing else"
            )
            raise QueryRejectedError(msg)

    def _is_unambiguous_output_name(self, column: exp.Column, scopes: Mapping[int, Scope]) -> bool:
        """ORDER BY may name the query's own outputs, which carry no source table.

        Only when the name cannot also mean a column of a table in that query: engines
        bind such a name, at least inside expressions, to the table column, so
        ``SELECT id AS email ... ORDER BY email LIKE 'a%'`` would sort by the real
        ``email``. sqlglot reads it as the alias, so ambiguity is refused outright.
        """
        order = column.find_ancestor(exp.Order, exp.Select)
        if not isinstance(order, exp.Order):
            return False
        query = order.parent
        if not isinstance(query, exp.Query) or column.name not in query.named_selects:
            return False
        scope = scopes.get(id(query))
        if scope is None:
            return False
        name = column.name.lower()
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and name in (self.columns(source.name.lower()) or {}):
                return False
        return True

    # -- helpers ------------------------------------------------------------------------

    def _is_pii(self, scope: Scope, column: exp.Column) -> bool:
        source = _source(scope, column.table)
        if not isinstance(source, exp.Table):
            # Derived tables and CTEs: their projections were masked or refused already.
            return False
        return column.name.lower() in self.pii.get(source.name.lower(), frozenset())

    def _norm(self, name: str | None) -> str:
        identifier = normalize_identifiers(exp.to_identifier(name or ""), dialect=self.dialect)
        return identifier.name


def _require_allowed_constructs(tree: exp.Query) -> None:
    for node in tree.walk():
        if isinstance(node, exp.Func) and type(node) not in ALLOWED_NODES:
            if type(node) not in ALLOWED_FUNCTIONS:
                name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                msg = f"function {name.upper()} is not allowed"
                raise QueryRejectedError(msg)
            continue
        if type(node) not in ALLOWED_NODES:
            msg = f"forbidden construct: {node.key.upper()}"
            raise QueryRejectedError(msg)
        _require_plain(node)


def _require_plain(node: exp.Expr) -> None:
    """Refuse the allowed node types in the forms that would sidestep the other checks."""
    match node:
        case exp.Table() if not isinstance(node.this, exp.Identifier) or node.args.get("laterals"):
            msg = "only plain table references are allowed"
            raise QueryRejectedError(msg)
        case exp.TableAlias() if node.columns:
            msg = "column alias lists (AS t(a, b)) are not allowed"
            raise QueryRejectedError(msg)
        case exp.Star() if any(node.args.get(k) for k in ("except", "replace", "rename")):
            msg = "SELECT * modifiers (EXCLUDE, REPLACE, RENAME) are not allowed"
            raise QueryRejectedError(msg)
        case exp.With() if node.args.get("recursive"):
            msg = "recursive CTEs are not allowed"
            raise QueryRejectedError(msg)
        case exp.Join() if node.args.get("method") or node.args.get("kind") in {
            "ASOF",
            "POSITIONAL",
        }:
            msg = f"{node.args.get('method') or node.args.get('kind')} joins are not allowed"
            raise QueryRejectedError(msg)
        case _:
            pass


def _physical_tables(tree: exp.Query) -> list[exp.Table]:
    physical: list[exp.Table] = []
    for scope in traverse_scope(tree):
        physical.extend(s for s in scope.sources.values() if isinstance(s, exp.Table))
    return physical


def _source(scope: Scope, alias: str) -> exp.Table | Scope | None:
    """The table or derived scope a column's qualifier refers to, searching outward."""
    current: Scope | None = scope
    while current is not None:
        if alias and alias in current.sources:
            source = current.sources[alias]
            return source if isinstance(source, exp.Table | Scope) else None
        current = current.parent
    return None


def _is_bare_count_argument(column: exp.Column) -> bool:
    parent = column.parent
    if isinstance(parent, exp.Distinct) and len(parent.expressions) == 1:
        parent = parent.parent
    return isinstance(parent, exp.Count)


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
