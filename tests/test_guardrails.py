"""Each guardrail has a test that fails if that guard is removed."""

from __future__ import annotations

import pytest

from warehouse_context_mcp.guardrails import MASK, QueryRejected, guard, mask_rows

PII = {"dim_charterers": {"contact_email"}}


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
    with pytest.raises(QueryRejected):
        guard(sql)


def test_rejects_unparseable_sql() -> None:
    with pytest.raises(QueryRejected):
        guard("selec * fro")


def test_adds_limit_when_missing() -> None:
    assert "LIMIT 10" in guard("select * from dim_vessels", max_rows=10).sql


def test_tightens_a_limit_above_the_cap() -> None:
    sql = guard("select * from dim_vessels limit 5000", max_rows=10).sql
    assert "LIMIT 10" in sql and "5000" not in sql


def test_keeps_a_limit_below_the_cap() -> None:
    assert "LIMIT 3" in guard("select * from dim_vessels limit 3", max_rows=10).sql


def test_limits_set_operations() -> None:
    sql = guard("select 1 as a union all select 2 as a", max_rows=1).sql
    assert sql.rstrip().endswith("LIMIT 1")


def test_reports_tables() -> None:
    q = guard("select v.vessel_name from dim_vessels v join stg_voyages s using (vessel_id)")
    assert q.tables == {"dim_vessels", "stg_voyages"}


def test_rejects_reading_pii_even_when_aliased() -> None:
    with pytest.raises(QueryRejected):
        guard("select contact_email as e from dim_charterers", pii=PII)


def test_rejects_filtering_on_pii() -> None:
    with pytest.raises(QueryRejected):
        guard("select charterer_id from dim_charterers where contact_email like 'a%'", pii=PII)


def test_rejects_pii_inside_a_subquery() -> None:
    with pytest.raises(QueryRejected):
        guard("select x from (select contact_email as x from dim_charterers)", pii=PII)


def test_allows_counting_pii() -> None:
    guard("select count(distinct contact_email) from dim_charterers", pii=PII)


def test_pii_rule_only_applies_to_tables_in_the_query() -> None:
    guard("select contact_email from some_other_table", pii=PII)


def test_masks_pii_columns_in_results() -> None:
    rows = mask_rows(["id", "Contact_Email"], [(1, "a@b.c"), (2, None)], {"contact_email"})
    assert rows == [{"id": 1, "Contact_Email": MASK}, {"id": 2, "Contact_Email": None}]
