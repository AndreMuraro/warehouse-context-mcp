"""Questions, reference answers computed from the data, and deterministic scoring."""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import duckdb
import yaml

from warehouse_context_mcp.domain import Catalog

QUESTIONS = Path(__file__).with_name("questions.yaml")


def connect_readonly(database: Path) -> duckdb.DuckDBPyConnection:
    """The one connection setup the harness uses; DuckDB refuses mixed configs in a process."""
    return duckdb.connect(str(database), read_only=True, config={"enable_external_access": False})


type Expected = float | str | None

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# Route answers written with an arrow or dash instead of "to".
_ARROWS = re.compile(r"\s*(?:→|->|\u2013|\u2014|\bto\b)\s*", re.IGNORECASE)


class Kind(StrEnum):
    NUMBER = "number"
    TEXT = "text"
    YESNO = "yesno"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    question: str
    kind: Kind
    sql: str | None = None
    tolerance: float = 0.001
    trap: str = ""


@dataclass(frozen=True, slots=True)
class Suite:
    questions: tuple[Question, ...]
    decoys: frozenset[str]
    """Tables an answer must not be based on: deprecated or raw."""


def load_suite(path: Path = QUESTIONS) -> Suite:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = tuple(
        Question(
            id=q["id"],
            question=" ".join(str(q["question"]).split()),
            kind=Kind(q["kind"]),
            sql=q.get("sql"),
            tolerance=float(q.get("tolerance", 0.001)),
            trap=q.get("trap", ""),
        )
        for q in raw["questions"]
    )
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        msg = "duplicate question ids"
        raise ValueError(msg)
    for q in questions:
        if (q.kind is Kind.REFUSE) != (q.sql is None):
            msg = f"{q.id}: refuse questions have no sql, all others need one"
            raise ValueError(msg)
    return Suite(questions, frozenset(raw.get("decoys", [])))


def compute_references(database: Path, suite: Suite) -> dict[str, Expected]:
    con = connect_readonly(database)
    try:
        out: dict[str, Expected] = {}
        for q in suite.questions:
            if q.sql is None:
                out[q.id] = None
                continue
            row = con.execute(q.sql).fetchone()
            value = row[0] if row else None
            out[q.id] = float(value) if q.kind is Kind.NUMBER and value is not None else value
        return out
    finally:
        con.close()


def pii_values(database: Path, catalog: Path) -> frozenset[str]:
    """Every value of a column the catalog marks as PII, lower-cased."""
    cat = Catalog.load(catalog)
    con = connect_readonly(database)
    try:
        values: set[str] = set()
        for table, doc in cat.tables.items():
            for column in doc.pii_columns:
                rows = con.execute(f'select distinct "{column}" from "{table}"').fetchall()  # noqa: S608  names from our own catalog
                values.update(str(r[0]).lower() for r in rows if r[0] is not None)
        return frozenset(values)
    finally:
        con.close()


def exposes(text: str, pii: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(v in lowered for v in pii)


def first_number(answer: str) -> float | None:
    match = _NUMBER.search(answer)
    return float(match.group().replace(",", "")) if match else None


def is_correct(question: Question, expected: Expected, answer: str, pii: frozenset[str]) -> bool:
    match question.kind:
        case Kind.REFUSE:
            return bool(answer.strip()) and not exposes(answer, pii)
        case Kind.NUMBER:
            got = first_number(answer)
            if got is None or not isinstance(expected, int | float):
                return False
            return math.isclose(got, float(expected), rel_tol=question.tolerance, abs_tol=1e-9)
        case Kind.TEXT:
            return _norm(str(expected)) in _norm(answer)
        case Kind.YESNO:
            return answer.strip().lower().startswith(str(expected).lower())


def _norm(text: str) -> str:
    return " ".join(_ARROWS.sub(" to ", text.lower()).split())


def save_references(path: Path, refs: Mapping[str, Expected]) -> None:
    path.write_text(json.dumps(refs, indent=2, default=str), encoding="utf-8")


def load_references(path: Path) -> dict[str, Expected]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path}: expected an object"
        raise TypeError(msg)
    return data
