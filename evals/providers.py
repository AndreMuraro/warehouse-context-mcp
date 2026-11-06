"""Model providers for the evaluation: one neutral interface, two wire formats.

Almost every hosted model speaks either Anthropic's Messages API or the OpenAI-style
Chat Completions API (OpenAI, OpenRouter, Moonshot/Kimi, Groq, Together, Mistral,
DeepSeek, Gemini's compatibility endpoint, Ollama, LiteLLM and vLLM proxies). Two small
adapters over plain HTTP cover all of them without a vendor SDK per provider.

Assistant turns are replayed to the provider exactly as it returned them, so fields the
harness does not model (thinking blocks, ``reasoning_content``) survive a tool loop.
"""

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx2

type Json = dict[str, Any]


class Wire(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass(frozen=True, slots=True)
class Preset:
    wire: Wire
    base_url: str
    key_env: str | None
    """Environment variable holding the API key; ``None`` for local servers."""


PRESETS: Mapping[str, Preset] = {
    "anthropic": Preset(Wire.ANTHROPIC, "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "openai": Preset(Wire.OPENAI, "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": Preset(Wire.OPENAI, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "moonshot": Preset(Wire.OPENAI, "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    "groq": Preset(Wire.OPENAI, "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "together": Preset(Wire.OPENAI, "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "mistral": Preset(Wire.OPENAI, "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "deepseek": Preset(Wire.OPENAI, "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "gemini": Preset(
        Wire.OPENAI, "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"
    ),
    "ollama": Preset(Wire.OPENAI, "http://localhost:11434/v1", None),
}
ALIASES: Mapping[str, str] = {"kimi": "moonshot", "claude": "anthropic", "google": "gemini"}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Json


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Json


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Turn:
    """One model reply: its text, the tools it called, and the native message to replay."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    native: Json = field(default_factory=dict)
    cached: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    call: ToolCall
    content: str


type Message = str | Turn | tuple[ToolResult, ...]
"""A user prompt, an assistant turn, or the results of that turn's tool calls."""


class Provider(Protocol):
    label: str

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Turn: ...


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class HttpProvider:
    """A model behind one of the two wire formats, with retries and a response cache."""

    wire: Wire
    base_url: str
    model: str
    api_key: str | None
    label: str
    temperature: float | None = 0.0
    max_tokens: int = 4096
    cache_dir: Path | None = None
    client: httpx2.Client = field(default_factory=lambda: httpx2.Client(timeout=180.0))
    retries: int = 5
    salt: str = ""
    """Part of the cache key, so repeated runs of a question are separate samples."""

    def complete(self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Turn:
        if self.wire is Wire.ANTHROPIC:
            url, body = f"{self.base_url}/messages", self._anthropic_body(system, messages, tools)
        else:
            url, body = (
                f"{self.base_url}/chat/completions",
                self._openai_body(system, messages, tools),
            )

        path, data = self._cached(url, body)
        hit = data is not None
        if data is None:
            data = self._post(url, body)
            self._store(path, data)
        turn = _parse_anthropic(data) if self.wire is Wire.ANTHROPIC else _parse_openai(data)
        return _as_cached(turn) if hit else turn

    # --- request bodies ----------------------------------------------------------------

    def _anthropic_body(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Json:
        wire: list[Json] = []
        for message in messages:
            match message:
                case str():
                    wire.append({"role": "user", "content": message})
                case Turn(native=native):
                    wire.append({"role": "assistant", "content": native["content"]})
                case results:
                    wire.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": r.call.id,
                                    "content": r.content,
                                }
                                for r in results
                            ],
                        }
                    )
        body: Json = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": wire,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    def _openai_body(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Json:
        wire: list[Json] = [{"role": "system", "content": system}]
        for message in messages:
            match message:
                case str():
                    wire.append({"role": "user", "content": message})
                case Turn(native=native):
                    wire.append({k: v for k, v in native.items() if v is not None})
                case results:
                    wire.extend(
                        {"role": "tool", "tool_call_id": r.call.id, "content": r.content}
                        for r in results
                    )
        body: Json = {
            "model": self.model,
            "messages": wire,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    # --- transport ---------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.wire is Wire.ANTHROPIC:
            headers["anthropic-version"] = "2023-06-01"
            if self.api_key:
                headers["x-api-key"] = self.api_key
        elif self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, url: str, body: Json) -> Json:
        delay = 2.0
        for attempt in range(self.retries + 1):
            try:
                response = self.client.post(url, json=body, headers=self._headers())
            except httpx2.TransportError as err:
                if attempt == self.retries:
                    msg = f"{self.label}: {type(err).__name__}: {err}"
                    raise ProviderError(msg) from err
            else:
                if response.status_code == 200:  # noqa: PLR2004
                    data = response.json()
                    if not isinstance(data, dict):
                        msg = f"{self.label}: unexpected response {data!r:.200}"
                        raise ProviderError(msg)
                    return data
                retryable = response.status_code in {408, 409, 425, 429} or (
                    response.status_code >= 500  # noqa: PLR2004
                )
                if not retryable or attempt == self.retries:
                    msg = f"{self.label}: HTTP {response.status_code}: {response.text[:500]}"
                    raise ProviderError(msg)
                delay = _retry_after(response) or delay
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise AssertionError  # pragma: no cover  the loop always returns or raises

    # --- cache ---------------------------------------------------------------------------

    def _cached(self, url: str, body: Json) -> tuple[Path | None, Json | None]:
        if self.cache_dir is None:
            return None, None
        material = json.dumps([url, body, self.salt], sort_keys=True).encode()
        key = hashlib.sha256(material).hexdigest()
        path = self.cache_dir / key[:2] / f"{key}.json"
        if path.exists():
            return path, json.loads(path.read_text(encoding="utf-8"))
        return path, None

    @staticmethod
    def _store(path: Path | None, data: Json) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(data), encoding="utf-8")
        partial.replace(path)


def _as_cached(turn: Turn) -> Turn:
    return Turn(turn.text, turn.tool_calls, turn.usage, turn.native, cached=True)


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return min(float(response.headers.get("retry-after", "")), 60.0)
    except ValueError:
        return None


def _parse_anthropic(data: Json) -> Turn:
    blocks: list[Json] = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = tuple(
        ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
        for b in blocks
        if b.get("type") == "tool_use"
    )
    usage = data.get("usage") or {}
    return Turn(
        text=text,
        tool_calls=calls,
        usage=Usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        native={"content": blocks},
    )


# Fields sent back on the next request. Reasoning fields matter: Moonshot's thinking models
# and OpenRouter's reasoning models need them in a tool loop. Anything else a provider adds
# to its reply (annotations, refusal, audio) is response-only and may be rejected.
_REPLAYED = frozenset(
    {"content", "tool_calls", "name", "reasoning_content", "reasoning", "reasoning_details"}
)


def _parse_openai(data: Json) -> Turn:
    choices = data.get("choices") or []
    if not choices:
        msg = f"no choices in response: {json.dumps(data)[:300]}"
        raise ProviderError(msg)
    message: Json = choices[0].get("message") or {}
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments = function.get("arguments") or "{}"
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            parsed = {"__unparsed__": arguments}
        calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{len(calls)}",
                name=function.get("name", ""),
                arguments=parsed if isinstance(parsed, dict) else {"value": parsed},
            )
        )
    usage = data.get("usage") or {}
    native = {"role": "assistant", **{k: v for k, v in message.items() if k in _REPLAYED}}
    return Turn(
        text=message.get("content") or "",
        tool_calls=tuple(calls),
        usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        native=native,
    )


def resolve(  # noqa: PLR0913  each option is one CLI flag
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
    wire: Wire | None = None,
    temperature: float | None = 0.0,
    cache_dir: Path | None = None,
    env_file: Path | None = None,
) -> HttpProvider:
    """Build a provider from a preset name, or a custom endpoint with ``--base-url``."""
    name = ALIASES.get(provider, provider)
    preset = PRESETS.get(name)
    if preset is None and base_url is None:
        known = ", ".join(sorted([*PRESETS, *ALIASES]))
        msg = f"unknown provider {provider!r}; use one of {known}, or --base-url"
        raise ProviderError(msg)
    chosen_wire = wire or (preset.wire if preset else Wire.OPENAI)
    url = (base_url or (preset.base_url if preset else "")).rstrip("/")
    key_env = api_key_env or (preset.key_env if preset else None)
    api_key = None
    if key_env:
        api_key = os.environ.get(key_env) or _from_env_file(env_file, key_env)
        if not api_key:
            msg = f"{key_env} is not set (environment or {env_file or '.env'})"
            raise ProviderError(msg)
    return HttpProvider(
        wire=chosen_wire,
        base_url=url,
        model=model,
        api_key=api_key,
        label=f"{name}:{model}",
        temperature=temperature,
        cache_dir=cache_dir,
    )


def _from_env_file(path: Path | None, key: str) -> str | None:
    """Read one KEY=VALUE line; the value is never printed or logged."""
    if path is None or not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.strip().removeprefix("export ").strip() == key:
            return value.strip().strip("'\"") or None
    return None
