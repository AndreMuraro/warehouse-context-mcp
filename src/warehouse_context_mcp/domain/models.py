"""Typed results returned by the service layer and exposed as MCP structured output."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

type Cell = str | int | float | bool | None
"""A JSON-safe value. Warehouse values are normalised to this before leaving the service."""

type Row = dict[str, Cell]


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TableSummary(_Result):
    table: str
    description: str
    owner: str | None


class ColumnDescription(_Result):
    name: str
    type: str
    nullable: bool
    description: str
    pii: bool


class TableDescription(_Result):
    table: str
    description: str
    owner: str | None
    grain: str | None
    row_count: int
    columns: list[ColumnDescription]
    undocumented_columns: list[str]


class Lineage(_Result):
    table: str
    upstream: list[str]
    downstream: list[str]


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class Freshness(_Result):
    table: str
    status: FreshnessStatus
    latest: datetime | None = None
    age_hours: float | None = None
    expected_within_hours: float | None = None
    reason: str | None = None


class QueryAccepted(_Result):
    ok: Literal[True] = True
    sql_executed: str
    row_limit: int
    rows: list[Row]


class QueryRefused(_Result):
    """Returned, not raised, so the agent can read the reason and repair its query."""

    ok: Literal[False] = False
    error: str


type QueryResult = QueryAccepted | QueryRefused
