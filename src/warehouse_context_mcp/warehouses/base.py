"""The port every warehouse adapter implements, and small helpers they share."""

import re
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from warehouse_context_mcp.domain.catalog import Catalog
from warehouse_context_mcp.domain.errors import ConfigurationError
from warehouse_context_mcp.domain.namespace import Namespace


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class ResultSet:
    columns: tuple[str, ...]
    rows: list[tuple[object, ...]]


class Warehouse(Protocol):
    """Physical facts about one schema, and a way to run an already-guarded query.

    Adapters never decide what is safe to run: :class:`~warehouse_context_mcp.guardrails.
    QueryPolicy` does that first. Adapters are the second layer: they connect read-only.
    """

    @property
    def dialect(self) -> str:
        """The sqlglot dialect name used to parse and render SQL for this warehouse."""
        ...

    @property
    def namespace(self) -> Namespace: ...

    def list_tables(self) -> list[str]:
        """Table names in the namespace, lower-case and sorted."""
        ...

    def columns(self, table: str) -> list[Column]: ...

    def row_count(self, table: str) -> int: ...

    def run(self, sql: str, timeout_s: float) -> ResultSet: ...

    def native_catalog(self) -> Catalog:
        """Comments and tags already stored in the warehouse."""
        ...

    def refresh(self) -> None:
        """Forget cached metadata so the next calls re-read it."""
        ...

    def close(self) -> None: ...


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def validate_identifier(value: str) -> str:
    """Check a configured identifier before it is interpolated into catalog SQL.

    Values (table names, tag names) are always bound as parameters; only configured
    database, catalog and schema names, checked here, are written into SQL text.
    """
    if not _IDENTIFIER.fullmatch(value):
        msg = f"invalid identifier: {value!r}"
        raise ConfigurationError(msg)
    return value


@contextmanager
def deadline(seconds: float, cancel: Callable[[], object]) -> Generator[None]:
    """Call ``cancel`` from a timer thread if the block is still running after ``seconds``."""
    timer = threading.Timer(seconds, cancel)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
