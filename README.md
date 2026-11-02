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

**Scope**: queries may only touch tables in the configured schema; anything qualified with another database or schema is refused.

**PII**: columns flagged `pii: true` in the catalog can be counted (`COUNT(DISTINCT email)`) but never read, filtered on or grouped by, including through aliases and subqueries. `SELECT *` is allowed; PII values come back masked.

See [docs/adr/0001-read-only-by-construction.md](docs/adr/0001-read-only-by-construction.md) for the reasoning.

## Quick start

```bash
pip install -e ".[dev]"
python examples/build_demo.py examples/demo.duckdb      # fully synthetic shipping data
pytest
```

Connect it to Claude Code:

```bash
claude mcp add warehouse -- warehouse-context-mcp \
  --duckdb examples/demo.duckdb --catalog examples/catalog.yaml
```

Then ask: *"Which vessel class had the best average TCE last quarter, and is that table fresh?"*

### Snowflake or Databricks

```bash
pip install -e ".[snowflake]"            # or ".[databricks]"
cp .env.example .env                     # fill in your own values; .env is git-ignored
# run examples/snowflake_demo.sql (or databricks_demo.sql) once in the warehouse
claude mcp add warehouse -- warehouse-context-mcp --backend snowflake --catalog examples/catalog.yaml
```

Live tests (skipped unless credentials are set): `WCM_LIVE=snowflake pytest -m live`.

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

## Roadmap

- [x] v0.1: DuckDB adapter, catalog, six tools, three-layer guardrails, tests, CI
- [x] v0.2: Snowflake and Databricks Unity Catalog adapters, native comments and PII tags, schema scoping
- [ ] Catalog import from dbt `manifest.json`
- [ ] Evaluation: 20 analyst questions answered with and without the server, scored for correctness and table choice
- [ ] Per-tool audit log

## Design notes

- `context.py` holds all behaviour and has no MCP dependency; `server.py` is a thin transport. The service layer is tested directly.
- Errors from guardrails are returned to the agent as data (`{"ok": false, "error": ...}`) so it can repair its query instead of failing the call.
- The demo warehouse is synthetic and generated from a fixed seed.

## License

MIT
