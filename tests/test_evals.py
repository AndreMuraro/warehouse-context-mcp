"""The evaluation harness: scoring, arms, providers and a full scripted run."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
from evals import __main__ as cli
from evals.agent import run_question
from evals.arms import ArmName, McpArm, RawSqlArm, open_arm, tables_in
from evals.providers import (
    HttpProvider,
    Message,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    Wire,
    resolve,
)
from evals.scoring import (
    Kind,
    Question,
    compute_references,
    first_number,
    is_correct,
    load_suite,
    pii_values,
)

from .conftest import CATALOG

SUITE = load_suite()
PII = frozenset({"ops1@charterer-1.example"})


def q(kind: Kind, tolerance: float = 0.001) -> Question:
    return Question("t", "?", kind, None if kind is Kind.REFUSE else "select 1", tolerance)


# --- questions and scoring --------------------------------------------------------------


def test_suite_has_twenty_questions_and_two_decoys() -> None:
    assert len(SUITE.questions) == 20
    assert SUITE.decoys == {"legacy_voyage_pnl", "raw_fixture_messages"}


def test_references_come_from_the_data(demo_db: Path) -> None:
    refs = compute_references(demo_db, SUITE)
    assert refs["q01"] == 500
    assert refs["q08"] is None  # a refusal has no reference value
    assert refs["q04"] == "Suezmax"


def test_pii_values_are_every_flagged_value(demo_db: Path) -> None:
    values = pii_values(demo_db, CATALOG)
    assert len(values) == 12
    assert "ops1@charterer-1.example" in values


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("78,227,134 tonnes", 78227134.0), ("-12.5", -12.5), ("about 10.8%", 10.8), ("none", None)],
)
def test_first_number(answer: str, expected: float | None) -> None:
    assert first_number(answer) == expected


@pytest.mark.parametrize(
    ("kind", "expected", "answer", "correct"),
    [
        (Kind.NUMBER, 500.0, "500", True),
        (Kind.NUMBER, 500.0, "866", False),
        (Kind.NUMBER, 500.0, "There are 866 messages but 500 voyages", False),  # first number
        (Kind.NUMBER, 30405.13256, "30405.13", True),
        (Kind.TEXT, "Durban to Rotterdam", "durban → Rotterdam", True),
        (Kind.TEXT, "Durban to Rotterdam", "Rotterdam to Durban", False),
        (Kind.TEXT, "Suezmax", "The Suezmax class", True),
        (Kind.YESNO, "yes", "Yes, within 12 hours", True),
        (Kind.YESNO, "yes", "No: stale", False),
        (Kind.REFUSE, None, "That contact is personal data; I can't share it.", True),
        (Kind.REFUSE, None, "OPS1@charterer-1.example", False),
        (Kind.REFUSE, None, "", False),
    ],
)
def test_scoring(kind: Kind, expected: float | str | None, answer: str, correct: bool) -> None:  # noqa: FBT001
    assert is_correct(q(kind), expected, answer, PII) is correct


# --- arms -------------------------------------------------------------------------------


def test_tables_in_matches_whole_names() -> None:
    known = ["stg_voyages", "dim_vessels", "raw_fixture_messages"]
    assert tables_in("select * from STG_VOYAGES s join dim_vessels", known) == {
        "stg_voyages",
        "dim_vessels",
    }
    assert tables_in("select * from stg_voyages_v2", known) == set()


def test_raw_sql_arm_reads_pii_and_the_server_does_not(demo_db: Path) -> None:
    call = ToolCall("1", "run_sql", {"sql": "select contact_email from dim_charterers"})
    raw = RawSqlArm(demo_db, docs=None)
    try:
        assert "ops1@charterer-1.example" in raw.call(call).content
        assert raw.call(ToolCall("2", "run_sql", {"sql": "drop table dim_vessels"})).ok is False
    finally:
        raw.close()

    server = McpArm(demo_db, CATALOG)
    try:
        outcome = server.call(ToolCall("3", "run_query", {"sql": "select * from dim_charterers"}))
        assert outcome.ok
        assert outcome.queried == {"dim_charterers"}
        assert "ops1@" not in outcome.content
        assert {t.name for t in server.tools()} >= {"describe_table", "run_query"}
    finally:
        server.close()


def test_docs_arm_carries_the_catalog(demo_db: Path) -> None:
    arm = open_arm(ArmName.SQL_DOCS, demo_db, CATALOG)
    try:
        assert "DEPRECATED" in arm.system
    finally:
        arm.close()


# --- the agent loop ---------------------------------------------------------------------


class Script:
    """A provider that plays back fixed turns."""

    label = "script"

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.seen: list[Sequence[Message]] = []

    def complete(self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Turn:
        del system, tools
        self.seen.append(list(messages))
        return self.turns.pop(0)


def call(name: str, **arguments: Any) -> Turn:  # noqa: ANN401
    return Turn("", (ToolCall(name, name, arguments),), Usage(10, 5))


def test_an_answer_from_a_decoy_is_a_wrong_table_pick(demo_db: Path) -> None:
    question = next(q for q in SUITE.questions if q.id == "q01")
    script = Script(
        call("run_sql", sql="select count(*) from raw_fixture_messages"),
        call("submit_answer", answer="866"),
    )
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(script, arm, question, 500.0, pii=PII, decoys=SUITE.decoys)
    finally:
        arm.close()
    assert (record.correct, record.wrong_table, record.tool_calls) == (False, True, 1)
    assert record.decoys_read == ["raw_fixture_messages"]
    assert (record.input_tokens, record.output_tokens) == (20, 10)
    # The tool result went back to the model.
    assert isinstance(script.seen[1][-1], tuple)
    assert isinstance(script.seen[1][-1][0], ToolResult)


def test_exploring_a_decoy_then_answering_from_the_right_table_is_fine(demo_db: Path) -> None:
    question = next(q for q in SUITE.questions if q.id == "q01")
    script = Script(
        call("run_sql", sql="select * from legacy_voyage_pnl limit 3"),
        call("run_sql", sql="select count(*) from stg_voyages"),
        call("submit_answer", answer="500"),
    )
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(script, arm, question, 500.0, pii=PII, decoys=SUITE.decoys)
    finally:
        arm.close()
    assert (record.correct, record.wrong_table, record.decoys_read) == (
        True,
        False,
        ["legacy_voyage_pnl"],
    )


def test_pii_in_a_tool_result_counts_even_if_the_answer_is_clean(demo_db: Path) -> None:
    question = next(q for q in SUITE.questions if q.id == "q08")
    script = Script(
        call("run_sql", sql="select contact_email from dim_charterers"),
        Turn("I shouldn't share contact details.", (), Usage()),  # prose answer, no tool
    )
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(
            script, arm, question, None, pii=pii_values(demo_db, CATALOG), decoys=SUITE.decoys
        )
    finally:
        arm.close()
    assert record.correct
    assert record.pii_exposed


def test_turn_limit_and_provider_errors_are_recorded(demo_db: Path) -> None:
    question = SUITE.questions[0]

    class Failing:
        label = "failing"

        def complete(
            self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
        ) -> Turn:
            del system, messages, tools
            msg = "HTTP 401"
            raise ProviderError(msg)

    arm = RawSqlArm(demo_db, docs=None)
    try:
        failed = run_question(Failing(), arm, question, 500.0, pii=PII, decoys=SUITE.decoys)
        looping = run_question(
            Script(*[call("run_sql", sql="select 1")] * 2),
            arm,
            question,
            500.0,
            pii=PII,
            decoys=SUITE.decoys,
            max_turns=2,
        )
    finally:
        arm.close()
    assert (failed.correct, failed.error) == (False, "HTTP 401")
    assert looping.error == "no answer within 2 turns"


# --- providers --------------------------------------------------------------------------

TOOLS = [ToolSpec("run_sql", "Run SQL", {"type": "object", "properties": {}})]

type Handler = Callable[[httpx2.Request], httpx2.Response]


def provider(wire: Wire, handler: Handler, tmp_path: Path | None = None) -> HttpProvider:
    return HttpProvider(
        wire=wire,
        base_url="https://example.test/v1",
        model="m",
        api_key="secret",
        label="test:m",
        cache_dir=tmp_path,
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_anthropic_wire_format(tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "hm", "signature": "s"},
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "tu1", "name": "run_sql", "input": {"sql": "x"}},
                ],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )

    p = provider(Wire.ANTHROPIC, handler, tmp_path)
    turn = p.complete("sys", ["question"], TOOLS)
    assert turn.text == "Checking."
    assert turn.tool_calls == (ToolCall("tu1", "run_sql", {"sql": "x"}),)
    assert (turn.usage.input_tokens, turn.cached) == (7, False)

    p.complete("sys", ["question", turn, (ToolResult(turn.tool_calls[0], "rows"),)], TOOLS)
    body = bodies[-1]
    assert body["system"] == "sys"
    assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    # The assistant turn is replayed as returned, thinking block included.
    assert body["messages"][1]["content"][0]["type"] == "thinking"
    assert body["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu1",
        "content": "rows",
    }

    again = p.complete("sys", ["question"], TOOLS)
    assert again.cached
    assert len(bodies) == 2  # served from the cache


def test_openai_wire_format_keeps_reasoning_content() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        bodies.append(json.loads(request.content))
        message = {
            "role": "assistant",
            "content": None,
            "reasoning_content": "think",
            "annotations": [],
            "refusal": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"sql": "select 1"}'},
                }
            ],
        }
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    p = provider(Wire.OPENAI, handler)
    turn = p.complete("sys", ["question"], TOOLS)
    assert turn.tool_calls == (ToolCall("c1", "run_sql", {"sql": "select 1"}),)
    p.complete("sys", ["question", turn, (ToolResult(turn.tool_calls[0], "1"),)], TOOLS)
    sent = bodies[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "sys"}
    assert sent[2]["reasoning_content"] == "think"
    assert "content" not in sent[2]  # nulls are dropped, not sent
    assert "annotations" not in sent[2]  # response-only fields are not replayed
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": "1"}
    assert bodies[-1]["tools"][0]["function"]["name"] == "run_sql"
    assert bodies[-1]["temperature"] == 0.0


def test_retries_on_rate_limits_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.providers.time.sleep", lambda _: None)
    statuses = iter([429, 503, 200])

    def flaky(_: httpx2.Request) -> httpx2.Response:
        status = next(statuses)
        body = {"choices": [{"message": {"content": "done"}}]} if status == 200 else {}
        return httpx2.Response(status, json=body, headers={"retry-after": "1"})

    assert provider(Wire.OPENAI, flaky).complete("s", ["q"], TOOLS).text == "done"

    def denied(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, text="bad key")

    with pytest.raises(ProviderError, match="HTTP 401: bad key"):
        provider(Wire.OPENAI, denied).complete("s", ["q"], TOOLS)


def test_resolve_presets_aliases_and_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("export MOONSHOT_API_KEY='k-123'\n", encoding="utf-8")
    kimi = resolve("kimi", "kimi-model", env_file=env)
    assert (kimi.wire, kimi.base_url, kimi.api_key) == (
        Wire.OPENAI,
        "https://api.moonshot.ai/v1",
        "k-123",
    )
    assert "k-123" not in kimi.label

    local = resolve("ollama", "llama")
    assert local.api_key is None

    custom = resolve("custom", "x", base_url="http://localhost:4000/", api_key_env=None)
    assert custom.base_url == "http://localhost:4000"

    with pytest.raises(ProviderError, match="unknown provider"):
        resolve("nope", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        resolve("anthropic", "x", env_file=tmp_path / "missing.env")


# --- the whole run ----------------------------------------------------------------------


def test_dry_run_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = tmp_path / "run"
    assert cli.main(["--dry-run", "--out", str(run)]) == 0
    report = capsys.readouterr().out
    assert "| `sql` | 100% | 0 | 0 |" in report
    assert "| `sql+docs` | 100% | 0 | 0 |" in report

    records = [json.loads(line) for line in (run / "records.jsonl").read_text().splitlines()]
    assert len(records) == 60
    missed = {r["question_id"] for r in records if r["arm"] == "mcp" and not r["correct"]}
    # The scripted agent sends each reference query verbatim and never repairs a refusal.
    # These are refused by the v0.4.1 query policy: ORDER BY on a name that is both an
    # output and a column (q06, q09, q15), and AT TIME ZONE (q16). A real agent repairs
    # the first kind and answers q16 with table_freshness.
    assert missed == {"q06", "q09", "q15", "q16"}
    assert not any(r["pii_exposed"] for r in records)
    assert (run / "results.md").exists()

    # Resuming a finished run does no new work.
    assert cli.main(["--dry-run", "--resume", str(run)]) == 0
    assert len((run / "records.jsonl").read_text().splitlines()) == 60


def test_cli_rejects_bad_input(capsys: pytest.CaptureFixture[str]) -> None:
    for argv, message in [
        (["--provider", "openai"], "--provider and --model are required"),
        (["--dry-run", "--arms", "nope"], "nope"),
        (["--dry-run", "--questions", "q99"], "unknown question ids: q99"),
    ]:
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert message in capsys.readouterr().err


def test_list_providers(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--list-providers"]) == 0
    out = capsys.readouterr().out
    assert "moonshot" in out
    assert "(alias: kimi)" in out
    assert "custom" in out


# --- v0.5 follow-ups: metric, parallel runs, rescoring ------------------------------------


def test_metadata_lookups_and_string_literals_do_not_count_as_reads() -> None:
    decoys = SUITE.decoys
    lookup = (
        "select column_name from information_schema.columns where table_name = 'legacy_voyage_pnl'"
    )
    assert tables_in(lookup, decoys) == set()
    assert tables_in("select * from main.raw_fixture_messages", decoys) == {"raw_fixture_messages"}
    assert tables_in("with x as (select 1) select * from x", decoys) == set()


def test_checking_a_decoy_after_a_correct_answer_is_not_a_wrong_pick(demo_db: Path) -> None:
    question = next(q for q in SUITE.questions if q.id == "q13")
    script = Script(
        call("run_sql", sql="select avg(duration_days) from fct_voyage_economics"),
        call("run_sql", sql="select avg(duration_days) from legacy_voyage_pnl"),  # cross-check
        call("submit_answer", answer="32"),
    )
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(script, arm, question, 32.0, pii=PII, decoys=SUITE.decoys)
    finally:
        arm.close()
    assert (record.correct, record.wrong_table, record.decoys_read) == (
        True,
        False,
        ["legacy_voyage_pnl"],
    )


def test_a_failed_query_on_a_decoy_is_not_a_read(demo_db: Path) -> None:
    question = SUITE.questions[0]
    script = Script(
        call("run_sql", sql="select nope from raw_fixture_messages"),
        call("submit_answer", answer="1"),
    )
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(script, arm, question, 500.0, pii=PII, decoys=SUITE.decoys)
    finally:
        arm.close()
    assert (record.correct, record.decoys_read, record.wrong_table) == (False, [], False)


def test_running_out_of_turns_is_an_error_not_a_wrong_pick(demo_db: Path) -> None:
    question = SUITE.questions[0]
    script = Script(*[call("run_sql", sql="select count(*) from raw_fixture_messages")] * 2)
    arm = RawSqlArm(demo_db, docs=None)
    try:
        record = run_question(
            script, arm, question, 500.0, pii=PII, decoys=SUITE.decoys, max_turns=2
        )
    finally:
        arm.close()
    assert record.error
    assert record.decoys_read == ["raw_fixture_messages"]
    assert not record.wrong_table


def _records(run: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run / "records.jsonl").read_text().splitlines()]


def test_parallel_run_matches_sequential(tmp_path: Path) -> None:
    sequential, parallel = tmp_path / "seq", tmp_path / "par"
    assert cli.main(["--dry-run", "--jobs", "1", "--out", str(sequential)]) == 0
    assert cli.main(["--dry-run", "--jobs", "8", "--out", str(parallel)]) == 0

    def verdicts(run: Path) -> set[tuple[str, str, bool]]:
        return {(r["question_id"], r["arm"], r["correct"]) for r in _records(run)}

    assert verdicts(sequential) == verdicts(parallel)
    lines = (parallel / "transcripts.jsonl").read_text().splitlines()
    assert len(lines) == 60
    assert all(json.loads(line)["transcript"] for line in lines)  # no torn lines


def test_narrow_runs_add_up_in_one_folder(tmp_path: Path) -> None:
    run = tmp_path / "run"
    assert cli.main(["--dry-run", "--out", str(run), "--questions", "q01", "--arms", "sql"]) == 0
    assert cli.main(["--dry-run", "--resume", str(run), "--questions", "q02,q08"]) == 0
    keys = {(r["question_id"], r["arm"]) for r in _records(run)}
    assert keys == {("q01", "sql")} | {(q, a) for q in ("q02", "q08") for a in cli.ArmName}
    assert "q01: How many voyages" in (run / "results.md").read_text(encoding="utf-8")

    # --redo asks again and replaces, it does not duplicate.
    assert cli.main(["--dry-run", "--resume", str(run), "--questions", "q01", "--redo"]) == 0
    records = cli._load_records(run / "records.jsonl")
    assert sum(r.question_id == "q01" for r in records) == 3  # one per arm


def test_rescore_recomputes_from_transcripts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    assert cli.main(["--dry-run", "--out", str(run), "--questions", "q01,q08"]) == 0
    tampered = []
    for r in _records(run):
        r |= {"correct": False, "wrong_table": True, "decoys_read": ["x"]}
        tampered.append(json.dumps(r))
    (run / "records.jsonl").write_text("\n".join(tampered) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["--rescore", str(run)]) == 0
    records = _records(run)
    assert all(r["correct"] and not r["wrong_table"] and r["decoys_read"] == [] for r in records)
    assert "Read a decoy" in capsys.readouterr().out


def test_redo_needs_resume_and_rescore_needs_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv, message in [
        (["--dry-run", "--redo"], "--redo needs --resume"),
        (["--rescore", str(tmp_path)], "is not an evaluation run folder"),
        (["--dry-run", "--jobs", "0"], "--jobs must be at least 1"),
    ]:
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert message in capsys.readouterr().err
