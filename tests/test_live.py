"""Live checks against real warehouses. Skipped unless credentials are in the environment.

    WCM_LIVE=snowflake uv run pytest -m live     # SNOWFLAKE_* settings (see .env.example)
    WCM_LIVE=databricks uv run pytest -m live    # DATABRICKS_* settings

    PowerShell:  $env:WCM_LIVE = "snowflake"; uv run pytest -m live
    Settings are read from the environment or from ./.env (the environment wins).

They run the demo scripts' expectations: 4 tables, documented columns, the PII tag
honoured, writes refused.
"""

import os
from collections.abc import Generator

import pytest

from warehouse_context_mcp.demo import bundled_catalog
from warehouse_context_mcp.domain import QueryAccepted, QueryRefused
from warehouse_context_mcp.guardrails import MASK
from warehouse_context_mcp.service import WarehouseContext
from warehouse_context_mcp.warehouses import Backend, open_warehouse

BACKEND = os.environ.get("WCM_LIVE", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        BACKEND not in {Backend.SNOWFLAKE, Backend.DATABRICKS}, reason="WCM_LIVE not set"
    ),
]


@pytest.fixture(scope="module")
def ctx() -> Generator[WarehouseContext]:
    warehouse = open_warehouse(Backend(BACKEND))
    yield WarehouseContext(warehouse)
    warehouse.close()


def test_lists_demo_tables(ctx: WarehouseContext) -> None:
    names = {t.table for t in ctx.list_tables()}
    assert {"dim_vessels", "dim_charterers", "stg_voyages", "fct_voyage_economics"} <= names


def test_native_comments_and_pii_tag(ctx: WarehouseContext) -> None:
    d = ctx.describe_table("dim_charterers")
    assert d.description.startswith("Companies that charter vessels")
    cols = {c.name: c for c in d.columns}
    assert cols["contact_email"].pii is True
    assert cols["country"].description.startswith("ISO")


def test_query_masks_and_refuses(ctx: WarehouseContext) -> None:
    res = ctx.query("select * from dim_charterers", max_rows=3)
    assert isinstance(res, QueryAccepted)
    assert len(res.rows) == 3
    assert all(r["contact_email"] == MASK for r in res.rows)
    masked = ctx.query("select contact_email from dim_charterers")
    assert isinstance(masked, QueryAccepted)
    assert all(r["contact_email"] == MASK for r in masked.rows)
    filtered = ctx.query("select country from dim_charterers where contact_email like 'a%'")
    assert isinstance(filtered, QueryRefused)
    assert isinstance(ctx.query("delete from dim_vessels"), QueryRefused)


def test_aggregate(ctx: WarehouseContext) -> None:
    res = ctx.query(
        "select vessel_class, round(avg(e.tce_usd_per_day), 0) as avg_tce "
        "from fct_voyage_economics e join dim_vessels v using (vessel_id) "
        "group by 1 order by 2 desc"
    )
    assert isinstance(res, QueryAccepted)
    assert len(res.rows) == 5


def test_freshness_reads_a_timestamp(ctx: WarehouseContext) -> None:
    # Which column records updates is curated knowledge, not stored in the warehouse.
    curated = WarehouseContext(ctx.warehouse, bundled_catalog())
    result = curated.freshness("dim_vessels")
    assert result.latest is not None, result.reason
    assert result.latest.tzinfo is not None
