from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from build_demo import build  # noqa: E402

from warehouse_context_mcp.adapters import DuckDBWarehouse  # noqa: E402
from warehouse_context_mcp.catalog import Catalog  # noqa: E402
from warehouse_context_mcp.context import Limits, WarehouseContext  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("wh") / "demo.duckdb"
    build(str(path), now=NOW)
    return str(path)


@pytest.fixture()
def ctx(demo_db: str) -> WarehouseContext:
    return WarehouseContext(
        DuckDBWarehouse(demo_db),
        Catalog.load(ROOT / "examples" / "catalog.yaml"),
        Limits(max_rows=50, sample_rows=3, timeout_s=5),
    )
