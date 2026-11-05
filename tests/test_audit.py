"""The audit log: one JSON line per tool call, appended, never holding result rows."""

import asyncio
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from warehouse_context_mcp import cli
from warehouse_context_mcp.audit import AuditLog
from warehouse_context_mcp.server import build_server
from warehouse_context_mcp.service import WarehouseContext

T0 = datetime(2026, 11, 5, 12, tzinfo=UTC)


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_tool_call_is_recorded(ctx: WarehouseContext, tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock=lambda: T0)
    server = build_server(ctx, log)
    calls = [
        ("list_tables", {}),
        ("describe_table", {"table": "dim_vessels"}),
        ("run_query", {"sql": "select vessel_class from dim_vessels", "max_rows": 3}),
        ("run_query", {"sql": "delete from dim_vessels"}),
        ("run_query", {"sql": "select contact_email from dim_charterers"}),
    ]
    for name, arguments in calls:
        asyncio.run(server.call_tool(name, arguments))
    with pytest.raises(Exception, match="Error executing tool sample_rows"):
        asyncio.run(server.call_tool("sample_rows", {"table": "nope"}))
    log.close()

    entries = lines(path)
    assert [e["tool"] for e in entries] == [*(c[0] for c in calls), "sample_rows"]
    assert [e["outcome"] for e in entries] == ["ok", "ok", "ok", "refused", "ok", "error"]
    assert entries[0]["items"] == 6
    query = entries[2]
    assert (query["rows"], query["truncated"], query["arguments"]["max_rows"]) == (3, False, 3)
    assert query["sql"].endswith("LIMIT 3")
    assert query["ts"] == "2026-11-05T12:00:00.000+00:00"
    assert query["duration_ms"] >= 0
    assert "only SELECT" in entries[3]["error"]
    assert entries[5]["error"] == "unknown table: nope"


def test_result_rows_are_never_logged(ctx: WarehouseContext, tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    server = build_server(ctx, log)
    asyncio.run(server.call_tool("run_query", {"sql": "select * from dim_charterers"}))
    asyncio.run(server.call_tool("sample_rows", {"table": "dim_vessels"}))
    log.close()
    text = path.read_text(encoding="utf-8")
    assert "Charterer A" not in text
    assert "MV Example" not in text
    assert "@charterer" not in text


def test_appends_and_never_truncates(ctx: WarehouseContext, tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text('{"earlier": true}\n', encoding="utf-8")
    for _ in range(2):  # two server starts
        log = AuditLog(path)
        asyncio.run(build_server(ctx, log).call_tool("list_tables", {}))
        log.close()
    assert lines(path)[0] == {"earlier": True}
    assert len(lines(path)) == 3


def test_concurrent_calls_write_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    long = "x" * 5_000  # longer than a pipe buffer, so torn writes would show

    def call(i: int) -> None:
        log.call("run_query", {"sql": f"{i}:{long}"}, lambda: None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call, range(200)))
    log.close()
    entries = lines(path)  # every line parses
    assert {e["arguments"]["sql"].split(":")[0] for e in entries} == {str(i) for i in range(200)}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_new_log_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "audit.jsonl"
    AuditLog(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cli_flags_reach_the_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.x.dim_vessels": {
                        "resource_type": "model",
                        "name": "dim_vessels",
                        "description": "From dbt.",
                        "columns": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    seen: dict[str, Any] = {}

    def fake_run(self: object) -> None:
        del self
        server = seen["server"]
        result = asyncio.run(server.call_tool("list_tables", {}))
        seen["tables"] = result.structured_content["result"]

    original = cli.build_server

    def capture(ctx: WarehouseContext, audit: AuditLog | None = None) -> object:
        server = original(ctx, audit)
        seen["server"] = server
        return server

    monkeypatch.setattr(cli, "build_server", capture)
    monkeypatch.setattr("mcp.server.mcpserver.MCPServer.run", fake_run)
    monkeypatch.setenv("WCM_DEMO_DIR", str(tmp_path / "demo"))
    audit = tmp_path / "audit.jsonl"
    argv = ["--demo", "--dbt-manifest", str(manifest), "--audit-log", str(audit)]
    assert cli.main(argv) == 0

    vessels = next(t for t in seen["tables"] if t["table"] == "dim_vessels")
    # The demo's curated catalog wins over dbt where both describe a table.
    assert vessels["description"] == "One row per vessel in the fleet register."
    assert lines(audit)[0]["tool"] == "list_tables"
