"""Each guardrail has a test that fails if that guard is removed."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from warehouse_context_mcp.domain import Namespace, QueryRejectedError
from warehouse_context_mcp.guardrails import MASK, QueryPolicy, guard, mask_rows, to_cell

PII = {"dim_charterers": frozenset({"contact_email"})}


@pytest.mark.parametrize(
    "sql",
    [
        "delete from dim_vessels",
        "update dim_vessels set vessel_name = 'x'",
        "insert into dim_vessels values (1)",
        "drop table dim_vessels",
        "create table t as select 1",
        "alter table dim_vessels add column x int",
        "select 1; drop table dim_vessels",
        "select * into backup from dim_vessels",
        "copy dim_vessels to 'out.csv'",
        "attach 'other.db'",
        "pragma show_tables",
        "set threads = 1",
    ],
)
def test_rejects_anything_but_one_select(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        guard(sql)


def test_rejects_unparseable_sql() -> None:
    with pytest.raises(QueryRejectedError):
        guard("selec * fro")


def test_policy_requires_a_positive_row_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        QueryPolicy(max_rows=0)


def test_adds_limit_when_missing() -> None:
    assert "LIMIT 10" in guard("select * from dim_vessels", max_rows=10).sql


def test_tightens_a_limit_above_the_cap() -> None:
    sql = guard("select * from dim_vessels limit 5000", max_rows=10).sql
    assert "LIMIT 10" in sql
    assert "5000" not in sql


def test_keeps_a_limit_below_the_cap() -> None:
    assert "LIMIT 3" in guard("select * from dim_vessels limit 3", max_rows=10).sql


def test_limits_set_operations() -> None:
    sql = guard("select 1 as a union all select 2 as a", max_rows=1).sql
    assert sql.rstrip().endswith("LIMIT 1")


def test_reports_tables_but_not_cte_names() -> None:
    q = guard("select v.vessel_name from dim_vessels v join stg_voyages s using (vessel_id)")
    assert q.tables == {"dim_vessels", "stg_voyages"}
    q = guard("with recent as (select * from stg_voyages) select count(*) from recent")
    assert q.tables == {"stg_voyages"}


def test_rejects_reading_pii_even_when_aliased() -> None:
    with pytest.raises(QueryRejectedError, match="PII"):
        guard("select contact_email as e from dim_charterers", pii=PII)


def test_rejects_filtering_on_pii() -> None:
    with pytest.raises(QueryRejectedError):
        guard("select charterer_id from dim_charterers where contact_email like 'a%'", pii=PII)


def test_rejects_pii_inside_a_subquery() -> None:
    with pytest.raises(QueryRejectedError):
        guard("select x from (select contact_email as x from dim_charterers)", pii=PII)


def test_allows_counting_pii() -> None:
    guard("select count(distinct contact_email) from dim_charterers", pii=PII)


def test_pii_rule_only_applies_to_tables_in_the_query() -> None:
    guard("select contact_email from some_other_table", pii=PII)


SHIPPING = Namespace(schema="shipping", catalog="wcm_demo")


@pytest.mark.parametrize(
    "sql",
    [
        "select * from other_db.shipping.dim_vessels",
        "select * from public.dim_vessels",
        "select * from dim_vessels join finance.payroll using (id)",
    ],
)
def test_rejects_tables_outside_the_namespace(sql: str) -> None:
    with pytest.raises(QueryRejectedError, match="outside the configured schema"):
        guard(sql, dialect="snowflake", namespace=SHIPPING)


def test_qualifies_unqualified_tables_with_the_namespace() -> None:
    sql = guard(
        "with r as (select * from dim_vessels) select * from r join SHIPPING.stg_voyages s "
        "using (vessel_id)",
        dialect="snowflake",
        namespace=SHIPPING,
    ).sql.lower()
    assert "from wcm_demo.shipping.dim_vessels" in sql
    assert "join wcm_demo.shipping.stg_voyages" in sql
    assert "wcm_demo.shipping.r " not in sql


def test_masks_pii_columns_in_results() -> None:
    rows = mask_rows(["id", "Contact_Email"], [(1, "a@b.c"), (2, None)], {"contact_email"})
    assert rows == [{"id": 1, "Contact_Email": MASK}, {"id": 2, "Contact_Email": None}]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(12), 12),
        (Decimal("12.50"), 12.5),
        (datetime(2026, 11, 1, 12, tzinfo=UTC), "2026-11-01T12:00:00+00:00"),
        (date(2026, 11, 1), "2026-11-01"),
        (b"\x00\x01", "<2 bytes>"),
        (True, True),
        (None, None),
        (Decimal("NaN"), "NaN"),
    ],
)
def test_cells_are_json_safe(value: object, expected: object) -> None:
    assert to_cell(value) == expected
