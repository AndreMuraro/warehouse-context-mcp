"""``python -m warehouse_context_mcp.demo [DIR]``: build the demo and print its paths."""

import sys
from pathlib import Path

from . import ensure_demo

database, catalog = ensure_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
print(f"{database}\n{catalog}")  # noqa: T201
