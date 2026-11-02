"""Curated warehouse knowledge: meaning, ownership, lineage, sensitivity and freshness.

This is the part of warehouse knowledge that schema alone does not carry. It comes from a
YAML file reviewed like code, from comments and tags stored in the warehouse, or both.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ColumnDoc(_Frozen):
    description: str = ""
    pii: bool = False


class TableDoc(_Frozen):
    description: str = ""
    owner: str | None = None
    grain: str | None = None
    upstream: tuple[str, ...] = ()
    freshness_column: str | None = None
    expected_freshness_hours: float | None = Field(default=None, gt=0)
    columns: Mapping[str, ColumnDoc] = Field(default_factory=dict)

    @property
    def expected_freshness(self) -> timedelta | None:
        if self.expected_freshness_hours is None:
            return None
        return timedelta(hours=self.expected_freshness_hours)

    @property
    def pii_columns(self) -> frozenset[str]:
        return frozenset(name.lower() for name, doc in self.columns.items() if doc.pii)


_EMPTY = TableDoc()
_NO_COLUMN = ColumnDoc()


class Catalog(_Frozen):
    tables: Mapping[str, TableDoc] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> Catalog:
        if path is None:
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def table(self, name: str) -> TableDoc:
        return self.tables.get(name.lower(), _EMPTY)

    def pii_map(self, tables: Iterable[str]) -> dict[str, frozenset[str]]:
        return {t: self.table(t).pii_columns for t in tables}

    def merged_with(self, curated: Catalog) -> Catalog:
        """Overlay a curated catalog on warehouse-native docs (``self``).

        Curated values win where set; native comments and tags fill the gaps. A column is
        PII if either source says so: a flag can be added, never removed.
        """
        names = sorted(self.tables.keys() | curated.tables.keys())
        return Catalog(tables={n: _merge(self.table(n), curated.table(n)) for n in names})

    def downstream(self, table: str) -> list[str]:
        name = table.lower()
        return sorted(t for t, doc in self.tables.items() if name in _lower(doc.upstream))

    def upstream(self, table: str) -> list[str]:
        return _lower(self.table(table).upstream)

    def lineage(self, table: str, depth: int) -> tuple[list[str], list[str]]:
        """Upstream and downstream tables up to ``depth`` hops, cycle-safe and ordered."""
        return _walk(table, self.upstream, depth), _walk(table, self.downstream, depth)


def _merge(native: TableDoc, curated: TableDoc) -> TableDoc:
    columns: dict[str, ColumnDoc] = {}
    for col in sorted(native.columns.keys() | curated.columns.keys()):
        n = native.columns.get(col, _NO_COLUMN)
        c = curated.columns.get(col, _NO_COLUMN)
        columns[col] = ColumnDoc(description=c.description or n.description, pii=c.pii or n.pii)
    return TableDoc(
        description=curated.description or native.description,
        owner=curated.owner or native.owner,
        grain=curated.grain or native.grain,
        upstream=curated.upstream or native.upstream,
        freshness_column=curated.freshness_column or native.freshness_column,
        expected_freshness_hours=(
            curated.expected_freshness_hours
            if curated.expected_freshness_hours is not None
            else native.expected_freshness_hours
        ),
        columns=columns,
    )


def _lower(names: Iterable[str]) -> list[str]:
    return [n.lower() for n in names]


def _walk(start: str, step: Callable[[str], list[str]], depth: int) -> list[str]:
    """Breadth-first walk; each table appears once, in the order it was first reached."""
    origin = start.lower()
    seen: dict[str, None] = {}  # insertion-ordered set
    frontier = [origin]
    for _ in range(depth):
        reached = [n for node in frontier for n in step(node) if n != origin and n not in seen]
        frontier = list(dict.fromkeys(reached))
        seen.update(dict.fromkeys(frontier))
    return list(seen)
