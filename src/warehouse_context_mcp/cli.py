"""Command line entry point: ``warehouse-context-mcp`` (or ``python -m warehouse_context_mcp``)."""

import argparse
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import uvicorn

from . import __version__
from .demo import ensure_demo
from .domain.catalog import Catalog
from .domain.errors import ConfigurationError
from .server import build_server, http_app
from .service import Limits, WarehouseContext
from .warehouses import Backend, open_warehouse


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warehouse-context-mcp",
        description="Serve governed warehouse context to AI agents over MCP.",
    )
    parser.add_argument(
        "--backend",
        type=Backend,
        choices=list(Backend),
        default=Backend.DUCKDB,
        help="snowflake and databricks read SNOWFLAKE_* / DATABRICKS_* settings",
    )
    parser.add_argument("--duckdb", type=Path, help="DuckDB database file (duckdb backend)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="serve the bundled synthetic warehouse, built on first use (no setup needed)",
    )
    parser.add_argument("--catalog", type=Path, help="curated catalog YAML file")
    parser.add_argument("--max-rows", type=int, default=200, help="hard cap on rows returned")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="optional KEY=VALUE settings file"
    )
    http = parser.add_argument_group("transport")
    http.add_argument(
        "--transport",
        type=Transport,
        choices=list(Transport),
        default=Transport.STDIO,
        help="stdio: launched by an MCP client (default); http: Streamable HTTP service",
    )
    http.add_argument("--host", default="127.0.0.1", help="http: interface to bind")
    http.add_argument("--port", type=int, default=8000, help="http: port to listen on")
    http.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="NAME",
        help="http: extra Host header value to accept besides localhost (repeatable)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.demo:
        if args.duckdb or args.backend is not Backend.DUCKDB:
            parser.error("--demo cannot be combined with --duckdb or another --backend")
        args.duckdb, demo_catalog = ensure_demo()
        args.catalog = args.catalog or demo_catalog
    try:
        warehouse = open_warehouse(args.backend, duckdb_path=args.duckdb, env_file=args.env_file)
    except ConfigurationError as err:
        print(f"warehouse-context-mcp: {err}", file=sys.stderr)  # noqa: T201
        return 2
    try:
        ctx = WarehouseContext(
            warehouse, Catalog.load(args.catalog), Limits(max_rows=args.max_rows)
        )
        server = build_server(ctx)
        match args.transport:
            case Transport.STDIO:
                server.run()
            case Transport.HTTP:
                app = http_app(server, allowed_hosts=args.allowed_host)
                uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        warehouse.close()
    return 0
