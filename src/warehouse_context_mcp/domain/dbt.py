"""Catalog docs and lineage from a dbt ``manifest.json`` (``dbt docs generate`` or ``dbt compile``).

Models, seeds, snapshots and sources become tables, named by their relation (``alias`` or
``identifier``), so they match what the warehouse lists. What dbt knows is mapped on:

- ``description`` and column descriptions;
- ``owner`` and ``grain`` from ``meta`` (node or ``config.meta``);
- ``upstream`` from ``depends_on.nodes``;
- freshness from a source's ``loaded_at_field`` and ``freshness.warn_after``, or
  ``meta.freshness_column`` and ``meta.expected_freshness_hours`` on any node;
- PII from a column's ``meta`` (``pii: true`` or ``contains_pii: true``) or a ``pii`` tag.

Only the fields above are read; the manifest's SQL, compiled code and configs are not.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .catalog import Catalog, ColumnDoc, TableDoc

RESOURCE_TYPES = frozenset({"model", "seed", "snapshot", "source"})
_HOURS = {"minute": 1 / 60, "hour": 1.0, "day": 24.0}
_PII_KEYS = ("pii", "contains_pii")

type Node = Mapping[str, Any]


def load_manifest(path: Path) -> Catalog:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path}: not a dbt manifest"
        raise TypeError(msg)
    return catalog_from_manifest(data)


def catalog_from_manifest(manifest: Mapping[str, Any]) -> Catalog:
    nodes: dict[str, Node] = {
        uid: node
        for section in ("nodes", "sources")
        for uid, node in (manifest.get(section) or {}).items()
        if node.get("resource_type") in RESOURCE_TYPES
    }
    names = {uid: _relation(node) for uid, node in nodes.items()}
    tables: dict[str, TableDoc] = {}
    # Sources first, so a model of the same name wins: it is what analysts query.
    for uid in sorted(nodes, key=lambda u: (nodes[u].get("resource_type") != "source", u)):
        node = nodes[uid]
        meta = {**(node.get("config") or {}).get("meta", {}), **(node.get("meta") or {})}
        upstream = tuple(
            dict.fromkeys(
                names[dep]
                for dep in (node.get("depends_on") or {}).get("nodes", [])
                if dep in names and names[dep] != names[uid]
            )
        )
        freshness_column, expected = _freshness(node, meta)
        tables[names[uid]] = TableDoc(
            description=_text(node.get("description")),
            owner=_optional(meta.get("owner")),
            grain=_optional(meta.get("grain")),
            upstream=upstream,
            freshness_column=freshness_column,
            expected_freshness_hours=expected,
            columns={
                name.lower(): ColumnDoc(description=_text(col.get("description")), pii=_is_pii(col))
                for name, col in (node.get("columns") or {}).items()
            },
        )
    return Catalog(tables=tables)


def _relation(node: Node) -> str:
    name = node.get("alias") or node.get("identifier") or node["name"]
    return str(name).lower()


def _freshness(node: Node, meta: Mapping[str, Any]) -> tuple[str | None, float | None]:
    column = _optional(meta.get("freshness_column") or node.get("loaded_at_field"))
    hours = meta.get("expected_freshness_hours")
    if hours is None:
        warn = ((node.get("freshness") or {}).get("warn_after")) or {}
        count, period = warn.get("count"), warn.get("period")
        if count and period in _HOURS:
            hours = float(count) * _HOURS[period]
    expected = float(hours) if isinstance(hours, int | float) and hours > 0 else None
    return (column.lower() if column else None), expected


def _is_pii(column: Node) -> bool:
    meta = {**(column.get("config") or {}).get("meta", {}), **(column.get("meta") or {})}
    tags = {str(t).lower() for t in column.get("tags") or []}
    return any(meta.get(k) is True for k in _PII_KEYS) or "pii" in tags


def _text(value: object) -> str:
    return " ".join(str(value).split()) if value else ""


def _optional(value: object) -> str | None:
    return str(value) if value else None
