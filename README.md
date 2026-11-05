# warehouse-context-mcp

<!-- mcp-name: io.github.AndreMuraro/warehouse-context-mcp -->

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

Every query an agent sends is treated as written by an attacker, since agents can be prompt-injected by what they read. Protection comes in independent layers, each tested on its own:

1. **An allow-list query policy.** SQL is parsed with [sqlglot](https://github.com/tobymao/sqlglot) and must be exactly one SELECT built only from reviewed constructs and functions. Anything else is refused without having to be anticipated: table functions, `LATERAL`, `SUMMARIZE`, recursive CTEs and every vendor function such as `SYSTEM$…`, `READ_FILES`, `SECRET`, `HTTP_REQUEST` or `GETENV`.
2. **Resolution against the real schema.** `*` is expanded and every column must resolve to a real column of a known table, through aliases, CTEs and subqueries; a whole row used as a value is refused.
3. **PII masked inside the SQL.** A PII column in any select list becomes `'***'` before the query is sent, so the value never leaves the warehouse. It can be counted (`COUNT(DISTINCT email)`), but filtering, sorting, grouping or computing on it is refused, so its values cannot be guessed one test at a time.
4. **A locked-down connection.** DuckDB is opened read-only with external file and network access disabled and its configuration locked. Snowflake uses a SELECT-only role with secondary roles dropped, and admin roles are refused. Databricks uses a principal with only `USE` and `SELECT` grants.
5. **Bounded execution and results.** Every query has a row limit, a timeout, a per-value size cap and a total byte cap. Warehouse error messages are withheld for tables with PII, because they can quote values.

**Scope:** only tables in the configured schema; names are compared after the dialect's own case rules and every table is fully qualified in the SQL that runs.

**PII tags** stored in the warehouse (Snowflake tags, Unity Catalog column tags) are merged with the curated catalog, must be readable at startup (fail closed), and are refreshed every 10 minutes. For production data, also use warehouse-native masking (masking policies or column masks): the server's masking is defence in depth.

The queries an independent review used to break an earlier version live in [`tests/attacks/`](tests/attacks) and run on every commit. See the [threat model](docs/threat-model.md), [ADR 0004](docs/adr/0004-fail-closed-query-policy.md) and [SECURITY.md](SECURITY.md).

## Quick start

Try it without cloning (needs [uv](https://docs.astral.sh/uv/)); `--demo` serves a bundled, fully synthetic shipping warehouse that is built on first run:

```bash
uvx warehouse-context-mcp --demo
```

Also on [PyPI](https://pypi.org/project/warehouse-context-mcp/), as a container image (`ghcr.io/andremuraro/warehouse-context-mcp`), and in the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.AndreMuraro/warehouse-context-mcp`. Releases carry build provenance; see [SECURITY.md](SECURITY.md).

### Use it from your MCP client

**Clone the repository and it is already configured:** `.mcp.json` (Claude Code), `.vscode/mcp.json` (VS Code) and `.codex/config.toml` (Codex) each start the demo server. Each client asks before trusting a project's servers.

To add it everywhere instead:

| Client | Command or config |
| --- | --- |
| Claude Code | `claude mcp add --scope user warehouse -- uvx warehouse-context-mcp --demo` |
| Claude Desktop | `claude_desktop_config.json`: `"warehouse": {"command": "uvx", "args": ["warehouse-context-mcp", "--demo"]}` under `mcpServers` |
| VS Code | user `mcp.json`: `"warehouse": {"type": "stdio", "command": "uvx", "args": ["warehouse-context-mcp", "--demo"]}` under `servers` |
| Codex | `codex mcp add warehouse -- uvx warehouse-context-mcp --demo` |
| Any client, over HTTP | `docker compose up`, then point it at `http://127.0.0.1:8000/mcp` |

#### Claude Code plugin and agent skill

The repository is also a Claude Code plugin marketplace. The plugin bundles the server and a `warehouse-context` skill that teaches the agent the workflow (find, describe, check freshness, query, cite the executed SQL) and how to respond to refusals:

```bash
/plugin install warehouse-context --marketplace AndreMuraro/warehouse-context-mcp
```

The same skill is checked in for project use: `.claude/skills/` (Claude Code), `.agents/skills/` (Codex) and both are read by VS Code. Or copy `plugins/warehouse-context/skills/warehouse-context/` into your personal skills folder.

Replace `--demo` with `--backend snowflake` or `--backend databricks` (and the settings below) for a real warehouse.

Then ask: *"Which vessel class had the best average TCE last quarter, and is that table fresh?"*

### Development

Requires uv; it installs Python 3.14 if missing.

```bash
uv sync --all-extras                            # creates .venv from uv.lock
uv run pytest
```

### Snowflake or Databricks

```bash
uv sync --extra snowflake                # or --extra databricks, or --all-extras
cp .env.example .env                     # fill in your own values; .env is git-ignored
# run examples/snowflake_demo.sql (or databricks_demo.sql) once in the warehouse
uv run warehouse-context-mcp --backend snowflake --catalog path/to/catalog.yaml
```

Settings come from `SNOWFLAKE_*` / `DATABRICKS_*` environment variables or `./.env`; real environment variables win, and blank values count as unset. Key-pair auth for Snowflake: `uv run scripts/snowflake_keypair.py` prints the one `ALTER USER` statement to run.

#### Sharing a Snowflake CLI connection

If you use [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index), point the server at one of its connections instead of repeating the account and sign-in details:

```toml
# connections.toml (~/.snowflake/, or %USERPROFILE%\AppData\Local\snowflake\ on Windows)
[wcm]
account = "orgname-accountname"
user = "you"
authenticator = "SNOWFLAKE_JWT"
private_key_file = "~/.snowflake/snowflake_key.p8"
```

```bash
snow sql -c wcm -f examples/snowflake_demo.sql      # one-time demo setup, as an admin role
SNOWFLAKE_CONNECTION_NAME=wcm SNOWFLAKE_ROLE=WCM_READER uv run warehouse-context-mcp --backend snowflake
```

The connection supplies the account and sign-in; any `SNOWFLAKE_*` value you set overrides it, and this package's defaults never do. After connecting, the server adopts the session's actual database and schema, and refuses admin roles (`ACCOUNTADMIN`, `SYSADMIN`, …), so a CLI connection kept for admin work cannot bypass the SELECT-only role.

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

## HTTP transport and containers

By default the server speaks MCP over stdio: the client launches it. With `--transport http` it runs as a long-lived Streamable HTTP service instead, with stateless JSON responses and a `/healthz` endpoint:

```bash
uv run warehouse-context-mcp --demo --transport http --port 8000
claude mcp add --transport http warehouse http://127.0.0.1:8000/mcp
```

The HTTP transport has **no authentication**. DNS-rebinding protection stays on even when bound to every interface: only requests whose `Host` header names localhost, or a name passed with `--allowed-host`, are served. Publish it on loopback, or put an authenticating proxy in front.

### Docker

```bash
docker compose up --build                        # demo warehouse at http://127.0.0.1:8000/mcp
docker run --rm -i warehouse-context-mcp:local   # or stdio, for clients that launch a command
```

The image is built from `uv.lock`, runs as a non-root user, and contains every backend plus the synthetic demo warehouse. Compose runs it with a read-only filesystem, no Linux capabilities and `no-new-privileges`, and publishes ports on `127.0.0.1` only.

For a real warehouse, fill in `.env` and start a profile. Credentials reach the container as secret files under `/run/secrets`, not as environment variables:

```bash
docker compose --profile snowflake up --build    # :8001, key-pair sign-in (no browser in a container)
docker compose --profile databricks up --build   # :8002, token from DATABRICKS_TOKEN
```

- **Snowflake** uses `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` and the key file at `SNOWFLAKE_PRIVATE_KEY_PATH`; Snowflake CLI connection names are a host-only convenience.
- **Linux hosts:** if your key file is readable only by you, run as your own user: `WCM_UID=$(id -u) WCM_GID=$(id -g) docker compose ...`.
- Version tags publish the image to `ghcr.io/andremuraro/warehouse-context-mcp`.

CI builds the image, checks the stdio transport, brings the Compose service up until healthy, calls a tool over HTTP, confirms a foreign `Host` header is refused, and verifies the container is non-root and read-only.

## The catalog

Meaning lives in a YAML file reviewed in Git like code ([the demo's catalog](src/warehouse_context_mcp/demo/catalog.yaml)):

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

### From dbt

`--dbt-manifest target/manifest.json` reads the docs a dbt project already has: model, seed, snapshot and source descriptions, column descriptions, `owner` and `grain` from `meta`, lineage from `depends_on`, source freshness from `loaded_at_field` and `warn_after`, and PII from a column's `meta` (`pii: true` or `contains_pii: true`) or a `pii` tag. Tables are matched by relation name (`alias` or `identifier`).

The layers stack: warehouse comments and tags, then dbt, then the curated YAML on top, which wins where it sets a value. A PII flag from any layer sticks.

```bash
uvx warehouse-context-mcp --backend snowflake --dbt-manifest target/manifest.json --catalog overrides.yaml
```

## Audit log

`--audit-log audit.jsonl` appends one JSON line per tool call: time, tool, arguments, outcome (`ok`, `refused` or `error`), duration, row count and, for queries, the SQL actually sent after the policy rewrote it. Result rows are never written, so the log holds no warehouse data beyond what the agent put in its own SQL. The file is created owner-only, opened append-only and never truncated; rotation and shipping are left to the platform.

```json
{"ts": "2026-11-05T12:00:00.000+00:00", "tool": "run_query", "arguments": {"sql": "select contact_email from dim_charterers", "max_rows": 100}, "outcome": "ok", "rows": 12, "truncated": false, "sql": "SELECT '***' AS \"contact_email\" FROM \"main\".\"dim_charterers\" AS \"dim_charterers\" LIMIT 100", "duration_ms": 3.1}
```

## Evaluation

The claim behind this server is that agents answer warehouse questions better with governed context than with raw SQL access. [`evals/`](evals) measures it.

**Setup.** The demo warehouse plus two decoys an analyst learns to avoid: `legacy_voyage_pnl`, a deprecated table frozen months ago with older definitions, and `raw_fixture_messages`, the raw feed where a voyage appears once per amendment. [20 analyst questions](evals/questions.yaml) cover counts, sums, rankings, definitions, freshness and a request for a contact's email. Each reference answer is computed from the data at run time.

The same model, prompt and 20-turn budget run under three arms:

| Arm | The agent gets |
|---|---|
| `sql` | One raw SQL tool on a read-only connection: the usual text-to-SQL setup |
| `sql+docs` | The same tool, with the curated catalog pasted into the system prompt |
| `mcp` | This server's six tools, called through the MCP server in-process |

`sql+docs` separates what documentation alone buys from what the tools and guardrails add.

**Scoring** is deterministic. A number must be within the question's tolerance (0.1% by default) of the reference, a name must appear in the answer, and the email question is correct when no PII value is disclosed. A **wrong-table pick** is an answer whose last query read a decoy. A **PII exposure** is any PII value reaching the model, in a tool result or the answer.

**Run it.** Any provider with an Anthropic- or OpenAI-compatible API works. The key is read from the environment or a `.env` file and never printed.

```bash
uv run python -m evals --list-providers
uv run python -m evals --provider anthropic --model <model-id> --repeats 3
uv run python -m evals --provider openrouter --model <vendor/model> --repeats 3
uv run python -m evals --provider kimi --model <model-id>
uv run python -m evals --provider ollama --model <local-model>          # no key, local
uv run python -m evals --provider custom --base-url http://localhost:4000/v1 --model <id>  # LiteLLM, vLLM
uv run python -m evals --dry-run                                         # scripted, no key
```

Each run writes a folder under `evals/results/` with its own freshly built database, the reference answers, cached model responses, per-question records, full transcripts and `results.md`. `--resume <folder>` continues an interrupted run without paying for finished calls. `--questions q01,q08` and `--arms sql,mcp` narrow a run, and `--temperature none` suits models that reject the parameter.

### Results

_First model runs go here before the v0.5.0 tag._

### Limits

- The warehouse is small, synthetic and has one schema, and the decoys were designed by the same person who wrote the server. The question file is public so the traps can be judged.
- `sql+docs` pastes the whole catalog into the prompt. That works for six tables; for a real warehouse it would not fit, which is part of the argument for tools.
- The `mcp` arm calls the server in-process. The tool schemas and result text are the ones a client receives, but stdio and HTTP transport are not exercised here (CI covers them).
- Models vary between runs even at temperature 0. Use `--repeats 3` and read the range.

## Architecture

```
src/warehouse_context_mcp/
├── demo/          the bundled synthetic warehouse and its catalog
├── domain/        catalog, dbt manifest import, result models, namespace, errors
├── guardrails/    QueryPolicy: parse, scope, PII rules, row limit; result masking
├── warehouses/    Warehouse protocol + DuckDB, Snowflake, Databricks adapters
├── service.py     WarehouseContext: combines the three; no MCP dependency
├── server.py      MCP transport: six read-only tools, stdio or Streamable HTTP
├── audit.py       append-only JSON-lines log of tool calls
├── settings.py    pydantic-settings for connection config and secrets
└── cli.py         entry point
evals/             the evaluation harness (not shipped in the package)
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
- [x] v0.4: Streamable HTTP transport, hardened container image and Compose setup, Snowflake CLI connections, zero-setup `--demo`, client configs, PyPI and MCP Registry release pipeline
- [x] v0.5: evaluation (20 analyst questions, three arms, any Anthropic- or OpenAI-compatible model), dbt `manifest.json` import, append-only audit log
- [ ] Fewer false refusals: `ORDER BY` on an output that is the same column it shadows (found by the evaluation)

## License

MIT
