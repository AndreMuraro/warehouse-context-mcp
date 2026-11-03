"""The query policy refuses everything it does not positively allow."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from warehouse_context_mcp.domain import Namespace, QueryRejectedError
from warehouse_context_mcp.guardrails import (
    MASK,
    TRUNCATED,
    GuardedQuery,
    QueryPolicy,
    mask_rows,
    to_cell,
)

COLUMNS: dict[str, Mapping[str, str]] = {
    "dim_vessels": {"vessel_id": "INT", "vessel_name": "VARCHAR", "vessel_class": "VARCHAR"},
    "dim_charterers": {
        "charterer_id": "INT",
        "charterer_name": "VARCHAR",
        "contact_email": "VARCHAR",
        "country": "VARCHAR",
    },
    "stg_voyages": {"voyage_id": "INT", "vessel_id": "INT", "cargo_tonnes": "INT"},
}
PII = {"dim_charterers": frozenset({"contact_email"})}


def check(sql: str, **options: object) -> GuardedQuery:
    settings: dict[str, object] = {"columns": COLUMNS.get, "pii": PII} | options
    return QueryPolicy(**settings).check(sql)  # ty: ignore[invalid-argument-type]


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
def test_refuses_anything_but_one_select(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        check(sql)


def test_refuses_unparseable_sql() -> None:
    with pytest.raises(QueryRejectedError):
        check("selec * fro")


def test_policy_requires_a_positive_row_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        QueryPolicy(columns=COLUMNS.get, max_rows=0)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("select getenv('HOME')", "function GETENV is not allowed"),
        ("select * from read_text('/etc/hostname')", "only plain table references"),
        ("select * from dim_vessels, lateral read_text('x')", "forbidden construct: LATERAL"),
        ("select * from (summarize dim_charterers)", "forbidden construct"),
        ("select max(columns('contact.*')) from dim_charterers", "COLUMNS is not allowed"),
        ("select * from dim_charterers as t(a, b, c, d)", "column alias lists"),
        ("select * rename (contact_email as e) from dim_charterers", r"SELECT \* modifiers"),
        ("with recursive r as (select 1) select * from r", "recursive CTEs"),
        ("select t from dim_charterers t", "TABLECOLUMN"),  # a whole row as a value
        ("select nope from dim_vessels", "could not resolve"),
        ("select * from secrets", "unknown tables"),
    ],
)
def test_refuses_what_is_not_explicitly_allowed(sql: str, reason: str) -> None:
    with pytest.raises(QueryRejectedError, match=reason):
        check(sql)


def test_adds_limit_when_missing() -> None:
    assert "LIMIT 10" in check("select * from dim_vessels", max_rows=10).sql


def test_tightens_a_limit_above_the_cap() -> None:
    sql = check("select * from dim_vessels limit 5000", max_rows=10).sql
    assert "LIMIT 10" in sql
    assert "5000" not in sql


def test_keeps_a_limit_below_the_cap() -> None:
    assert "LIMIT 3" in check("select * from dim_vessels limit 3", max_rows=10).sql


def test_limits_set_operations() -> None:
    sql = check("select 1 as a union all select 2 as a", max_rows=1).sql
    assert sql.rstrip().endswith("LIMIT 1")


def test_reports_physical_tables_but_not_cte_names() -> None:
    q = check("select v.vessel_name from dim_vessels v join stg_voyages s using (vessel_id)")
    assert q.tables == {"dim_vessels", "stg_voyages"}
    q = check("with recent as (select * from stg_voyages) select count(*) from recent")
    assert q.tables == {"stg_voyages"}


@pytest.mark.parametrize(
    "sql",
    [
        "select * from dim_charterers",
        "select contact_email as e from dim_charterers",
        "select x from (select contact_email as x from dim_charterers) s",
        "with c as (select * from dim_charterers) select * from c",
        "select 1 a, 'b' b, 'c' c, 'd' d union all select * from dim_charterers",
    ],
)
def test_pii_in_any_select_list_is_masked_in_the_sql(sql: str) -> None:
    rewritten = check(sql).sql
    assert f"'{MASK}'" in rewritten
    assert '"dim_charterers"."contact_email"' not in rewritten  # the column is never read


@pytest.mark.parametrize(
    "sql",
    [
        "select charterer_id from dim_charterers where contact_email like 'a%'",
        "select country from dim_charterers order by contact_email",
        "select contact_email, count(*) from dim_charterers group by 1",
        "select upper(contact_email) from dim_charterers",
        "select count(case when contact_email like 'ops1@%' then 1 end) from dim_charterers",
        "select count(contact_email::int) from dim_charterers",
        "select min(contact_email) from dim_charterers",
    ],
)
def test_pii_cannot_be_used_to_infer_values(sql: str) -> None:
    with pytest.raises(QueryRejectedError, match="marked PII"):
        check(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "select count(contact_email) from dim_charterers",
        "select country, count(distinct contact_email) from dim_charterers group by country",
    ],
)
def test_pii_can_be_counted(sql: str) -> None:
    check(sql)


def test_pii_rule_only_applies_to_the_flagged_table() -> None:
    columns = COLUMNS | {"other": {"contact_email": "VARCHAR"}}
    check("select contact_email from other where contact_email like 'a%'", columns=columns.get)


SHIPPING = Namespace(schema="shipping", catalog="wcm_demo")


@pytest.mark.parametrize(
    "sql",
    [
        "select * from other_db.shipping.dim_vessels",
        "select * from public.dim_vessels",
        'select * from "WCM_DEMO"."shipping"."DIM_VESSELS"',  # quoted lower case is another schema
    ],
)
def test_refuses_tables_outside_the_namespace(sql: str) -> None:
    with pytest.raises(QueryRejectedError, match="outside the configured schema"):
        check(sql, dialect="snowflake", namespace=SHIPPING)


def test_qualifies_tables_with_the_namespace() -> None:
    sql = check(
        "with r as (select * from dim_vessels) select * from r join shipping.stg_voyages s "
        "using (vessel_id)",
        dialect="snowflake",
        namespace=SHIPPING,
    ).sql
    assert '"WCM_DEMO"."SHIPPING"."DIM_VESSELS"' in sql
    assert '"WCM_DEMO"."SHIPPING"."STG_VOYAGES"' in sql


def test_masks_and_bounds_rows() -> None:
    bounded = mask_rows(
        ["id", "Contact_Email", "note"],
        [(1, "a@b.c", "x" * 50), (2, None, "short")],
        {"contact_email"},
        max_cell_chars=10,
    )
    assert bounded.rows == [
        {"id": 1, "Contact_Email": MASK, "note": "x" * 10 + TRUNCATED},
        {"id": 2, "Contact_Email": None, "note": "short"},
    ]
    assert bounded.truncated


def test_total_result_size_is_bounded() -> None:
    bounded = mask_rows(["v"], [("y" * 100,)] * 50, set(), max_bytes=1_000)
    assert 0 < len(bounded.rows) < 50
    assert bounded.truncated


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
