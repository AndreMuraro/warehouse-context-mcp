"""Warehouse adapters: physical facts and read-only execution, one module per engine."""

from .base import Column, ResultSet, Warehouse
from .databricks import DatabricksWarehouse
from .dbapi import Connection, Cursor, DbApiWarehouse
from .duckdb import DuckDBWarehouse
from .factory import Backend, open_warehouse
from .snowflake import SnowflakeWarehouse

__all__ = [
    "Backend",
    "Column",
    "Connection",
    "Cursor",
    "DatabricksWarehouse",
    "DbApiWarehouse",
    "DuckDBWarehouse",
    "ResultSet",
    "SnowflakeWarehouse",
    "Warehouse",
    "open_warehouse",
]
