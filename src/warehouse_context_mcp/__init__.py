"""Governed warehouse context for AI agents, served over MCP."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("warehouse-context-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
