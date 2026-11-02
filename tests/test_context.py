from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from warehouse_context_mcp.context import WarehouseContext
from warehouse_context_mcp.guardrails import MASK

from .conftest import NOW


def test_list_tables_includes_docs(ctx: WarehouseContext) -> None:
    tables = {t["table"]: t for t in ctx.list_tables()}
    assert set(tables) == {"dim_charterers", "dim_vessels", "fct_voyage_economics", "stg_voyages"}
    assert tables["fct_voyage_economics"]["owner"] == "freight-analytics"


def test_describe_table_merges_schema_and_docs(ctx: WarehouseContext) -> None:
    d = ctx.describe_table("dim_charterers")
    cols = {c["name"]: c for c in d["columns"]}
    assert cols["contact_email"]["pii"] is True
    assert cols["country"]["description"].startswith("ISO")
    assert d["row_count"] == 12
    assert "updated_at" in d["undocumented_columns"]


def test_describe_unknown_table_raises(ctx: WarehouseContext) -> None:
    with pytest.raises(KeyError):
        ctx.describe_table("nope")


def test_lineage_walks_both_directions(ctx: WarehouseContext) -> None:
    lin = ctx.lineage("stg_voyages")
    assert lin["upstream"] == ["dim_vessels", "dim_charterers"]
    assert lin["downstream"] == ["fct_voyage_economics"]
    assert ctx.lineage("fct_voyage_economics")["upstream"] == [
        "stg_voyages",
        "dim_vessels",
        "dim_charterers",
    ]


def test_freshness_fresh_and_stale(ctx: WarehouseContext) -> None:
    assert ctx.freshness("dim_vessels", now=NOW)["status"] == "fresh"
    assert ctx.freshness("dim_vessels", now=NOW + timedelta(days=3))["status"] == "stale"


def test_sample_rows_are_capped_and_masked(ctx: WarehouseContext) -> None:
    res = ctx.sample_rows("dim_charterers", n=100)
    assert res["ok"] and len(res["rows"]) == 3
    assert all(r["contact_email"] == MASK for r in res["rows"])


def test_query_runs_and_reports_sql(ctx: WarehouseContext) -> None:
    res = ctx.query("select vessel_class, count(*) n from dim_vessels group by 1 order by 1")
    assert res["ok"] and "LIMIT" in res["sql_executed"]
    assert sum(r["n"] for r in res["rows"]) == 40


def test_query_rejections_are_returned_not_raised(ctx: WarehouseContext) -> None:
    res = ctx.query("drop table dim_vessels")
    assert res == {"ok": False, "error": "only SELECT queries are allowed, got DROP"}


def test_query_unknown_table(ctx: WarehouseContext) -> None:
    assert not ctx.query("select * from secrets")["ok"]


def test_connection_is_read_only_even_without_guardrails(ctx: WarehouseContext) -> None:
    with pytest.raises(duckdb.Error):
        ctx.wh.run("delete from dim_vessels", timeout_s=5)
