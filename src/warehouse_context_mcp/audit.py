"""An append-only audit log: one JSON line per tool call.

Each line records when a tool was called, with which arguments, what came of it (ok,
refused or error), which tables it touched, how many rows it returned, how long it took
and, for queries, the SQL actually sent to the warehouse after the policy rewrote it.
Result rows are never logged, so the log holds no warehouse data beyond what the agent
wrote into its own SQL.

The file is opened with ``O_APPEND`` and never truncated or rewritten; each line is one
``write`` call, so concurrent requests (HTTP transport) or several server processes
sharing a file do not interleave lines. Rotation and shipping are left to the platform
(logrotate, a sidecar, a log agent).
"""

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .domain.errors import WarehouseContextError
from .domain.models import QueryAccepted, QueryRefused

_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)


class Audit(Protocol):
    def call[T](self, tool: str, arguments: Mapping[str, Any], run: Callable[[], T]) -> T: ...


class NoAudit:
    """The default: run the tool, record nothing."""

    def call[T](self, tool: str, arguments: Mapping[str, Any], run: Callable[[], T]) -> T:
        del tool, arguments
        return run()


class AuditLog:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only on creation: the log contains the agent's SQL. An existing file keeps
        # the permissions an operator gave it.
        self._fd = os.open(path, _FLAGS, 0o600)

    def call[T](self, tool: str, arguments: Mapping[str, Any], run: Callable[[], T]) -> T:
        started = time.perf_counter()
        entry: dict[str, Any] = {
            "ts": self._clock().isoformat(timespec="milliseconds"),
            "tool": tool,
            "arguments": dict(arguments),
        }
        try:
            result = run()
        except WarehouseContextError as err:
            entry |= {"outcome": "error", "error": str(err)}
            raise
        except Exception as err:
            entry |= {"outcome": "error", "error": type(err).__name__}
            raise
        else:
            entry |= _describe(result)
            return result
        finally:
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            self._write(entry)

    def _write(self, entry: Mapping[str, Any]) -> None:
        line = (json.dumps(entry, default=str, ensure_ascii=False) + "\n").encode()
        with self._lock:
            os.write(self._fd, line)

    def close(self) -> None:
        os.close(self._fd)


def _describe(result: object) -> dict[str, Any]:
    match result:
        case QueryAccepted(sql_executed=sql, rows=rows, truncated=truncated):
            return {"outcome": "ok", "rows": len(rows), "truncated": truncated, "sql": sql}
        case QueryRefused(error=error):
            return {"outcome": "refused", "error": error}
        case list():
            return {"outcome": "ok", "items": len(result)}
        case _:
            return {"outcome": "ok"}
