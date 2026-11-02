"""Snowflake and Databricks adapters against a fake DB-API connection (no credentials)."""

from collections.abc import Sequence

import pytest

from warehouse_context_mcp.domain import (
    Catalog,
    ColumnDoc,
    ConfigurationError,
    QueryAccepted,
    QueryRefused,
    TableDoc,
    UnknownTableError,
)
from warehouse_context_mcp.guardrails import MASK
from warehouse_context_mcp.service import WarehouseContext
from warehouse_context_mcp.warehouses import DatabricksWarehouse, SnowflakeWarehouse

type Routes = list[tuple[str, list[tuple[object, ...]]]]


class FakeCursor:
    """Answers each statement with the rows of the first route whose needle it contains."""

    def __init__(self, routes: Routes, log: list[tuple[str, object]]) -> None:
        self._routes = routes
        self._log = log
        self._rows: list[tuple[object, ...]] = []
        self.description: Sequence[Sequence[object]] | None = None

    def execute(self, operation: str, parameters: object = None, /) -> None:
        self._log.append((operation, parameters))
        sql = " ".join(operation.lower().split())
        for needle, rows in self._routes:
            if needle in sql:
                self._rows = rows
                break
        else:
            msg = f"unexpected SQL: {operation}"
            raise AssertionError(msg)
        if "information_schema" not in sql:
            self.description = [("VESSEL_CLASS",), ("CONTACT_EMAIL",)]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None: ...


class FakeConnection:
    def __init__(self, routes: Routes) -> None:
        self.routes = routes
        self.log: list[tuple[str, object]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.routes, self.log)

    def close(self) -> None: ...


SNOWFLAKE_ROUTES: Routes = [
    ("tag_references_all_columns", [("CONTACT_EMAIL", "PII", "true")]),
    ("select table_name, comment", [("DIM_CHARTERERS", "Companies that charter vessels.")]),
    ("select table_name, column_name, comment", [("DIM_CHARTERERS", "COUNTRY", "ISO country")]),
    ("select table_name from", [("DIM_CHARTERERS",)]),
    (
        "select column_name, data_type",
        [("CHARTERER_ID", "NUMBER", "NO"), ("CONTACT_EMAIL", "TEXT", "YES")],
    ),
    ("select count(*)", [(12,)]),
    ("dim_charterers limit", [("MR", "ops1@x.example")]),
]


@pytest.fixture
def snowflake() -> SnowflakeWarehouse:
    return SnowflakeWarehouse(FakeConnection(SNOWFLAKE_ROUTES), "wcm_demo", "shipping")


def test_snowflake_reads_comments_and_pii_tags(snowflake: SnowflakeWarehouse) -> None:
    doc = snowflake.native_catalog().table("dim_charterers")
    assert doc.description == "Companies that charter vessels."
    assert doc.columns["country"].description == "ISO country"
    assert doc.pii_columns == {"contact_email"}


def test_snowflake_lists_and_describes(snowflake: SnowflakeWarehouse) -> None:
    assert snowflake.list_tables() == ["dim_charterers"]
    cols = snowflake.columns("DIM_CHARTERERS")
    assert [c.name for c in cols] == ["charterer_id", "contact_email"]
    assert [c.nullable for c in cols] == [False, True]
    assert snowflake.row_count("dim_charterers") == 12
    with pytest.raises(UnknownTableError):
        snowflake.columns("secrets")


def test_snowflake_binds_values_as_parameters() -> None:
    con = FakeConnection(SNOWFLAKE_ROUTES)
    SnowflakeWarehouse(con, "wcm_demo", "shipping").columns("dim_charterers")
    sql, params = con.log[-1]
    assert "%s" in sql
    assert params == ("SHIPPING", "dim_charterers")


def test_snowflake_tag_pii_masks_results_end_to_end(snowflake: SnowflakeWarehouse) -> None:
    ctx = WarehouseContext(snowflake)
    res = ctx.query("select * from dim_charterers")
    assert isinstance(res, QueryAccepted)
    assert res.rows == [{"vessel_class": "MR", "contact_email": MASK}]
    assert "WCM_DEMO.SHIPPING.dim_charterers".lower() in res.sql_executed.lower()
    assert isinstance(ctx.query("select contact_email from dim_charterers"), QueryRefused)


def test_rejects_tables_outside_the_configured_schema(snowflake: SnowflakeWarehouse) -> None:
    ctx = WarehouseContext(snowflake)
    res = ctx.query("select * from other_db.public.dim_charterers")
    assert isinstance(res, QueryRefused)
    assert "outside the configured schema" in res.error
    assert isinstance(ctx.query("select * from wcm_demo.shipping.dim_charterers"), QueryAccepted)


@pytest.mark.parametrize(("database", "schema"), [("db; drop", "x"), ("db", "x y"), ("", "x")])
def test_invalid_configured_identifiers_are_refused(database: str, schema: str) -> None:
    with pytest.raises(ConfigurationError, match="invalid identifier"):
        SnowflakeWarehouse(FakeConnection([]), database, schema)


DATABRICKS_ROUTES: Routes = [
    ("column_tags", [("dim_charterers", "contact_email", None)]),
    ("select table_name, comment", [("dim_charterers", "Charterers")]),
    ("select table_name, column_name, comment", []),
    ("select table_name from", [("dim_charterers",)]),
    ("select column_name, data_type", [("contact_email", "string", "YES")]),
]


def test_databricks_reads_unity_catalog_tags_with_named_parameters() -> None:
    con = FakeConnection(DATABRICKS_ROUTES)
    wh = DatabricksWarehouse(con, "workspace", "wcm_demo")
    doc = wh.native_catalog().table("dim_charterers")
    assert doc.description == "Charterers"
    assert doc.pii_columns == {"contact_email"}  # a bare tag with no value counts
    assert [c.name for c in wh.columns("dim_charterers")] == ["contact_email"]
    assert con.log[-1][1] == {"schema": "wcm_demo", "table": "dim_charterers"}
    assert str(wh.namespace) == "workspace.wcm_demo"


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
    merged = native.merged_with(curated).table("t")
    assert merged.description == "curated"
    assert merged.owner == "team"
    assert merged.columns["email"].description == "from comment"
    assert merged.pii_columns == {"email"}
