"""Build a small, fully synthetic shipping warehouse for demos and tests.

Usage: uv run examples/build_demo.py examples/demo.duckdb
Every value is generated from a fixed seed; no real vessels, companies or people.
"""

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

PORTS = ["Rotterdam", "Singapore", "Houston", "Santos", "Fujairah", "Ningbo", "Antwerp", "Durban"]
CLASSES = ["VLCC", "Suezmax", "Aframax", "LR2", "MR"]


def build(path: Path, now: datetime | None = None) -> None:
    rng = random.Random(42)
    now = now or datetime(2026, 11, 1, 12, 0, 0)  # noqa: DTZ001  naive UTC, like most warehouses
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


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "examples/demo.duckdb"))
