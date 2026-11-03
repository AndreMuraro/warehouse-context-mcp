"""Turn raw warehouse rows into bounded, JSON-safe rows with PII values masked.

PII is masked in the SQL itself by the query policy; masking here by column name is a
second layer. Bounding matters because the row limit alone does not bound the response:
one row can hold a 100 MB string or a whole table folded into a list.
"""

import json
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from warehouse_context_mcp.domain.models import Cell, Row

MASK = "***"
TRUNCATED = "…[truncated]"


def to_cell(value: object, max_chars: int | None = None) -> Cell:
    """Normalise a driver value to a JSON-safe scalar without losing integer precision."""
    match value:
        case None | bool() | int() | float():
            return value
        case str():
            return _clip(value, max_chars)
        case Decimal() if value.is_finite():
            exponent = value.as_tuple().exponent
            return int(value) if isinstance(exponent, int) and exponent >= 0 else float(value)
        case datetime() | date() | time():
            return value.isoformat()
        case bytes() | bytearray() | memoryview():
            return f"<{len(value)} bytes>"
        case _:
            return _clip(str(value), max_chars)


def _clip(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATED


@dataclass(frozen=True, slots=True)
class BoundedRows:
    rows: list[Row]
    truncated: bool
    """True when values were shortened or rows dropped to stay within the limits."""


def mask_rows(
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
    pii_columns: AbstractSet[str],
    *,
    max_cell_chars: int | None = None,
    max_bytes: int | None = None,
) -> BoundedRows:
    """Convert rows to dicts keyed by column name, masking PII and enforcing size limits."""
    masked = [c.lower() in pii_columns for c in columns]
    out: list[Row] = []
    size = 2  # the enclosing brackets
    truncated = False
    for raw in rows:
        row: Row = {}
        for col, hide, val in zip(columns, masked, raw, strict=True):
            cell = MASK if hide and val is not None else to_cell(val, max_cell_chars)
            truncated |= isinstance(cell, str) and cell.endswith(TRUNCATED)
            row[col] = cell
        size += len(json.dumps(row, ensure_ascii=False)) + 1
        if max_bytes is not None and size > max_bytes:
            return BoundedRows(rows=out, truncated=True)
        out.append(row)
    return BoundedRows(rows=out, truncated=truncated)
