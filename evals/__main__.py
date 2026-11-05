"""Run the evaluation: ``uv run python -m evals --provider anthropic --model <id>``.

Each run gets its own folder under ``evals/results/`` holding a freshly built demo
warehouse, the reference answers, every model response (cached, so ``--resume`` continues
a run without paying twice), per-question records, transcripts and ``results.md``.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from warehouse_context_mcp.demo import ensure_demo

from .agent import Record, run_question
from .arms import ArmName, open_arm
from .providers import ALIASES, PRESETS, HttpProvider, Provider, ProviderError, Wire, resolve
from .report import markdown
from .scoring import (
    QUESTIONS,
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
    run.add_argument("--resume", type=Path, help="continue an earlier run folder")
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
    done = _done(records_path)
    (run_dir / "config.json").write_text(
        json.dumps(
            {"label": label, "arms": [a.value for a in arms], "repeats": args.repeats},
            indent=2,
        ),
        encoding="utf-8",
    )

    records: list[Record] = _load_records(records_path)
    with (
        records_path.open("a", encoding="utf-8") as out,
        (run_dir / "transcripts.jsonl").open("a", encoding="utf-8") as transcripts,
    ):
        for arm_name in arms:
            arm = open_arm(arm_name, database, catalog)
            try:
                for repeat in range(args.repeats):
                    if isinstance(provider, HttpProvider):
                        provider.salt = f"repeat-{repeat}" if repeat else ""
                    for q in questions:
                        if (q.id, arm_name.value, repeat) in done:
                            continue
                        record = run_question(
                            provider,
                            arm,
                            q,
                            references[q.id],
                            pii=pii,
                            decoys=suite.decoys,
                            repeat=repeat,
                            max_turns=args.max_turns,
                        )
                        records.append(record)
                        out.write(json.dumps(record.summary(), default=str) + "\n")
                        out.flush()
                        transcripts.write(
                            json.dumps(
                                {
                                    "question": q.id,
                                    "arm": arm_name.value,
                                    "repeat": repeat,
                                    "transcript": record.transcript,
                                },
                                default=str,
                            )
                            + "\n"
                        )
                        print(_progress(record), file=sys.stderr)
            finally:
                arm.close()

    wanted_ids = {q.id for q in questions}
    selected = [
        r for r in records if r.arm in {a.value for a in arms} and r.question_id in wanted_ids
    ]
    report = markdown(selected, questions, label=label, repeats=args.repeats)
    (run_dir / "results.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"Run folder: {run_dir} (pass it to --resume to continue)", file=sys.stderr)
    return 0


def _slug(label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]+', '-', label).strip('-')}"


def _done(path: Path) -> set[tuple[str, str, int]]:
    return {(r.question_id, r.arm, r.repeat) for r in _load_records(path)}


def _load_records(path: Path) -> list[Record]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Record(**json.loads(line)) for line in lines if line.strip()]


def _progress(r: Record) -> str:
    mark = "✓" if r.correct else "✗"
    flags = (" wrong-table" if r.wrong_table else "") + (" PII" if r.pii_exposed else "")
    error = f"  error: {r.error[:120]}" if r.error else ""
    return f"{r.arm:<9} r{r.repeat} {r.question_id} {mark} {r.tool_calls:>2} calls{flags}{error}"


if __name__ == "__main__":
    sys.exit(main())
