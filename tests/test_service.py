from datetime import timedelta
from pathlib import Path
from typing import override

import duckdb
import pytest

from warehouse_context_mcp.domain import (
    FreshnessStatus,
    QueryAccepted,
    QueryRefused,
    UnknownTableError,
)
from warehouse_context_mcp.guardrails import MASK
from warehouse_context_mcp.service import Limits, WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse

from .conftest import NOW


def accepted(result: QueryAccepted | QueryRefused) -> QueryAccepted:
    assert isinstance(result, QueryAccepted), result
    return result


def test_list_tables_includes_docs(ctx: WarehouseContext) -> None:
    tables = {t.table: t for t in ctx.list_tables()}
    assert set(tables) == {"dim_charterers", "dim_vessels", "fct_voyage_economics", "stg_voyages"}
    assert tables["fct_voyage_economics"].owner == "freight-analytics"


def test_describe_table_merges_schema_and_docs(ctx: WarehouseContext) -> None:
    d = ctx.describe_table("DIM_CHARTERERS")
    cols = {c.name: c for c in d.columns}
    assert d.table == "dim_charterers"
    assert cols["contact_email"].pii is True
    assert cols["country"].description.startswith("ISO")
    assert d.row_count == 12
    assert "updated_at" in d.undocumented_columns


@pytest.mark.parametrize("call", ["describe_table", "lineage", "freshness", "sample_rows"])
def test_unknown_tables_raise(ctx: WarehouseContext, call: str) -> None:
    with pytest.raises(UnknownTableError, match="nope"):
        getattr(ctx, call)("nope")


def test_lineage_walks_both_directions(ctx: WarehouseContext) -> None:
    lin = ctx.lineage("stg_voyages")
    assert lin.upstream == ["dim_vessels", "dim_charterers"]
    assert lin.downstream == ["fct_voyage_economics"]
    assert ctx.lineage("fct_voyage_economics").upstream == [
        "stg_voyages",
        "dim_vessels",
        "dim_charterers",
    ]
    assert ctx.lineage("fct_voyage_economics", depth=1).upstream == ["stg_voyages"]


def test_freshness_fresh_and_stale(ctx: WarehouseContext) -> None:
    fresh = ctx.freshness("dim_vessels", now=NOW)
    assert fresh.status is FreshnessStatus.FRESH
    assert fresh.latest is not None
    assert fresh.latest.tzinfo is not None
    assert ctx.freshness("dim_vessels", now=NOW + timedelta(days=3)).status is (
        FreshnessStatus.STALE
    )


def test_freshness_unknown_without_documented_column(warehouse: DuckDBWarehouse) -> None:
    result = WarehouseContext(warehouse).freshness("dim_charterers")
    assert result.status is FreshnessStatus.UNKNOWN
    assert result.reason


def test_sample_rows_are_capped_and_masked(ctx: WarehouseContext) -> None:
    res = accepted(ctx.sample_rows("dim_charterers", n=100))
    assert len(res.rows) == 3
    assert all(r["contact_email"] == MASK for r in res.rows)


def test_query_runs_and_reports_sql(ctx: WarehouseContext) -> None:
    res = accepted(ctx.query("select vessel_class, count(*) n from dim_vessels group by 1"))
    assert "LIMIT" in res.sql_executed
    assert '"main"."dim_vessels"' in res.sql_executed
    assert sum(int(r["n"] or 0) for r in res.rows) == 40


def test_query_rejections_are_returned_not_raised(ctx: WarehouseContext) -> None:
    assert ctx.query("drop table dim_vessels") == QueryRefused(
        error="only SELECT queries are allowed, got DROP"
    )


def test_query_unknown_table(ctx: WarehouseContext) -> None:
    res = ctx.query("select * from secrets")
    assert isinstance(res, QueryRefused)
    assert "unknown tables" in res.error


def test_connection_is_read_only_even_without_guardrails(ctx: WarehouseContext) -> None:
    with pytest.raises(duckdb.Error):
        ctx.warehouse.run("delete from dim_vessels", timeout_s=5)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from read_text('/etc/hostname')",
        "select getenv('HOME')",
        "install httpfs",
        "set enable_external_access = true",
    ],
)
def test_duckdb_connection_is_locked_down_even_without_the_policy(
    warehouse: DuckDBWarehouse, sql: str
) -> None:
    with pytest.raises(duckdb.Error):
        warehouse.run(sql, timeout_s=5)


def test_engine_errors_on_pii_tables_withhold_details(ctx: WarehouseContext) -> None:
    # Passes the policy, fails in DuckDB: the message would quote a value.
    res = ctx.query("select cast(charterer_name as int) from dim_charterers")
    assert isinstance(res, QueryRefused)
    assert "withheld" in res.error
    assert "Charterer" not in res.error


def test_engine_errors_elsewhere_explain_themselves(ctx: WarehouseContext) -> None:
    res = ctx.query("select cast(vessel_name as int) from dim_vessels")
    assert isinstance(res, QueryRefused)
    assert "Conversion Error" in res.error


def test_results_are_bounded_in_size(warehouse: DuckDBWarehouse) -> None:
    ctx = WarehouseContext(warehouse, limits=Limits(max_cell_chars=5, max_result_bytes=1_000))
    res = accepted(ctx.query("select vessel_name from dim_vessels"))
    assert res.truncated
    assert all(len(str(r["vessel_name"])) <= 5 + len("…[truncated]") for r in res.rows)
    assert len(res.rows) < 40


class CountingWarehouse(DuckDBWarehouse):
    refreshes = 0

    @override
    def refresh(self) -> None:
        self.refreshes += 1


def test_metadata_is_reread_after_its_ttl(demo_db: Path) -> None:
    warehouse = CountingWarehouse.open(demo_db)
    ctx = WarehouseContext(warehouse, limits=Limits(metadata_ttl_s=0))
    ctx.query("select count(*) from dim_vessels")
    ctx.query("select count(*) from dim_vessels")
    assert warehouse.refreshes >= 3  # startup, then once per query with a zero TTL
    warehouse.close()
