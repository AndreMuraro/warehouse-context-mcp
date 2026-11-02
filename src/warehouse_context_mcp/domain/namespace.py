"""Where an agent is allowed to look."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Namespace:
    """The schema an agent may query, and the catalog (database) that holds it.

    ``catalog`` is ``None`` for single-catalog engines such as a DuckDB file. Names are
    stored lower-case; warehouses compare identifiers case-insensitively.
    """

    schema: str
    catalog: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", self.schema.lower())
        if self.catalog is not None:
            object.__setattr__(self, "catalog", self.catalog.lower())

    def contains(self, catalog: str, schema: str) -> bool:
        """Whether a reference written as ``catalog.schema.table`` stays inside.

        Empty parts mean the reference was unqualified and resolves to this namespace.
        """
        schema_ok = not schema or schema.lower() == self.schema
        catalog_ok = not catalog or self.catalog is None or catalog.lower() == self.catalog
        return schema_ok and catalog_ok

    def __str__(self) -> str:
        return f"{self.catalog}.{self.schema}" if self.catalog else self.schema
