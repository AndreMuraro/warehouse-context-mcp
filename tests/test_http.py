"""Streamable HTTP transport: works for local clients, refuses foreign Host headers."""

from collections.abc import Generator
from typing import Any

import pytest
from starlette.testclient import TestClient

from warehouse_context_mcp import __version__
from warehouse_context_mcp.server import build_server, http_app
from warehouse_context_mcp.service import WarehouseContext

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Host": "localhost:8000"}


def rpc(method: str, params: dict[str, Any] | None = None, id_: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


INITIALIZE = rpc(
    "initialize",
    {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
)


@pytest.fixture
def client(ctx: WarehouseContext) -> Generator[TestClient]:
    app = http_app(build_server(ctx), allowed_hosts=["mcp.internal"])
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "duckdb"}


def test_initialize_and_call_a_tool_over_http(client: TestClient) -> None:
    init = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert init.status_code == 200
    info = init.json()["result"]["serverInfo"]
    assert info["name"] == "warehouse-context"
    assert info["version"] == __version__

    call = client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "run_query", "arguments": {"sql": "drop table x"}}, 2),
        headers=MCP_HEADERS | {"Mcp-Protocol-Version": "2025-06-18"},
    )
    assert call.status_code == 200
    assert call.json()["result"]["structuredContent"]["result"]["ok"] is False


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "192.168.1.20:8000"])
def test_foreign_host_headers_are_refused(client: TestClient, host: str) -> None:
    """DNS-rebinding protection stays on even though a container binds 0.0.0.0."""
    response = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS | {"Host": host})
    assert response.status_code in {400, 421}


def test_explicitly_allowed_hosts_are_served(client: TestClient) -> None:
    headers = MCP_HEADERS | {"Host": "mcp.internal:8000"}
    assert client.post("/mcp", json=INITIALIZE, headers=headers).status_code == 200
