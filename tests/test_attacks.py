"""Regression suite from the independent security review: every attack stays closed.

The DuckDB queries run end to end against the demo warehouse; the Snowflake and Databricks
queries go through the policy for their dialect (no live warehouse is needed).
"""

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from warehouse_context_mcp.domain import (
    Catalog,
    Namespace,
    QueryAccepted,
    QueryRefused,
    QueryRejectedError,
)
from warehouse_context_mcp.guardrails import QueryPolicy
from warehouse_context_mcp.service import Limits, WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse

from .conftest import CATALOG

ATTACKS = Path(__file__).parent / "attacks"


def load(name: str) -> list[str]:
    text = "\n".join(
        line
        for line in (ATTACKS / name).read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )
    return [q.strip() for q in text.split(";;") if q.strip()]


# Anything that would show the attack worked: demo e-mail addresses, file contents,
# environment variables, settings or metadata from outside the schema.
LEAK = re.compile(
    r"@charterer-|root:|PATH=|HOME=|enable_external_access|\.yaml|/etc/", re.IGNORECASE
)


@pytest.mark.parametrize("sql", load("duckdb.sql"))
def test_duckdb_attack_leaks_nothing(warehouse: DuckDBWarehouse, sql: str) -> None:
    limits = Limits(timeout_s=5, max_result_bytes=200_000)
    ctx = WarehouseContext(warehouse, Catalog.load(CATALOG), limits)
    result = ctx.query(sql)
    if isinstance(result, QueryAccepted):
        assert not LEAK.search(str(result.rows)), result
        assert len(str(result.rows)) < 400_000


@pytest.mark.parametrize("sql", load("order_oracles.sql"))
def test_order_by_cannot_sort_by_pii(warehouse: DuckDBWarehouse, sql: str) -> None:
    ctx = WarehouseContext(warehouse, Catalog.load(CATALOG))
    result = ctx.query(sql)
    assert isinstance(result, QueryRefused), result


SNOWFLAKE_COLUMNS: dict[str, Mapping[str, str]] = {
    "dim_charterers": {"charterer_id": "NUMBER", "contact_email": "TEXT", "country": "TEXT"},
    "dim_vessels": {"vessel_id": "NUMBER", "vessel_class": "TEXT"},
}


@pytest.mark.parametrize(
    ("dialect", "namespace", "sql"),
    [
        ("snowflake", Namespace(schema="shipping", catalog="wcm_demo"), q)
        for q in load("snowflake.sql")
    ]
    + [
        ("databricks", Namespace(schema="wcm_demo", catalog="workspace"), q)
        for q in load("databricks.sql")
    ],
)
def test_warehouse_dialect_attack_is_refused_or_masked(
    dialect: str, namespace: Namespace, sql: str
) -> None:
    policy = QueryPolicy(
        columns=SNOWFLAKE_COLUMNS.get,
        dialect=dialect,
        namespace=namespace,
        pii={"dim_charterers": frozenset({"contact_email"})},
    )
    try:
        guarded = policy.check(sql)
    except QueryRejectedError:
        return
    # Accepted only if the PII column is never read: at most it names a masked output.
    assert not re.search(r"""[.](["`]?)contact_email\1""", guarded.sql, re.IGNORECASE), guarded.sql
