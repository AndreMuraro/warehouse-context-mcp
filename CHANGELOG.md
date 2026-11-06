# Changelog

## [0.5.1] - Unreleased

### Fixed
- `ORDER BY` on an output that is the same column as its name is no longer refused:
  `SELECT country ... ORDER BY country`, `ORDER BY 1`, `ORDER BY t.country` and
  expressions over it. Both readings of the name are that one column, provided no other
  table in the query has a column of that name and it is not PII. Found by the
  evaluation: it refused 3 of the 20 reference queries, and 3 of Kimi K3's 80 server
  queries before it rewrote them. Every attack query from both reviews stays refused, plus
  five new variants of the exception.

## [0.5.0] - 2026-11-05

First results: Kimi K3 scored 92% with raw SQL, 95% with the catalog in the prompt and
100% through the server, with 11, 3 and 0 runs exposing PII. See README > Evaluation.

### Added
- Evaluation harness in `evals/`: 20 analyst questions over the demo warehouse, answered
  under three arms (raw SQL, raw SQL with the catalog in the prompt, this server) and
  scored for accuracy, wrong-table picks and PII exposures. Works with any Anthropic- or
  OpenAI-compatible API: presets for Anthropic, OpenAI, OpenRouter, Moonshot (Kimi), Groq,
  Together, Mistral, DeepSeek, Gemini and Ollama, or `--base-url`. Cached, resumable runs;
  `--jobs` for parallel questions, `--questions`/`--arms`/`--redo` to run part of a suite,
  `--rescore` to recompute verdicts from transcripts; `--dry-run` needs no key. See ADR 0005.
- `.env.example` lists the evaluation's provider keys, `SNOWFLAKE_STATEMENT_TIMEOUT_S` and
  `DATABRICKS_ALLOW_CUSTOM_HOST`; a test keeps it in step with the settings.
- `--dbt-manifest`: table and column docs, owners, grain, lineage, freshness and PII tags
  from a dbt `manifest.json`, layered between warehouse comments and the curated YAML.
- `--audit-log`: an append-only JSON-lines record of every tool call (arguments, outcome,
  duration, row count, executed SQL; never result rows), created owner-only.
- Demo warehouse: two decoy tables, `legacy_voyage_pnl` (deprecated) and
  `raw_fixture_messages` (one row per amendment), documented in the catalog. Existing
  demo tables keep every value.

### Changed
- `stg_voyages` now lists `raw_fixture_messages` upstream in the demo catalog.

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
