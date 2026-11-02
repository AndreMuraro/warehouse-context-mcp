"""Command line entry point: ``warehouse-context-mcp`` (or ``python -m warehouse_context_mcp``)."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .domain.catalog import Catalog
from .domain.errors import ConfigurationError
from .server import build_server
from .service import Limits, WarehouseContext
from .warehouses import Backend, open_warehouse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warehouse-context-mcp",
        description="Serve governed warehouse context to AI agents over MCP (stdio).",
    )
    parser.add_argument(
        "--backend",
        type=Backend,
        choices=list(Backend),
        default=Backend.DUCKDB,
        help="snowflake and databricks read SNOWFLAKE_* / DATABRICKS_* settings",
    )
    parser.add_argument("--duckdb", type=Path, help="DuckDB database file (duckdb backend)")
    parser.add_argument("--catalog", type=Path, help="curated catalog YAML file")
    parser.add_argument("--max-rows", type=int, default=200, help="hard cap on rows returned")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="optional KEY=VALUE settings file"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        warehouse = open_warehouse(args.backend, duckdb_path=args.duckdb, env_file=args.env_file)
    except ConfigurationError as err:
        print(f"warehouse-context-mcp: {err}", file=sys.stderr)  # noqa: T201
        return 2
    try:
        ctx = WarehouseContext(
            warehouse, Catalog.load(args.catalog), Limits(max_rows=args.max_rows)
        )
        build_server(ctx).run()
    finally:
        warehouse.close()
    return 0
