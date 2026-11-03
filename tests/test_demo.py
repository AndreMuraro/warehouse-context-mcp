"""The bundled demo: zero-setup for MCP clients, safe when several start at once."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from warehouse_context_mcp import cli
from warehouse_context_mcp.demo import ensure_demo
from warehouse_context_mcp.domain import Catalog
from warehouse_context_mcp.service import WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse


def test_demo_is_built_once_with_the_packaged_catalog(tmp_path: Path) -> None:
    database, catalog = ensure_demo(tmp_path)
    built_at = database.stat().st_mtime_ns
    assert ensure_demo(tmp_path) == (database, catalog)
    assert database.stat().st_mtime_ns == built_at  # reused, not rebuilt
    assert "fct_voyage_economics" in Catalog.load(catalog).tables


def test_demo_is_fresh_when_built_now(tmp_path: Path) -> None:
    database, catalog = ensure_demo(tmp_path)
    warehouse = DuckDBWarehouse.open(database)
    try:
        ctx = WarehouseContext(warehouse, Catalog.load(catalog))
        assert ctx.freshness("dim_vessels").status == "fresh"
    finally:
        warehouse.close()


def test_concurrent_first_starts_do_not_collide(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ensure_demo(tmp_path), range(4)))
    assert len(set(results)) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["catalog.yaml", "demo.duckdb"]


@pytest.mark.parametrize("extra", [["--duckdb", "x.duckdb"], ["--backend", "snowflake"]])
def test_demo_flag_excludes_other_sources(
    extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--demo", *extra])
    assert "--demo cannot be combined" in capsys.readouterr().err
