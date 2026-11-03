# ADR 0001: Read-only by construction, in three layers

**Status:** accepted; the query-policy design is superseded by [ADR 0004](0004-fail-closed-query-policy.md) · **Date:** 2026-11-02

## Context

The server lets an AI agent run SQL it wrote itself against a warehouse. Agents make mistakes
and can be prompt-injected through the data they read. A single safety check is a single
point of failure.

## Decision

Enforce read-only and bounded access in three layers that do not depend on each other:

1. **Parse-tree validation** (sqlglot): exactly one statement; root is SELECT or a set
   operation of SELECTs; no write, DDL, `INTO` or unmodelled `Command` node anywhere in the
   tree. Unparseable SQL is rejected, never passed through.
2. **Read-only connection** at the driver level (DuckDB `read_only=True`; a read-only role
   for Snowflake and Databricks).
3. **Bounded execution**: a row limit is always applied or tightened, and long queries are
   interrupted.

PII is protected by refusing explicit references to catalog-flagged columns (except inside
`COUNT`), not by masking output alone, because aliasing defeats name-based masking. Masking
remains as a second layer for `SELECT *`.

## Consequences

- Some legitimate queries are refused (for example grouping by a PII column). The error
  message tells the agent why, so it can rephrase.
- String-based checks such as "starts with SELECT" are explicitly avoided: they miss
  `SELECT … INTO`, stacked statements and dialect commands.
- Each layer has tests that fail if that layer is removed.
