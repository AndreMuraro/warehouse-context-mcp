"""A small, fully synthetic shipping warehouse for demos and tests.

Every value is generated from a fixed seed; no real vessels, companies or people.

    python -m warehouse_context_mcp.demo DIR    # writes DIR/demo.duckdb and DIR/catalog.yaml
"""

import os
import random
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import duckdb

from warehouse_context_mcp import __version__

DATABASE = "demo.duckdb"
CATALOG = "catalog.yaml"

PORTS = ["Rotterdam", "Singapore", "Houston", "Santos", "Fujairah", "Ningbo", "Antwerp", "Durban"]
CLASSES = ["VLCC", "Suezmax", "Aframax", "LR2", "MR"]


def build(path: Path, now: datetime | None = None) -> None:
    rng = random.Random(42)  # noqa: S311  reproducible synthetic data, not security
    # Naive UTC timestamps, like most warehouses.
    now = now or datetime.now(UTC).replace(tzinfo=None)
    con = duckdb.connect(str(path))
    for t in ["fct_voyage_economics", "stg_voyages", "dim_vessels", "dim_charterers"]:
        con.execute(f"drop table if exists {t}")

    con.execute("""create table dim_vessels (
        vessel_id integer, vessel_name varchar, vessel_class varchar, dwt_tonnes integer,
        built_year integer, updated_at timestamp)""")
    vessels = [
        (
            i,
            f"MV Example {i:03d}",
            rng.choice(CLASSES),
            rng.randint(40_000, 320_000),
            rng.randint(2005, 2024),
            now - timedelta(hours=rng.randint(1, 20)),
        )
        for i in range(1, 41)
    ]
    con.executemany("insert into dim_vessels values (?, ?, ?, ?, ?, ?)", vessels)

    con.execute("""create table dim_charterers (
        charterer_id integer, charterer_name varchar, contact_email varchar,
        country varchar, updated_at timestamp)""")
    charterers = [
        (
            i,
            f"Charterer {chr(64 + i)}",
            f"ops{i}@charterer-{i}.example",
            rng.choice(["NL", "SG", "US", "BR", "AE"]),
            now - timedelta(days=rng.randint(1, 60)),
        )
        for i in range(1, 13)
    ]
    con.executemany("insert into dim_charterers values (?, ?, ?, ?, ?)", charterers)

    con.execute("""create table stg_voyages (
        voyage_id integer, vessel_id integer, charterer_id integer, load_port varchar,
        discharge_port varchar, laycan_start date, cargo_tonnes integer, loaded_at timestamp)""")
    voyages = []
    for v in range(1, 501):
        load, dis = rng.sample(PORTS, 2)
        start = (now - timedelta(days=rng.randint(1, 365))).date()
        voyages.append(
            (
                v,
                rng.randint(1, 40),
                rng.randint(1, 12),
                load,
                dis,
                start,
                rng.randint(30_000, 280_000),
                now - timedelta(hours=rng.randint(1, 30)),
            )
        )
    con.executemany("insert into stg_voyages values (?, ?, ?, ?, ?, ?, ?, ?)", voyages)

    con.execute("""create table fct_voyage_economics as
        select voyage_id, vessel_id, charterer_id,
               round(cargo_tonnes * (8 + (voyage_id % 17)) / 1.0, 2) as revenue_usd,
               round(cargo_tonnes * (5 + (voyage_id % 11)) / 1.0, 2) as voyage_costs_usd,
               20 + (voyage_id % 25) as duration_days,
               loaded_at as computed_at
        from stg_voyages""")
    con.execute("""alter table fct_voyage_economics add column tce_usd_per_day double""")
    con.execute("""update fct_voyage_economics
        set tce_usd_per_day = round((revenue_usd - voyage_costs_usd) / duration_days, 2)""")
    con.close()


def ensure_demo(directory: Path | None = None) -> tuple[Path, Path]:
    """Return the demo database and catalog paths, building them on first use.

    The default location is a per-version folder in the system temp directory, or
    ``WCM_DEMO_DIR``. Each file is written under a temporary name and renamed into place,
    so two clients starting at once never read a half-written file.
    """
    target = directory or default_demo_dir()
    target.mkdir(parents=True, exist_ok=True)
    database = target / DATABASE
    if not database.exists():
        fd, partial = tempfile.mkstemp(dir=target, suffix=".duckdb")
        os.close(fd)
        Path(partial).unlink()  # DuckDB wants to create the file itself
        try:
            build(Path(partial))
            Path(partial).replace(database)
        finally:
            Path(partial).unlink(missing_ok=True)
    catalog = target / CATALOG
    if not catalog.exists():
        fd, partial = tempfile.mkstemp(dir=target, suffix=".yaml")
        try:
            with files(__package__).joinpath(CATALOG).open("rb") as src, os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
            Path(partial).replace(catalog)
        finally:
            Path(partial).unlink(missing_ok=True)
    return database, catalog


def default_demo_dir() -> Path:
    if configured := os.environ.get("WCM_DEMO_DIR"):
        return Path(configured)
    return Path(tempfile.gettempdir()) / "warehouse-context-mcp" / __version__
