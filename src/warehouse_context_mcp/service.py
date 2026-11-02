"""The application layer: warehouse facts, catalog meaning, and the query policy between.

It has no MCP dependency, so it is tested and reusable without a server.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlglot import exp

from .domain.catalog import Catalog, ColumnDoc
from .domain.errors import QueryRejectedError, UnknownTableError
from .domain.models import (
    ColumnDescription,
    Freshness,
    FreshnessStatus,
    Lineage,
    QueryAccepted,
    QueryRefused,
    QueryResult,
    TableDescription,
    TableSummary,
)
from .guardrails import QueryPolicy, mask_rows
from .warehouses import ResultSet, Warehouse


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    max_rows: int = 200
    sample_rows: int = 5
    timeout_s: float = 10.0
    max_lineage_depth: int = 5


@dataclass(frozen=True, slots=True)
class _Executed:
    sql: str
    result: ResultSet
    pii: frozenset[str]


class WarehouseContext:
    def __init__(
        self, warehouse: Warehouse, catalog: Catalog | None = None, limits: Limits | None = None
    ) -> None:
        self.warehouse = warehouse
        # Comments and tags stored in the warehouse, overlaid by the curated catalog.
        self.catalog = warehouse.native_catalog().merged_with(catalog or Catalog())
        self.limits = limits or Limits()

    def list_tables(self) -> list[TableSummary]:
        summaries: list[TableSummary] = []
        for name in self.warehouse.list_tables():
            doc = self.catalog.table(name)
            summaries.append(TableSummary(table=name, description=doc.description, owner=doc.owner))
        return summaries

    def describe_table(self, table: str) -> TableDescription:
        name = self._require(table)
        doc = self.catalog.table(name)
        columns = [
            ColumnDescription(
                name=col.name,
                type=col.data_type,
                nullable=col.nullable,
                description=doc.columns.get(col.name, ColumnDoc()).description,
                pii=col.name in doc.pii_columns,
            )
            for col in self.warehouse.columns(name)
        ]
        return TableDescription(
            table=name,
            description=doc.description,
            owner=doc.owner,
            grain=doc.grain,
            row_count=self.warehouse.row_count(name),
            columns=columns,
            undocumented_columns=[c.name for c in columns if not c.description],
        )

    def lineage(self, table: str, depth: int = 3) -> Lineage:
        name = self._require(table)
        upstream, downstream = self.catalog.lineage(
            name, depth=max(1, min(depth, self.limits.max_lineage_depth))
        )
        return Lineage(table=name, upstream=upstream, downstream=downstream)

    def freshness(self, table: str, now: datetime | None = None) -> Freshness:
        name = self._require(table)
        doc = self.catalog.table(name)
        if not doc.freshness_column:
            return _unknown(name, "no freshness_column documented in the catalog")
        column = doc.freshness_column.lower()
        if column not in {c.name for c in self.warehouse.columns(name)}:
            return _unknown(name, f"freshness_column {column!r} not found in table")

        sql = exp.select(exp.Max(this=exp.column(column))).from_(name)
        match self._execute(sql.sql(dialect=self.warehouse.dialect), max_rows=1):
            case _Executed(result=ResultSet(rows=[(datetime() as latest, *_), *_])):
                pass
            case QueryRefused(error=error):
                return _unknown(name, error)
            case _:
                return _unknown(name, "freshness column is empty or not a timestamp")

        # Naive warehouse timestamps are treated as UTC, the common warehouse convention.
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        now = now if now.tzinfo else now.replace(tzinfo=UTC)
        age = now - latest
        expected = doc.expected_freshness
        if expected is None:
            status = FreshnessStatus.UNKNOWN
        else:
            status = FreshnessStatus.FRESH if age <= expected else FreshnessStatus.STALE
        return Freshness(
            table=name,
            status=status,
            latest=latest,
            age_hours=round(age.total_seconds() / 3600, 2),
            expected_within_hours=doc.expected_freshness_hours,
        )

    def sample_rows(self, table: str, n: int | None = None) -> QueryResult:
        name = self._require(table)
        limit = min(n or self.limits.sample_rows, self.limits.sample_rows)
        return self.query(exp.select("*").from_(name).sql(dialect=self.warehouse.dialect), limit)

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        """Run one guarded query. Refusals come back as data so the agent can repair them."""
        limit = min(max_rows or self.limits.max_rows, self.limits.max_rows)
        match self._execute(sql, limit):
            case QueryRefused() as refused:
                return refused
            case _Executed(sql=executed, result=result, pii=pii):
                return QueryAccepted(
                    sql_executed=executed,
                    row_limit=limit,
                    rows=mask_rows(result.columns, result.rows, pii),
                )

    def _execute(self, sql: str, max_rows: int) -> _Executed | QueryRefused:
        known = self.warehouse.list_tables()
        policy = QueryPolicy(
            dialect=self.warehouse.dialect,
            max_rows=max_rows,
            pii=self.catalog.pii_map(known),
            namespace=self.warehouse.namespace,
        )
        try:
            guarded = policy.check(sql)
        except QueryRejectedError as err:
            return QueryRefused(error=str(err))
        if unknown := guarded.tables - set(known):
            return QueryRefused(error=f"unknown tables: {sorted(unknown)}")

        result = self.warehouse.run(guarded.sql, self.limits.timeout_s)
        pii = frozenset[str]().union(*(policy.pii[t] for t in guarded.tables))
        return _Executed(sql=guarded.sql, result=result, pii=pii)

    def _require(self, table: str) -> str:
        name = table.lower()
        if name not in self.warehouse.list_tables():
            raise UnknownTableError(table)
        return name


def _unknown(table: str, reason: str) -> Freshness:
    return Freshness(table=table, status=FreshnessStatus.UNKNOWN, reason=reason)
