"""Snowflake and Databricks adapters against a fake DB-API connection (no credentials)."""

from __future__ import annotations

from typing import Any

import pytest

from warehouse_context_mcp.adapters import DatabricksWarehouse, SnowflakeWarehouse
from warehouse_context_mcp.catalog import Catalog, ColumnDoc, TableDoc
from warehouse_context_mcp.context import WarehouseContext
from warehouse_context_mcp.guardrails import MASK, guard


class FakeCursor:
    def __init__(self, routes: list[tuple[str, list[tuple[Any, ...]]]], log: list[str]):
        self.routes, self.log = routes, log
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.log.append(sql)
        low = " ".join(sql.lower().split())
        for needle, rows in self.routes:
            if needle in low:
                self._rows = rows
                break
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        if low.startswith("select") and "information_schema" not in low:
            self.description = [("VESSEL_CLASS",), ("CONTACT_EMAIL",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None: ...

    def cancel(self) -> None: ...


class FakeConnection:
    def __init__(self, routes: list[tuple[str, list[tuple[Any, ...]]]]):
        self.routes, self.log = routes, []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.routes, self.log)


SNOWFLAKE_ROUTES = [
    ("tag_references_all_columns", [("CONTACT_EMAIL", "PII", "true")]),
    ("select table_name, comment", [("DIM_CHARTERERS", "Companies that charter vessels.")]),
    ("select table_name, column_name, comment", [("DIM_CHARTERERS", "COUNTRY", "ISO country")]),
    ("select table_name from", [("DIM_CHARTERERS",)]),
    (
        "select column_name, data_type",
        [("CHARTERER_ID", "NUMBER", "NO", None), ("CONTACT_EMAIL", "TEXT", "YES", "x")],
    ),
    ("select count(*)", [(12,)]),
    ("dim_charterers limit", [("MR", "ops1@x.example")]),
]


@pytest.fixture()
def snowflake() -> SnowflakeWarehouse:
    return SnowflakeWarehouse(FakeConnection(SNOWFLAKE_ROUTES), "wcm_demo", "shipping")


def test_snowflake_reads_comments_and_pii_tags(snowflake: SnowflakeWarehouse) -> None:
    cat = snowflake.native_catalog()
    doc = cat.table("dim_charterers")
    assert doc.description == "Companies that charter vessels."
    assert doc.columns["country"].description == "ISO country"
    assert cat.pii_columns("dim_charterers") == {"contact_email"}


def test_snowflake_lists_and_describes(snowflake: SnowflakeWarehouse) -> None:
    assert snowflake.list_tables() == ["dim_charterers"]
    cols = snowflake.columns("DIM_CHARTERERS")
    assert [c.name for c in cols] == ["charterer_id", "contact_email"]
    assert snowflake.row_count("dim_charterers") == 12
    with pytest.raises(KeyError):
        snowflake.columns("secrets")


def test_snowflake_tag_pii_masks_results_end_to_end(snowflake: SnowflakeWarehouse) -> None:
    ctx = WarehouseContext(snowflake, Catalog())
    res = ctx.query("select * from dim_charterers")
    assert res["ok"] and res["rows"] == [{"vessel_class": "MR", "contact_email": MASK}]
    assert not ctx.query("select contact_email from dim_charterers")["ok"]


def test_rejects_tables_outside_the_configured_schema(snowflake: SnowflakeWarehouse) -> None:
    ctx = WarehouseContext(snowflake, Catalog())
    res = ctx.query("select * from other_db.public.dim_charterers")
    assert not res["ok"] and "outside the configured schema" in res["error"]
    assert ctx.query("select * from wcm_demo.shipping.dim_charterers")["ok"]


def test_invalid_configured_identifiers_are_refused() -> None:
    with pytest.raises(ValueError):
        SnowflakeWarehouse(FakeConnection([]), "db; drop", "x")


DATABRICKS_ROUTES = [
    ("column_tags", [("dim_charterers", "contact_email", "true")]),
    ("select table_name, comment", [("dim_charterers", "Charterers")]),
    ("select table_name, column_name, comment", []),
    ("select table_name from", [("dim_charterers",)]),
    ("select column_name, data_type", [("contact_email", "string", "YES", None)]),
]


def test_databricks_reads_unity_catalog_tags() -> None:
    wh = DatabricksWarehouse(FakeConnection(DATABRICKS_ROUTES), "workspace", "wcm_demo")
    cat = wh.native_catalog()
    assert cat.table("dim_charterers").description == "Charterers"
    assert cat.pii_columns("dim_charterers") == {"contact_email"}
    assert [c.name for c in wh.columns("dim_charterers")] == ["contact_email"]
    assert wh.namespace == ("workspace", "wcm_demo")


def test_curated_catalog_wins_but_cannot_unflag_pii() -> None:
    native = Catalog(
        tables={
            "t": TableDoc(
                description="native",
                columns={"email": ColumnDoc(description="from comment", pii=True)},
            )
        }
    )
    curated = Catalog(
        tables={
            "t": TableDoc(
                description="curated", owner="team", columns={"email": ColumnDoc(pii=False)}
            )
        }
    )
    merged = native.merged_with(curated)
    assert merged.table("t").description == "curated"
    assert merged.table("t").owner == "team"
    assert merged.table("t").columns["email"].description == "from comment"
    assert merged.pii_columns("t") == {"email"}


def test_cte_names_are_not_treated_as_tables() -> None:
    q = guard("with recent as (select * from stg_voyages) select count(*) from recent")
    assert q.tables == {"stg_voyages"}


def test_env_file_loader_handles_crlf_quotes_and_precedence(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warehouse_context_mcp.adapters import load_env_file

    env = tmp_path / ".env"
    env.write_bytes(b'WCM_A="one"\r\nWCM_B=\r\n# comment\r\nWCM_C=three\r\n')
    monkeypatch.setenv("WCM_C", "kept")
    monkeypatch.delenv("WCM_A", raising=False)
    monkeypatch.delenv("WCM_B", raising=False)
    load_env_file(env)
    import os

    assert os.environ["WCM_A"] == "one"
    assert "WCM_B" not in os.environ
    assert os.environ["WCM_C"] == "kept"
