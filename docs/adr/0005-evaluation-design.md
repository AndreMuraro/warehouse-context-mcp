# ADR 0005: Measure the server against raw SQL, with any model

**Status:** accepted · **Date:** 2026-11-05

## Context

The README claims that agents answer warehouse questions better with governed context. Until
v0.5 nothing tested that. A useful measurement has to answer three questions: better than
what, at what, and with which model.

## Decision

- **Three arms, one variable each.** `sql` is raw SQL on a read-only connection, the
  common text-to-SQL setup. `sql+docs` adds the curated catalog to the prompt, so the
  value of documentation is measured apart from the tools. `mcp` is the server. Prompt,
  model, temperature and turn budget are the same in all three.
- **Traps in the data, not in the questions.** The demo gains two decoys that real
  warehouses have: a deprecated table with older definitions and a raw feed with one
  row per amendment. Questions are plain analyst questions. The catalog says which table
  to trust; the schema alone does not. The decoys use their own random seed, so the
  original tables keep every value.
- **Ground truth from SQL, scored deterministically.** Each question carries reference SQL,
  run against the same database at the start of a run. Answers go through a
  `submit_answer` tool and are scored by rule: first number within tolerance, name
  contained, yes/no prefix, or "no PII value disclosed". There is no LLM judge.
- **Three metrics, not one.** Accuracy, wrong-table picks (the last query before the
  answer read a decoy) and PII exposures (a PII value reached the model at all). The
  last one counts tool results, not only answers: once a value is in a model's context
  it has left the warehouse.
- **Any model, over plain HTTP.** Two adapters, Anthropic Messages and OpenAI Chat
  Completions, cover the major providers and routers through presets, plus any compatible
  endpoint with `--base-url`. The client is `httpx2`, already a dependency of the MCP
  SDK, so the harness adds no vendor SDK. Assistant turns are replayed verbatim, so
  thinking blocks and `reasoning_content` survive tool loops.
- **The real server, in-process.** The `mcp` arm calls `MCPServer.call_tool`, which gives
  the agent the same schemas and result text a client receives, without spawning a
  process per question.
- **Reproducible, resumable runs.** Each run gets a folder with its own database, the
  reference answers, every model response cached by request, records and transcripts.
  `--resume` replays cached calls for free; repeats salt the cache key so they are
  separate samples.
- **A scripted model for CI.** It sends each reference query and submits the result, so
  tests cover arms, policy, scoring and reporting with no key and no network.

## Consequences

- The harness lives in `evals/` and is not shipped in the wheel; it is run from a clone.
- The scripted run already found friction in the v0.4.1 query policy: `ORDER BY` on a
  name that is both an output and a table column is refused even when the output is that
  same column (`SELECT country ... ORDER BY country`, or `ORDER BY 1`). The rule exists
  to stop an alias from shadowing a PII column in the sort, but it also catches the most
  common shape of model-written SQL. Agents can repair the query from the error, which
  costs turns; narrowing the rule is tracked on the roadmap.
- Results depend on the model and vary between runs. The README reports the model, the
  date and the range over repeats, never a single best run.
- The demo is small and the decoys were designed by the author. The question file is
  public so readers can judge the traps for themselves.
