from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest

from warehouse_context_mcp.demo import build
from warehouse_context_mcp.domain import Catalog
from warehouse_context_mcp.service import Limits, WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "warehouse_context_mcp" / "demo" / "catalog.yaml"
NOW = datetime(2026, 11, 1, 12, 0, 0)  # noqa: DTZ001  the demo stores naive UTC timestamps


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "demo.duckdb"
    build(path, now=NOW)
    return path


@pytest.fixture
def warehouse(demo_db: Path) -> Generator[DuckDBWarehouse]:
    wh = DuckDBWarehouse.open(demo_db)
    yield wh
    wh.close()


@pytest.fixture
def ctx(warehouse: DuckDBWarehouse) -> WarehouseContext:
    return WarehouseContext(
        warehouse,
        Catalog.load(CATALOG),
        Limits(max_rows=50, sample_rows=3, timeout_s=5),
    )
