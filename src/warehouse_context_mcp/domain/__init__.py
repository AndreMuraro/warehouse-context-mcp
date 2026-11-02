"""Domain model: the curated catalog, result types and errors. No I/O beyond YAML loading."""

from .catalog import Catalog, ColumnDoc, TableDoc
from .errors import ConfigurationError, QueryRejectedError, UnknownTableError, WarehouseContextError
from .models import (
    Cell,
    ColumnDescription,
    Freshness,
    FreshnessStatus,
    Lineage,
    QueryAccepted,
    QueryRefused,
    QueryResult,
    Row,
    TableDescription,
    TableSummary,
)
from .namespace import Namespace

__all__ = [
    "Catalog",
    "Cell",
    "ColumnDescription",
    "ColumnDoc",
    "ConfigurationError",
    "Freshness",
    "FreshnessStatus",
    "Lineage",
    "Namespace",
    "QueryAccepted",
    "QueryRefused",
    "QueryRejectedError",
    "QueryResult",
    "Row",
    "TableDescription",
    "TableDoc",
    "TableSummary",
    "UnknownTableError",
    "WarehouseContextError",
]
