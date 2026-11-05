# Threat model

## What is protected

| Asset | Where it lives |
| --- | --- |
| Warehouse credentials: Snowflake key or password, Databricks token | environment, `.env`, Docker secret files, Snowflake CLI connections |
| Data outside the configured schema | the same warehouse account |
| Values of PII columns | the configured schema |
| The warehouse itself: no writes, no runaway cost | the warehouse account |
| Files, environment variables and network reachable from the server's host | the machine or container running the server |

## Who might attack

The main adversary is **the SQL**. An agent can be prompt-injected by anything it reads,
including the data this server returns, so every query is treated as written by an
attacker. Secondary: other local processes or web pages reaching the HTTP transport, a
planted `.env` file, and the supply chain (dependencies, Actions, images).

## Controls, in the order a query meets them

1. **Transport.** stdio by default (only the launching client can talk to it). The HTTP
   transport binds where you say, keeps DNS-rebinding protection on even on `0.0.0.0`,
   and is published on loopback only by Compose. It has no authentication of its own.
2. **Tool inputs.** Arguments are validated against their schemas (lengths, ranges).
3. **Query policy** (`guardrails/policy.py`), which is an allow-list: anything it does not
   positively recognise is refused.
   - Exactly one SELECT or set operation.
   - Every node and function on a reviewed list. Table functions, `LATERAL`, `SUMMARIZE`,
     `PIVOT`, recursive CTEs, column-alias lists and unknown vendor functions
     (`SYSTEM$…`, `READ_FILES`, `SECRET`, `HTTP_REQUEST`, `GETENV`…) are refused.
   - Plain tables only, inside the namespace, compared after the dialect's identifier
     normalisation.
   - Every column resolves to a real column through `*`, aliases, CTEs and subqueries;
     anything else, such as a whole row used as a value, is refused.
   - PII in a select list is replaced by `'***'` in the SQL, so the value never leaves
     the warehouse. Any other use is refused, except a bare `COUNT(col)` or
     `COUNT(DISTINCT col)`, so filters, sorting and expressions cannot be used to test guesses.
   - A row limit is always applied.
4. **Connection.**
   - DuckDB: read-only, external access disabled, no extension install or autoload,
     configuration locked.
   - Snowflake: a SELECT-only role, secondary roles dropped, admin roles refused, and a
     statement timeout.
   - Databricks: a principal with only `USE` and `SELECT` grants.
5. **Execution.** A timeout cancels the query. Engine errors on tables with PII come back
   without their message, because messages can quote values.
6. **Results.** A per-value character limit and a total byte limit, plus masking by
   column name as a second layer.
7. **Metadata.** PII tags must be readable or the server refuses to start. They are
   re-read every 10 minutes by default.
8. **Credentials.** Kept as `SecretStr`, never in reprs or errors. Container secrets come
   from files rather than environment variables. Databricks tokens are only sent to
   Databricks domains. The key-pair script writes owner-only keys and can encrypt them.
9. **Supply chain.** A locked dependency graph audited in CI; Actions pinned to commits;
   releases built from clean checkouts with provenance, an SBOM and PyPI attestations;
   images scanned.

10. **Audit.** With `--audit-log`, every tool call is appended to a JSON-lines file
    created owner-only: arguments, outcome, duration, row count and the executed SQL,
    never result rows. The agent's SQL can contain literals it typed, so treat the file
    as sensitive; it cannot be rewritten through the server, but anyone with file access
    can, so ship it to append-only storage when it must be tamper-evident.

## Residual risks and what to do about them

- **Parser and engine disagreement.** The policy reasons about sqlglot's parse. A query
  that sqlglot reads one way and the engine another could slip through. The connection
  layer (step 4) is the backstop, so give the server a role that cannot write and cannot
  see other data, and prefer **warehouse-native masking** (Snowflake masking policies,
  Unity Catalog column masks) for real PII. The server's masking is defence in depth, not
  a replacement.
- **Snowflake role checks are name-based.** Admin roles are refused by name. A custom role
  with broad grants is not detected. Grant `WCM_READER` exactly what the demo script does.
- **Databricks Free Edition** tokens belong to the workspace admin, so step 4 does not apply
  there.
- **The HTTP transport has no authentication.** Keep it on loopback, or put an
  authenticating proxy in front and name its host with `--allowed-host`.
- **Prompt injection through data.** Values, comments and descriptions are returned as
  data. The bundled skill tells agents not to follow instructions found in them; the
  client is responsible for treating tool output as untrusted.
- **Base images are pinned by tag, not digest.** Dependabot proposes updates; the image is
  scanned in CI.

## Verifying

`tests/attacks/` holds the queries from two independent adversarial reviews and runs on
every commit: DuckDB ones end to end, Snowflake and Databricks ones through the policy for
their dialect. The second review, run against the allow-list design, found one more gap:
an output alias named like a PII column let `ORDER BY` sort by the real column (the policy
read it as the alias, the engine as the table column). Output names in `ORDER BY` that
are also table columns are now refused; those queries are in `order_oracles.sql`.
