"""Release metadata stays consistent, so a version tag publishes cleanly everywhere."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_server_json_versions_match_the_package() -> None:
    assert SERVER["version"] == PROJECT["version"]
    assert {p["version"] for p in SERVER["packages"]} == {PROJECT["version"]}
    assert {p["identifier"] for p in SERVER["packages"]} == {PROJECT["name"]}


def test_readme_proves_ownership_for_the_registry() -> None:
    # The registry looks for this marker in the PyPI description (the README).
    readme = (ROOT / PROJECT["readme"]).read_text(encoding="utf-8")
    marker = re.search(r"mcp-name: (\S+?)\s*(?:-->|$)", readme, re.MULTILINE)
    assert marker
    assert marker.group(1) == SERVER["name"]


def test_registry_limits() -> None:
    # Namespace matching is case-sensitive and must equal the GitHub owner exactly.
    assert SERVER["name"].startswith("io.github.AndreMuraro/")
    assert 1 <= len(SERVER["description"]) <= 100


def test_client_configs_launch_the_demo() -> None:
    claude = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    vscode = json.loads((ROOT / ".vscode" / "mcp.json").read_text(encoding="utf-8"))["servers"]
    codex = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
    for server in (
        claude["warehouse-demo"],
        vscode["warehouse-demo"],
        codex["mcp_servers"]["warehouse-demo"],
    ):
        assert server["command"] == "uv"
        assert server["args"][-2:] == ["warehouse-context-mcp", "--demo"]
