# ADR 0002: Python 3.14+, the Astral toolchain, and a layered package

**Status:** accepted · **Date:** 2026-11-02

## Context

v0.1–0.2 grew as four flat modules with `Any`-typed driver connections and dict results.
That was quick to write but left gaps: results had no schema for MCP clients, a driver's
parameter style was not checked, and adapters mixed connection settings with SQL.

## Decision

**Python 3.14 floor, tested on 3.15.** 3.14 brings lazily evaluated annotations (PEP 649),
so the code needs neither `from __future__ import annotations` nor `TYPE_CHECKING` blocks.
3.12-era syntax is used where it helps: `type` aliases and generic classes (PEP 695),
`@override`. 3.15 is supported for everything except the Snowflake extra, which has no
3.15 wheels yet; an environment marker skips it instead of failing the install.

**uv** for environments, locking and builds (`uv_build`), with dev tools in a
`[dependency-groups]` table so they never ship as package extras. One `uv.lock` covers
both Python versions. The key-pair helper declares its own dependency inline (PEP 723).

**ruff** with every rule enabled and a short, commented ignore list. Narrow `noqa`
comments carry their reason, for example where catalog SQL interpolates an identifier
that has already been validated.

**ty** as the type checker, with "possibly" diagnostics promoted to errors and unused
suppressions reported. It runs over tests, examples and scripts as well as the package.
It caught two real issues during the migration: a single `Cursor` protocol cannot accept
both positional (Snowflake) and named (Databricks) parameters, which led to the generic
`Cursor[P]`, and the Snowflake driver's `cursor()` is typed as possibly returning a dict
cursor, which is handled with one documented cast at the connect boundary.

**Layers with inward dependencies:** domain → guardrails → warehouses → service →
transport. The service is usable and tested without MCP; adapters describe only where
metadata lives.

**Typed results.** Every tool returns a frozen pydantic model, and tool arguments declare
their bounds, so both appear in the MCP schemas that clients see.

## Consequences

- Python 3.11–3.13 are no longer supported. For a new tool aimed at agent builders this is
  acceptable; the 0.2 release remains available for older interpreters.
- ty is pre-1.0. If it blocks on a false positive, the rule can be relaxed per file in
  `pyproject.toml` with a comment, as is done for the optional driver imports.
- Strict catalog validation (`extra="forbid"`) turns silent YAML mistakes into load-time
  errors. It found one in the example catalog: an unquoted description containing commas
  had been cut short.
