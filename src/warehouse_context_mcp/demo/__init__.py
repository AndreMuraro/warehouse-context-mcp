"""A small, fully synthetic shipping warehouse for demos and tests.

Every value is generated from a fixed seed; no real vessels, companies or people.

    python -m warehouse_context_mcp.demo DIR    # writes DIR/demo.duckdb and DIR/catalog.yaml
"""

import os
import random
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import duckdb
import yaml

from warehouse_context_mcp import __version__
from warehouse_context_mcp.domain.catalog import Catalog

DATABASE = "demo.duckdb"
CATALOG = "catalog.yaml"

PORTS = ["Rotterdam", "Singapore", "Houston", "Santos", "Fujairah", "Ningbo", "Antwerp", "Durban"]
CLASSES = ["VLCC", "Suezmax", "Aframax", "LR2", "MR"]

type Voyage = tuple[int, int, int, str, str, date, int, datetime]


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
    voyages: list[Voyage] = []
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
    _add_decoys(con, voyages, now)
    con.close()


def _add_decoys(con: duckdb.DuckDBPyConnection, voyages: Sequence[Voyage], now: datetime) -> None:
    """Tables a careful analyst avoids; the catalog says why, the schema does not.

    They give the evaluation (``evals/``) something to get wrong: a deprecated table with
    older definitions and a raw feed where a voyage appears once per amendment. Their own
    seed keeps every value in the tables above unchanged.
    """
    rng = random.Random(7)  # noqa: S311  reproducible synthetic data, not security
    for t in ["legacy_voyage_pnl", "raw_fixture_messages"]:
        con.execute(f"drop table if exists {t}")

    # Frozen when the economics model was replaced: older voyages only, revenue net of a
    # 5% address commission, and ballast days counted in the duration.
    con.execute(
        """create table legacy_voyage_pnl as
        select voyage_id, vessel_id,
               round(revenue_usd * 0.95, 2) as revenue_usd,
               voyage_costs_usd as costs_usd,
               duration_days + 6 as duration_days,
               round((revenue_usd * 0.95 - voyage_costs_usd) / (duration_days + 6), 2)
                   as tce_usd_per_day,
               ?::timestamp as computed_at
        from fct_voyage_economics
        where voyage_id <= 350""",
        [now - timedelta(days=140)],
    )

    con.execute("""create table raw_fixture_messages (
        message_id integer, voyage_id integer, vessel_id integer, charterer_id integer,
        cargo_tonnes integer, received_at timestamp)""")
    messages: list[tuple[object, ...]] = []
    for voyage_id, vessel_id, charterer_id, *_, cargo, loaded_at in voyages:
        amendments = rng.choice([0, 0, 1, 1, 2])
        # Earlier messages carry the quantities later amended; the last one is current.
        for k in range(amendments, -1, -1):
            tonnes = cargo if k == 0 else cargo + rng.choice([-1, 1]) * rng.randint(1, 20) * 1_000
            received = loaded_at - timedelta(hours=6 * k + 1)
            messages.append(
                (len(messages) + 1, voyage_id, vessel_id, charterer_id, tonnes, received)
            )
    con.executemany("insert into raw_fixture_messages values (?, ?, ?, ?, ?, ?)", messages)


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
            _publish(Path(partial), database)
        finally:
            Path(partial).unlink(missing_ok=True)
    catalog = target / CATALOG
    if not catalog.exists():
        fd, partial = tempfile.mkstemp(dir=target, suffix=".yaml")
        try:
            with files(__package__).joinpath(CATALOG).open("rb") as src, os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # mkstemp creates owner-only files; the catalog is not secret, and in the image it
            # is built as root but read by an unprivileged user.
            Path(partial).chmod(0o644)
            _publish(Path(partial), catalog)
        finally:
            Path(partial).unlink(missing_ok=True)
    return database, catalog


def _publish(partial: Path, target: Path) -> None:
    """Rename a finished file into place, unless another process already published it.

    On Windows a file that another process has open cannot be replaced, so when two
    clients start at once the second rename fails; its copy is identical and is dropped.
    """
    try:
        partial.replace(target)
    except PermissionError:
        if not target.exists():
            raise


def bundled_catalog() -> Catalog:
    """The curated catalog for the demo tables, which the warehouse demo scripts mirror."""
    return Catalog.model_validate(yaml.safe_load(files(__package__).joinpath(CATALOG).read_text()))


def default_demo_dir() -> Path:
    if configured := os.environ.get("WCM_DEMO_DIR"):
        return Path(configured)
    return Path(tempfile.gettempdir()) / "warehouse-context-mcp" / __version__
