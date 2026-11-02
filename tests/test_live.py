"""Live checks against real warehouses. Skipped unless credentials are in the environment.

    WCM_LIVE=snowflake pytest -m live      # needs SNOWFLAKE_* variables (see .env.example)
    WCM_LIVE=databricks pytest -m live     # needs DATABRICKS_* variables

    PowerShell:  $env:WCM_LIVE = "snowflake"; pytest -m live
    Variables are read from the environment or from ./.env.

They run the demo scripts' expectations: 4 tables, documented columns, the PII tag
honoured, writes refused.
"""

from __future__ import annotations

import os

import pytest

from warehouse_context_mcp.adapters import (
    DatabricksWarehouse,
    SnowflakeWarehouse,
    Warehouse,
    load_env_file,
)
from warehouse_context_mcp.catalog import Catalog
from warehouse_context_mcp.context import WarehouseContext
from warehouse_context_mcp.guardrails import MASK

load_env_file()
BACKEND = os.environ.get("WCM_LIVE", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(BACKEND not in {"snowflake", "databricks"}, reason="WCM_LIVE not set"),
]


@pytest.fixture(scope="module")
def ctx() -> WarehouseContext:
    wh: Warehouse = (
        SnowflakeWarehouse.from_env() if BACKEND == "snowflake" else DatabricksWarehouse.from_env()
    )
    return WarehouseContext(wh, Catalog())


def test_lists_demo_tables(ctx: WarehouseContext) -> None:
    names = {t["table"] for t in ctx.list_tables()}
    assert {"dim_vessels", "dim_charterers", "stg_voyages", "fct_voyage_economics"} <= names


def test_native_comments_and_pii_tag(ctx: WarehouseContext) -> None:
    d = ctx.describe_table("dim_charterers")
    assert d["description"].startswith("Companies that charter vessels")
    cols = {c["name"]: c for c in d["columns"]}
    assert cols["contact_email"]["pii"] is True
    assert cols["country"]["description"].startswith("ISO")


def test_query_masks_and_refuses(ctx: WarehouseContext) -> None:
    res = ctx.query("select * from dim_charterers", max_rows=3)
    assert res["ok"] and len(res["rows"]) == 3
    assert all(r["contact_email"] == MASK for r in res["rows"])
    assert not ctx.query("select contact_email from dim_charterers")["ok"]
    assert not ctx.query("delete from dim_vessels")["ok"]


def test_aggregate(ctx: WarehouseContext) -> None:
    res = ctx.query(
        "select vessel_class, round(avg(e.tce_usd_per_day), 0) as avg_tce "
        "from fct_voyage_economics e join dim_vessels v using (vessel_id) "
        "group by 1 order by 2 desc"
    )
    assert res["ok"] and len(res["rows"]) == 5
