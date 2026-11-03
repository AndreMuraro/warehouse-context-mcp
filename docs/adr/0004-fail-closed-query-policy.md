# ADR 0004: A fail-closed query policy, with PII masked in the SQL

**Status:** accepted · **Date:** 2026-11-02 · **Supersedes:** the policy design in ADR 0001

## Context

ADR 0001's policy refused known-bad constructs: writes, DDL, `SELECT … INTO`, unmodelled
commands, and references to PII columns by name. An independent review broke it on the
demo warehouse:

- `LATERAL read_text('/proc/self/environ')` and similar table functions read files and
  the server's environment, because sqlglot does not model them as tables.
- PII came out without being named: a table alias used as a whole-row value, a column
  alias list, a `UNION` with `*`, `SUMMARIZE`.
- The exception for `COUNT(...)` allowed any expression, so
  `COUNT(CASE WHEN email LIKE 'a%' …)` revealed values one guess at a time.
- Vendor functions (`SECRET`, `HTTP_REQUEST`, `RESULT_SCAN`, `SYSTEM$…`) passed untouched.
- Masking results by column name missed every renamed column.

A denylist cannot keep up with three SQL dialects and their extensions.

## Decision

- **Allow-list.** Every node type and function must be on a reviewed list; everything
  else is refused, including all functions sqlglot cannot model. The list covers what
  analysis needs: joins, CTEs, subqueries, windows, aggregates, and date, text and math
  functions.
- **Resolve, then decide.** The query is qualified against the real schema (sqlglot's
  optimizer). `*` is expanded and every column must resolve to a real column; anything
  left unresolved, such as a row-as-value, is refused. The allow-list runs again on the
  qualified tree.
- **Mask in the SQL.** A PII column in a select list becomes `'***'` before the query is
  sent, at every level of the query, so the value never leaves the warehouse. Any other
  use is refused, except a bare `COUNT(col)` or `COUNT(DISTINCT col)`.
- **Normalised identifiers.** Namespaces are compared after the dialect's normalisation,
  so a quoted `"shipping"` is not taken for `SHIPPING` on Snowflake.
- **Strengthen the connection layer as well:** a locked-down DuckDB connection, Snowflake
  secondary roles dropped, PII tags that must be readable, engine error messages withheld
  for PII tables, and bounded result sizes.

## Consequences

- Some valid SQL is refused until its construct or function is reviewed and added. That
  is the intended trade: a refusal tells the agent what to change, a leak cannot be undone.
- `SELECT contact_email` now returns masked values instead of an error, which is more
  useful to agents and harder to misuse.
- The policy needs column metadata for referenced tables; adapters cache it and refresh
  it with the PII tags.
- The review's attack queries are kept in `tests/attacks/` as a regression suite.
