"""Pick and open a warehouse adapter from CLI arguments and settings."""

from enum import StrEnum
from pathlib import Path

from warehouse_context_mcp.domain.errors import ConfigurationError
from warehouse_context_mcp.settings import DatabricksSettings, SnowflakeSettings

from .base import Warehouse
from .databricks import DatabricksWarehouse
from .duckdb import DuckDBWarehouse
from .snowflake import SnowflakeWarehouse


class Backend(StrEnum):
    DUCKDB = "duckdb"
    SNOWFLAKE = "snowflake"
    DATABRICKS = "databricks"


def open_warehouse(
    backend: Backend, *, duckdb_path: Path | None = None, env_file: Path | None = Path(".env")
) -> Warehouse:
    match backend:
        case Backend.DUCKDB:
            if duckdb_path is None:
                msg = "the duckdb backend needs a database file (--duckdb PATH)"
                raise ConfigurationError(msg)
            return DuckDBWarehouse.open(duckdb_path)
        case Backend.SNOWFLAKE:
            return SnowflakeWarehouse.connect(SnowflakeSettings.from_env(env_file))
        case Backend.DATABRICKS:
            return DatabricksWarehouse.connect(DatabricksSettings.from_env(env_file))
