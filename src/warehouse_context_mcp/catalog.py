"""Human-curated warehouse context loaded from a YAML file.

The catalog is the part of warehouse knowledge that never lives in the database itself:
what a table means, who owns it, where it comes from, which columns are sensitive, and how
fresh it is supposed to be. Agents get this alongside the physical schema.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ColumnDoc(BaseModel):
    description: str = ""
    pii: bool = False


class TableDoc(BaseModel):
    description: str = ""
    owner: str | None = None
    grain: str | None = None
    upstream: list[str] = Field(default_factory=list)
    freshness_column: str | None = None
    expected_freshness_hours: float | None = None
    columns: dict[str, ColumnDoc] = Field(default_factory=dict)

    @property
    def expected_freshness(self) -> timedelta | None:
        if self.expected_freshness_hours is None:
            return None
        return timedelta(hours=self.expected_freshness_hours)


class Catalog(BaseModel):
    tables: dict[str, TableDoc] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> Catalog:
        if path is None:
            return cls()
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def table(self, name: str) -> TableDoc:
        return self.tables.get(name.lower(), TableDoc())

    def pii_columns(self, table: str) -> set[str]:
        return {c.lower() for c, doc in self.table(table).columns.items() if doc.pii}

    def downstream(self, table: str) -> list[str]:
        name = table.lower()
        return sorted(
            t for t, doc in self.tables.items() if name in (u.lower() for u in doc.upstream)
        )

    def lineage(self, table: str, depth: int = 3) -> dict[str, list[str]]:
        """Upstream and downstream tables up to `depth` hops, cycle-safe."""

        def walk(start: str, step: Callable[[str], list[str]]) -> list[str]:
            seen: list[str] = []
            frontier = [start.lower()]
            for _ in range(depth):
                nxt: list[str] = []
                for node in frontier:
                    for neighbour in step(node):
                        n = neighbour.lower()
                        if n not in seen and n != start.lower():
                            seen.append(n)
                            nxt.append(n)
                frontier = nxt
            return seen

        return {
            "upstream": walk(table, lambda t: self.table(t).upstream),
            "downstream": walk(table, self.downstream),
        }
