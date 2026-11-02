"""Exceptions raised by the domain, guardrail and warehouse layers."""


class WarehouseContextError(Exception):
    """Base class for every error this package raises on purpose."""


class UnknownTableError(WarehouseContextError, LookupError):
    """The table does not exist in the configured schema."""

    def __init__(self, table: str) -> None:
        super().__init__(f"unknown table: {table}")
        self.table = table


class QueryRejectedError(WarehouseContextError, ValueError):
    """The query is not provably read-only, in scope and free of PII reads."""


class ConfigurationError(WarehouseContextError, ValueError):
    """Settings are missing or invalid, or an optional backend is not installed."""
