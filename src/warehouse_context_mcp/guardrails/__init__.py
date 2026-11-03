"""Guardrails applied to every query an agent sends: read-only, in scope, PII-safe, bounded."""

from .masking import MASK, TRUNCATED, BoundedRows, mask_rows, to_cell
from .policy import (
    ALLOWED_FUNCTIONS,
    ALLOWED_NODES,
    ColumnLookup,
    GuardedQuery,
    PiiMap,
    QueryPolicy,
)

__all__ = [
    "ALLOWED_FUNCTIONS",
    "ALLOWED_NODES",
    "MASK",
    "TRUNCATED",
    "BoundedRows",
    "ColumnLookup",
    "GuardedQuery",
    "PiiMap",
    "QueryPolicy",
    "mask_rows",
    "to_cell",
]
