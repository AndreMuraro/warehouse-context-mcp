# Changelog

## [0.4.1] - 2026-11-03

### Fixed
- `--demo` on Windows: when two clients started at the same time, the second could fail
  with "Access is denied" because Windows cannot replace a file another process has open.
  The second client now uses the file the first one published.

### Changed
- The server reports its package version, a title and its website to MCP clients.

## [0.4.0] - 2026-11-03

First public release.

### Added
- MCP server with six read-only tools: `list_tables`, `describe_table`, `table_lineage`,
  `table_freshness`, `sample_rows`, `run_query`, with typed inputs and outputs.
- Backends: DuckDB, Snowflake (key pair, password, browser sign-in or a Snowflake CLI
  connection) and Databricks Unity Catalog; warehouse comments and PII tags merged with a
  curated YAML catalog.
- `--demo`: a bundled synthetic warehouse built on first run, for zero-setup trials.
- Streamable HTTP transport (`--transport http`) with a health endpoint; hardened container
  image and Compose setup.
- Client configurations for Claude Code, VS Code and Codex; a `warehouse-context` agent
  skill; a Claude Code plugin marketplace.

### Security
- Allow-list query policy: one SELECT built from reviewed constructs and functions; every
  column resolved against the real schema; PII masked inside the SQL and usable only in
  `COUNT`; identifiers compared after dialect normalisation. See ADR 0004.
- Locked-down connections (DuckDB without external access, Snowflake without secondary
  roles or admin roles), bounded results, engine errors withheld for PII tables, PII tags
  required at startup.
- Attack queries from two independent adversarial reviews kept as a regression suite.
- Supply chain: pinned Actions, cache-free release workflow, PyPI trusted publishing with
  attestations, image provenance and SBOM, dependency, workflow, Dockerfile and image
  scanning in CI.
