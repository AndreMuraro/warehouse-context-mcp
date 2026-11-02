# warehouse-context-mcp

An [MCP](https://modelcontextprotocol.io) server that gives AI agents **trustworthy, governed context about a data warehouse**: what each table means, where it comes from, how fresh it is, and a way to query it that cannot write, cannot run away, and cannot leak personal data.

Coding and analytics agents are good at SQL and bad at knowing *which* table to trust. Schema alone does not tell them that `fct_voyage_economics` is the governed source for TCE, that `stg_voyages` is staging, that `contact_email` is personal data, or that a table stopped updating two days ago. This server puts that context one tool call away, and puts guardrails around everything else.

## Tools

| Tool | What the agent gets |
| --- | --- |
| `list_tables` | Every table with its documented purpose and owner |
| `describe_table` | Columns, types, descriptions, PII flags, grain, row count, undocumented columns |
| `table_lineage` | Upstream sources and downstream consumers, cycle-safe, depth-limited |
| `table_freshness` | Last update, age, and whether it meets the documented expectation |
| `sample_rows` | A few example rows, PII masked |
| `run_query` | One read-only SELECT, row-limited, with PII protected |

## Backends

| Backend | Status | Native context it reads | Read-only connection layer |
| --- | --- | --- | --- |
| DuckDB | v0.1 | none (YAML catalog only) | file opened `read_only` |
| Snowflake | v0.2 | table/column comments, `pii` object tags | a SELECT-only role (`WCM_READER` in the demo script) |
| Databricks | v0.2 | Unity Catalog comments, `pii` column tags | a principal with only `USE` + `SELECT` grants |

Comments and tags stored in the warehouse are merged with the curated YAML catalog: curated values win, native ones fill the gaps, and a PII flag from either source sticks.

## Guardrails

Read-only is enforced in **three independent layers**, each tested on its own:

1. **Parsing, not string matching.** SQL is parsed with [sqlglot](https://github.com/tobymao/sqlglot). It must be exactly one statement whose root is a SELECT or a set operation, and no node in the tree may be a write, DDL, `SELECT … INTO`, or an unmodelled command (`COPY`, `ATTACH`, `PRAGMA`, `SET`).
2. **Read-only connection.** DuckDB opens the file read-only; Snowflake and Databricks connect with a SELECT-only role or principal, so a guardrail bug still cannot write.
3. **Bounded results.** A row limit is always applied (tightened if the query asks for more) and queries are interrupted after a timeout.

**Scope**: queries may only touch tables in the configured schema. Anything qualified with another database or schema is refused, and unqualified names are rewritten to fully qualified ones, so a query never depends on the session's default schema.

**PII**: columns flagged `pii: true` in the catalog can be counted (`COUNT(DISTINCT email)`) but never read, filtered on or grouped by, including through aliases and subqueries. `SELECT *` is allowed; PII values come back masked.

See [docs/adr/0001-read-only-by-construction.md](docs/adr/0001-read-only-by-construction.md) for the reasoning.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14 or 3.15 (uv installs it if missing).

```bash
uv sync                                         # creates .venv from uv.lock
uv run examples/build_demo.py examples/demo.duckdb   # fully synthetic shipping data
uv run pytest
```

Connect it to Claude Code:

```bash
claude mcp add warehouse -- uv run --directory "$PWD" warehouse-context-mcp \
  --duckdb examples/demo.duckdb --catalog examples/catalog.yaml
```

Then ask: *"Which vessel class had the best average TCE last quarter, and is that table fresh?"*

### Snowflake or Databricks

```bash
uv sync --extra snowflake                # or --extra databricks, or --all-extras
cp .env.example .env                     # fill in your own values; .env is git-ignored
# run examples/snowflake_demo.sql (or databricks_demo.sql) once in the warehouse
uv run warehouse-context-mcp --backend snowflake --catalog examples/catalog.yaml
```

Settings come from `SNOWFLAKE_*` / `DATABRICKS_*` environment variables or `./.env`; real environment variables win, and blank values count as unset. Key-pair auth for Snowflake: `uv run scripts/snowflake_keypair.py` prints the one `ALTER USER` statement to run.

Live tests run the demo expectations against a real warehouse and are skipped otherwise:

```bash
WCM_LIVE=snowflake uv run pytest -m live
```

On Windows the same commands work in PowerShell; set the variable with `$env:WCM_LIVE = "snowflake"`.

On the Databricks Free Edition the personal access token belongs to the workspace admin, so the read-only connection layer is not available there; the query policy still applies. In a real workspace, use a service principal with only `USE` and `SELECT` grants.

### Python versions

| | 3.14 | 3.15 |
| --- | --- | --- |
| Core, DuckDB | ✓ | ✓ |
| Databricks extra | ✓ | ✓ (lz4 and thrift build from source) |
| Snowflake extra | ✓ | not yet: no 3.15 wheels, and the source build fails; the extra is skipped by an environment marker |

## The catalog

Meaning lives in a YAML file reviewed in Git like code ([example](examples/catalog.yaml)):

```yaml
tables:
  fct_voyage_economics:
    description: Time-charter-equivalent economics per voyage.
    owner: freight-analytics
    grain: voyage_id
    upstream: [stg_voyages]
    freshness_column: computed_at
    expected_freshness_hours: 12
    columns:
      tce_usd_per_day: { description: TCE in USD per day. }
```

In production it would be generated from dbt docs, Unity Catalog comments or a data catalog.

## Architecture

```
src/warehouse_context_mcp/
├── domain/        catalog, result models, namespace, errors (no I/O beyond YAML)
├── guardrails/    QueryPolicy: parse, scope, PII rules, row limit; result masking
├── warehouses/    Warehouse protocol + DuckDB, Snowflake, Databricks adapters
├── service.py     WarehouseContext: combines the three; no MCP dependency
├── server.py      MCP transport: six read-only tools with typed inputs and outputs
├── settings.py    pydantic-settings for connection config and secrets
└── cli.py         entry point
```

Dependencies point inwards: the transport knows the service, the service knows the domain, guardrails and the `Warehouse` protocol, and nothing in the domain knows about MCP or any driver.

- **Typed end to end.** Tool results are frozen pydantic models, so MCP clients receive a JSON schema for every output. Tool arguments carry their bounds (`depth` 1–5, `max_rows` ≤ 1000) in the input schema. DB-API drivers are reached through a `Cursor[P]` protocol generic over the driver's paramstyle, so binding positional parameters to a named-style driver is a type error.
- **Errors as data.** Guardrail refusals come back as `{"ok": false, "error": ...}` so the agent can repair its query; programming errors and unknown tables raise.
- **Warehouse-agnostic core.** Adapters only describe where metadata lives; fetching, caching, allow-listing and catalog assembly are shared in `DbApiWarehouse`.

See [docs/adr](docs/adr) for the decisions behind the guardrails and the toolchain.

## Development

```bash
uv run ruff format          # format
uv run ruff check           # lint: every ruff rule, with a short documented ignore list
uv run ty check             # type check, strict rules
uv run pytest --cov         # tests with branch coverage
```

CI runs the same on Python 3.14 and 3.15 (Linux) and 3.14 (Windows), and builds and smoke-tests the wheel.

## Roadmap

- [x] v0.1: DuckDB adapter, catalog, six tools, three-layer guardrails, tests, CI
- [x] v0.2: Snowflake and Databricks Unity Catalog adapters, native comments and PII tags, schema scoping
- [x] v0.3: Python 3.14+, uv, ruff and ty; layered package; typed tool inputs and outputs; table names qualified by the policy
- [ ] Catalog import from dbt `manifest.json`
- [ ] Evaluation: 20 analyst questions answered with and without the server, scored for correctness and table choice
- [ ] Per-tool audit log

## License

MIT
