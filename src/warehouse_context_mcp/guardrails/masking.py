"""Turn raw warehouse rows into JSON-safe rows with PII values masked."""

from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from datetime import date, datetime, time
from decimal import Decimal

from warehouse_context_mcp.domain.models import Cell, Row

MASK = "***"


def to_cell(value: object) -> Cell:
    """Normalise a driver value to a JSON-safe scalar without losing integer precision."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case Decimal() if value.is_finite():
            exponent = value.as_tuple().exponent
            return int(value) if isinstance(exponent, int) and exponent >= 0 else float(value)
        case datetime() | date() | time():
            return value.isoformat()
        case bytes() | bytearray() | memoryview():
            return f"<{len(value)} bytes>"
        case _:
            return str(value)


def mask_rows(
    columns: Sequence[str], rows: Iterable[Sequence[object]], pii_columns: AbstractSet[str]
) -> list[Row]:
    """Convert rows to dicts keyed by column name, replacing non-null PII values."""
    masked = [c.lower() in pii_columns for c in columns]
    return [
        {
            col: MASK if hide and val is not None else to_cell(val)
            for col, hide, val in zip(columns, masked, row, strict=True)
        }
        for row in rows
    ]
