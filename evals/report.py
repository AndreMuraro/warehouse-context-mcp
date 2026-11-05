"""Aggregate records into the results table printed and saved after a run."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from .agent import Record
from .arms import ArmName
from .scoring import Question


@dataclass(frozen=True, slots=True)
class ArmSummary:
    arm: str
    runs: int
    accuracy: float
    accuracy_min: float
    accuracy_max: float
    wrong_tables: int
    pii_exposures: int
    errors: int
    mean_tool_calls: float
    tokens: int


def summarise(records: Sequence[Record]) -> list[ArmSummary]:
    by_arm: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        by_arm[r.arm].append(r)
    order = [a.value for a in ArmName]
    out: list[ArmSummary] = []
    for arm in sorted(by_arm, key=order.index):
        rows = by_arm[arm]
        per_repeat: dict[int, list[Record]] = defaultdict(list)
        for r in rows:
            per_repeat[r.repeat].append(r)
        accuracies = [sum(r.correct for r in rs) / len(rs) for rs in per_repeat.values()]
        out.append(
            ArmSummary(
                arm=arm,
                runs=len(rows),
                accuracy=mean(accuracies),
                accuracy_min=min(accuracies),
                accuracy_max=max(accuracies),
                wrong_tables=sum(r.wrong_table for r in rows),
                pii_exposures=sum(r.pii_exposed for r in rows),
                errors=sum(bool(r.error) for r in rows),
                mean_tool_calls=mean(r.tool_calls for r in rows),
                tokens=sum(r.input_tokens + r.output_tokens for r in rows),
            )
        )
    return out


def markdown(
    records: Sequence[Record], questions: Sequence[Question], *, label: str, repeats: int
) -> str:
    summaries = summarise(records)
    n = len({r.question_id for r in records})
    lines = [
        f"## Results: {label}",
        "",
        (
            f"{n} questions, {repeats} run(s) per arm. Wrong-table picks and PII exposures "
            "are counted over all runs."
        ),
        "",
        (
            "| Arm | Accuracy | Wrong-table picks | PII exposures | Errors "
            "| Tool calls / question | Tokens |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        spread = (
            f" ({s.accuracy_min:.0%} to {s.accuracy_max:.0%})"
            if s.accuracy_min != s.accuracy_max
            else ""
        )
        lines.append(
            f"| `{s.arm}` | {s.accuracy:.0%}{spread} | {s.wrong_tables} | {s.pii_exposures} "
            f"| {s.errors} | {s.mean_tool_calls:.1f} | {s.tokens:,} |"
        )

    arms = [s.arm for s in summaries]
    grid: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for r in records:
        grid[r.question_id, r.arm].append(r)
    lines += [
        "",
        "<details><summary>Per question</summary>",
        "",
        "| Question | " + " | ".join(f"`{a}`" for a in arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    for q in questions:
        if not any((q.id, a) in grid for a in arms):
            continue
        cells = [_cell(grid[q.id, a]) for a in arms]
        lines.append(f"| {q.id}: {q.question} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "✓ correct, ✗ wrong, ⚠ wrong table, 🔓 PII exposed; several runs show as a count.",
        "",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def _cell(rows: Sequence[Record]) -> str:
    if not rows:
        return ""
    if len(rows) == 1:
        r = rows[0]
        marks = ("✓" if r.correct else "✗") + ("⚠" if r.wrong_table else "")
        return marks + ("🔓" if r.pii_exposed else "")
    correct = sum(r.correct for r in rows)
    flags = ("⚠" if any(r.wrong_table for r in rows) else "") + (
        "🔓" if any(r.pii_exposed for r in rows) else ""
    )
    return f"{correct}/{len(rows)}{flags}"
