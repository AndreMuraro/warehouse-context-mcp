"""dbt manifest import: docs, owners, lineage, freshness and PII onto the catalog."""

import json
from pathlib import Path
from typing import Any

import pytest

from warehouse_context_mcp.domain import Catalog, catalog_from_manifest, load_manifest
from warehouse_context_mcp.service import WarehouseContext
from warehouse_context_mcp.warehouses import DuckDBWarehouse

MANIFEST: dict[str, Any] = {
    "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
    "sources": {
        "source.shipping.fixtures.voyages": {
            "resource_type": "source",
            "name": "voyages",
            "identifier": "RAW_FIXTURE_MESSAGES",
            "description": "Raw   fixture\nmessages.",
            "loaded_at_field": "received_at",
            "freshness": {"warn_after": {"count": 2, "period": "day"}, "error_after": {}},
            "columns": {},
            "meta": {"owner": "fleet-data"},
        }
    },
    "nodes": {
        "model.shipping.stg_voyages": {
            "resource_type": "model",
            "name": "stg_voyages",
            "alias": "stg_voyages",
            "description": "Voyages, deduplicated.",
            "config": {"meta": {"owner": "fleet-data", "grain": "voyage_id"}},
            "depends_on": {
                "nodes": ["source.shipping.fixtures.voyages", "model.shipping.dim_charterers"]
            },
            "columns": {"VOYAGE_ID": {"name": "VOYAGE_ID", "description": "Fixture id."}},
            "meta": {"freshness_column": "loaded_at", "expected_freshness_hours": 12},
        },
        "model.shipping.dim_charterers": {
            "resource_type": "model",
            "name": "charterers",
            "alias": "dim_charterers",
            "description": "Charterers.",
            "depends_on": {"nodes": []},
            "columns": {
                "contact_email": {"name": "contact_email", "meta": {"contains_pii": True}},
                "phone": {"name": "phone", "tags": ["PII"]},
                "country": {"name": "country", "description": "ISO code."},
            },
        },
        "test.shipping.not_null_stg_voyages_voyage_id": {
            "resource_type": "test",
            "name": "not_null_stg_voyages_voyage_id",
            "depends_on": {"nodes": ["model.shipping.stg_voyages"]},
        },
        "exposure.shipping.dashboard": {"resource_type": "exposure", "name": "dashboard"},
    },
}


def test_tables_are_named_by_relation_and_carry_docs() -> None:
    cat = catalog_from_manifest(MANIFEST)
    assert set(cat.tables) == {"raw_fixture_messages", "stg_voyages", "dim_charterers"}
    stg = cat.table("stg_voyages")
    assert (stg.description, stg.owner, stg.grain) == (
        "Voyages, deduplicated.",
        "fleet-data",
        "voyage_id",
    )
    assert stg.columns["voyage_id"].description == "Fixture id."
    assert cat.table("raw_fixture_messages").description == "Raw fixture messages."


def test_lineage_comes_from_depends_on() -> None:
    cat = catalog_from_manifest(MANIFEST)
    assert cat.upstream("stg_voyages") == ["raw_fixture_messages", "dim_charterers"]
    assert cat.downstream("dim_charterers") == ["stg_voyages"]  # tests are not tables


def test_freshness_from_sources_and_meta() -> None:
    cat = catalog_from_manifest(MANIFEST)
    raw = cat.table("raw_fixture_messages")
    assert (raw.freshness_column, raw.expected_freshness_hours) == ("received_at", 48.0)
    stg = cat.table("stg_voyages")
    assert (stg.freshness_column, stg.expected_freshness_hours) == ("loaded_at", 12.0)


def test_pii_from_column_meta_or_tags() -> None:
    assert catalog_from_manifest(MANIFEST).table("dim_charterers").pii_columns == {
        "contact_email",
        "phone",
    }


def test_curated_catalog_wins_but_pii_is_never_dropped() -> None:
    curated = Catalog.model_validate(
        {
            "tables": {
                "dim_charterers": {
                    "description": "Reviewed description.",
                    "columns": {"contact_email": {"description": "Ops contact.", "pii": False}},
                }
            }
        }
    )
    merged = catalog_from_manifest(MANIFEST).merged_with(curated)
    doc = merged.table("dim_charterers")
    assert doc.description == "Reviewed description."
    assert doc.columns["country"].description == "ISO code."
    assert "contact_email" in doc.pii_columns


def test_load_manifest_from_file(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    assert "stg_voyages" in load_manifest(path).tables
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="not a dbt manifest"):
        load_manifest(path)


def test_dbt_docs_reach_the_agent(warehouse: DuckDBWarehouse) -> None:
    ctx = WarehouseContext(warehouse, catalog_from_manifest(MANIFEST))
    described = ctx.describe_table("dim_charterers")
    assert {c.name for c in described.columns if c.pii} == {"contact_email"}
    assert ctx.lineage("stg_voyages").upstream == ["raw_fixture_messages", "dim_charterers"]
    masked = ctx.query("select contact_email from dim_charterers", max_rows=1)
    assert "@" not in str(masked)
