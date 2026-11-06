"""A scripted stand-in for a model: runs each question's reference SQL, then submits.

It needs no key and no network, so ``--dry-run`` and the tests exercise every part of
the harness: arms, tool calls, the query policy, scoring and the report. Its scores say
nothing about any model; a reference query the server's policy refuses shows up as a
miss in the ``mcp`` arm, which is useful to know when adding questions.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .providers import Message, ToolCall, ToolResult, ToolSpec, Turn, Usage
from .scoring import Kind, Question


class ScriptedProvider:
    label = "scripted"

    def __init__(self, questions: Sequence[Question]) -> None:
        self._by_text: Mapping[str, Question] = {q.question: q for q in questions}

    def complete(self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Turn:
        del system
        question = self._by_text[str(messages[0])]
        names = {t.name for t in tools}
        query_tool = "run_query" if "run_query" in names else "run_sql"

        if question.kind is Kind.REFUSE or question.sql is None:
            return _submit("I can't share personal contact details from the warehouse.")
        if len(messages) == 1:
            return Turn(
                text="",
                tool_calls=(ToolCall("call_1", query_tool, {"sql": question.sql}),),
                usage=Usage(),
            )
        last = messages[-1]
        if not isinstance(last, tuple) or not last:
            return _submit("")
        return _submit(_first_value(last[0]))


def _submit(answer: str) -> Turn:
    return Turn("", (ToolCall("call_submit", "submit_answer", {"answer": answer}),), Usage())


def _first_value(result: ToolResult) -> str:
    try:
        data: Any = json.loads(result.content)
    except json.JSONDecodeError:
        return f"could not answer: {result.content[:200]}"
    if not isinstance(data, dict):
        return "could not answer"
    if data.get("ok") is False:  # refused by the server's policy
        return f"could not answer: {data.get('error', '')}"
    rows = data.get("rows") or []
    if not rows:
        return "no rows"
    first = rows[0]
    value = next(iter(first.values())) if isinstance(first, dict) else first[0]
    return str(value)
