"""Run the evaluation: ``uv run python -m evals --provider anthropic --model <id>``.

Each run gets its own folder under ``evals/results/`` holding a freshly built demo
warehouse, the reference answers, every model response (cached, so ``--resume`` continues
a run without paying twice), per-question records, transcripts and ``results.md``.

To spend less time and money: ``--jobs`` runs questions in parallel, ``--questions`` and
``--arms`` narrow a run (and add to an existing one with ``--resume``), and ``--rescore``
recomputes verdicts from saved transcripts with no model calls.
"""

import argparse
import dataclasses
import json
import queue
import re
import sys
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from warehouse_context_mcp.demo import ensure_demo

from .agent import Record, run_question, score
from .arms import Arm, ArmName, open_arm
from .providers import ALIASES, PRESETS, HttpProvider, Provider, ProviderError, Wire, resolve
from .report import markdown
from .scoring import (
    QUESTIONS,
    Question,
    Suite,
    compute_references,
    load_references,
    load_suite,
    pii_values,
    save_references,
)
from .scripted import ScriptedProvider

RESULTS = Path(__file__).with_name("results")


def _temperature(value: str) -> float | None:
    return None if value.lower() == "none" else float(value)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals",
        description="Measure how an agent answers analyst questions with and without the server.",
    )
    model = p.add_argument_group("model")
    model.add_argument("--provider", help="preset name (see --list-providers) or 'custom'")
    model.add_argument("--model", help="model id as the provider names it")
    model.add_argument("--base-url", help="any OpenAI- or Anthropic-compatible endpoint")
    model.add_argument("--api-key-env", help="environment variable holding the key")
    model.add_argument("--wire", type=Wire, choices=list(Wire), help="API format for --base-url")
    model.add_argument(
        "--temperature",
        type=_temperature,
        default=0.0,
        help="sampling temperature, or 'none' for models that reject the parameter",
    )
    model.add_argument("--env-file", type=Path, default=Path(".env"), help="KEY=VALUE file")
    model.add_argument("--dry-run", action="store_true", help="scripted model: no key, no network")
    run = p.add_argument_group("run")
    run.add_argument(
        "--arms",
        default=",".join(a.value for a in ArmName),
        help="comma-separated arms: sql, sql+docs, mcp (default: all)",
    )
    run.add_argument("--questions", help="comma-separated ids, e.g. q01,q08 (default: all)")
    run.add_argument("--repeats", type=int, default=1, help="runs per question and arm")
    run.add_argument("--max-turns", type=int, default=20, help="model calls per question")
    run.add_argument("--out", type=Path, help="run folder (default: evals/results/<time>-<model>)")
    run.add_argument(
        "--resume",
        type=Path,
        help="continue an earlier run folder; finished questions are skipped, cached calls free",
    )
    run.add_argument(
        "--redo",
        action="store_true",
        help="with --resume: ask the selected questions again, replacing their records",
    )
    run.add_argument(
        "--jobs", type=int, default=4, help="questions answered in parallel (default: 4)"
    )
    run.add_argument(
        "--rescore",
        type=Path,
        metavar="FOLDER",
        help="recompute verdicts and results.md for a run from its transcripts; no model calls",
    )
    p.add_argument("--list-providers", action="store_true", help="show presets and exit")
    return p


def _list_providers() -> None:
    for name, preset in sorted(PRESETS.items()):
        aliases = [a for a, target in ALIASES.items() if target == name]
        alias = f" (alias: {', '.join(aliases)})" if aliases else ""
        key = preset.key_env or "no key"
        print(f"{name:<11} {preset.wire.value:<9} {preset.base_url}  [{key}]{alias}")
    print("custom      --base-url URL [--wire openai|anthropic] [--api-key-env NAME]")


def main(argv: Sequence[str] | None = None) -> int:  # noqa: C901, PLR0912, PLR0915  linear CLI flow
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list_providers:
        _list_providers()
        return 0
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.rescore:
        if not (args.rescore / "records.jsonl").exists():
            parser.error(f"{args.rescore} is not an evaluation run folder")
        return rescore(args.rescore)
    if not args.dry_run and not (args.provider and args.model):
        parser.error("--provider and --model are required (or use --dry-run)")
    try:
        arms = [ArmName(a.strip()) for a in args.arms.split(",") if a.strip()]
    except ValueError as err:
        parser.error(str(err))

    suite = load_suite(QUESTIONS)
    questions = suite.questions
    if args.questions:
        wanted = {q.strip() for q in args.questions.split(",")}
        if unknown := wanted - {q.id for q in questions}:
            parser.error(f"unknown question ids: {', '.join(sorted(unknown))}")
        questions = tuple(q for q in questions if q.id in wanted)

    label = "scripted" if args.dry_run else f"{args.provider}:{args.model}"
    run_dir: Path = args.resume or args.out or RESULTS / _slug(label)
    if args.redo and not args.resume:
        parser.error("--redo needs --resume")
    if args.resume and not (run_dir / "references.json").exists():
        parser.error(f"{run_dir} is not an evaluation run folder")
    run_dir.mkdir(parents=True, exist_ok=True)

    database, catalog = ensure_demo(run_dir)  # built once per run, reused on --resume
    references_path = run_dir / "references.json"
    if references_path.exists():
        references = load_references(references_path)
    else:
        references = compute_references(database, suite)
        save_references(references_path, references)
    pii = pii_values(database, catalog)

    provider: Provider
    if args.dry_run:
        provider = ScriptedProvider(suite.questions)
    else:
        provider_name = "custom" if args.provider == "custom" else args.provider
        try:
            provider = resolve(
                provider_name,
                args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
                wire=args.wire,
                temperature=args.temperature,
                cache_dir=run_dir / "cache",
                env_file=args.env_file,
            )
        except ProviderError as err:
            print(f"evals: {err}", file=sys.stderr)
            return 2

    records_path = run_dir / "records.jsonl"
    done = set() if args.redo else _done(records_path)
    config_path = run_dir / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps({"label": label}, indent=2), encoding="utf-8")

    tasks = [
        (arm, repeat, q)
        for arm in arms
        for repeat in range(args.repeats)
        for q in questions
        if (q.id, arm.value, repeat) not in done
    ]
    writer = _Writer(run_dir)
    try:
        for arm_name in arms:
            mine = [(r, q) for a, r, q in tasks if a is arm_name]
            _run_arm(
                arm_name,
                mine,
                provider=provider,
                references=references,
                suite=suite,
                pii=pii,
                database=database,
                catalog=catalog,
                jobs=args.jobs,
                max_turns=args.max_turns,
                writer=writer,
            )
    finally:
        writer.close()

    write_report(run_dir)
    print(f"Run folder: {run_dir} (pass it to --resume to continue)", file=sys.stderr)
    return 0


def _run_arm(  # noqa: PLR0913  one run's settings
    arm_name: ArmName,
    tasks: Sequence[tuple[int, Question]],
    *,
    provider: Provider,
    references: dict[str, Any],
    suite: Suite,
    pii: frozenset[str],
    database: Path,
    catalog: Path,
    jobs: int,
    max_turns: int,
    writer: _Writer,
) -> list[Record]:
    """Answer one arm's questions, ``jobs`` at a time, each worker with its own arm."""
    if not tasks:
        return []
    workers = min(jobs, len(tasks))
    pool: queue.Queue[Arm] = queue.Queue()
    for _ in range(workers):
        pool.put(open_arm(arm_name, database, catalog))

    def answer(task: tuple[int, Question]) -> Record:
        repeat, q = task
        # Repeats salt the cache key so they are separate samples, not cache hits.
        sampler = provider
        if isinstance(provider, HttpProvider) and repeat:
            sampler = dataclasses.replace(provider, salt=f"repeat-{repeat}")
        arm = pool.get()
        try:
            record = run_question(
                sampler,
                arm,
                q,
                references[q.id],
                pii=pii,
                decoys=suite.decoys,
                repeat=repeat,
                max_turns=max_turns,
            )
        finally:
            pool.put(arm)
        writer.write(record)
        return record

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(answer, tasks))
    finally:
        while not pool.empty():
            pool.get().close()


class _Writer:
    """Appends records and transcripts as they finish, one whole line at a time."""

    def __init__(self, run_dir: Path) -> None:
        self._lock = threading.Lock()
        self._records = (run_dir / "records.jsonl").open("a", encoding="utf-8")
        self._transcripts = (run_dir / "transcripts.jsonl").open("a", encoding="utf-8")

    def write(self, record: Record) -> None:
        transcript = {
            "question": record.question_id,
            "arm": record.arm,
            "repeat": record.repeat,
            "transcript": record.transcript,
        }
        with self._lock:
            self._records.write(json.dumps(record.summary(), default=str) + "\n")
            self._transcripts.write(json.dumps(transcript, default=str) + "\n")
            self._records.flush()
            self._transcripts.flush()
            print(_progress(record), file=sys.stderr)

    def close(self) -> None:
        self._records.close()
        self._transcripts.close()


def rescore(run_dir: Path) -> int:
    """Recompute every verdict from saved answers and transcripts, then rewrite the report."""
    suite = load_suite(QUESTIONS)
    by_id = {q.id: q for q in suite.questions}
    database, catalog = run_dir / "demo.duckdb", run_dir / "catalog.yaml"
    pii = pii_values(database, catalog)
    transcripts: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for line in (run_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            t = json.loads(line)
            transcripts[t["question"], t["arm"], t["repeat"]] = t["transcript"]

    records = _load_records(run_dir / "records.jsonl")
    for r in records:
        r.transcript = transcripts.get((r.question_id, r.arm, r.repeat), [])
        if r.question_id in by_id:
            score(r, by_id[r.question_id], pii=pii, decoys=suite.decoys)
    partial = run_dir / "records.jsonl.partial"
    partial.write_text(
        "".join(json.dumps(r.summary(), default=str) + "\n" for r in records), encoding="utf-8"
    )
    partial.replace(run_dir / "records.jsonl")

    write_report(run_dir)
    return 0


def write_report(run_dir: Path) -> None:
    """``results.md`` over every record in the run folder, however many runs added to it."""
    suite = load_suite(QUESTIONS)
    records = _load_records(run_dir / "records.jsonl")
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    repeats = max((r.repeat for r in records), default=0) + 1
    asked = [q for q in suite.questions if any(r.question_id == q.id for r in records)]
    report = markdown(records, asked, label=config.get("label", run_dir.name), repeats=repeats)
    (run_dir / "results.md").write_text(report, encoding="utf-8")
    print(report)


def _slug(label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]+', '-', label).strip('-')}"


def _done(path: Path) -> set[tuple[str, str, int]]:
    return {(r.question_id, r.arm, r.repeat) for r in _load_records(path)}


def _load_records(path: Path) -> list[Record]:
    """Records in file order; a question re-run later replaces its earlier record."""
    if not path.exists():
        return []
    latest: dict[tuple[str, str, int], Record] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = Record(**json.loads(line))
            latest[r.question_id, r.arm, r.repeat] = r
    return list(latest.values())


def _progress(r: Record) -> str:
    mark = "✓" if r.correct else "✗"
    flags = (" wrong-table" if r.wrong_table else "") + (" PII" if r.pii_exposed else "")
    flags += f" read {','.join(r.decoys_read)}" if r.decoys_read and not r.wrong_table else ""
    error = f"  error: {r.error[:120]}" if r.error else ""
    return f"{r.arm:<9} r{r.repeat} {r.question_id} {mark} {r.tool_calls:>2} calls{flags}{error}"


if __name__ == "__main__":
    sys.exit(main())
