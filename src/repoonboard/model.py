"""The model boundary.

Everything that talks to a language model lives behind the `Generator`
protocol. Two things follow from that:

- tests never make a network call — they use `RecordedGenerator`, which
  replays fixtures and fails loudly if asked for a station it has no
  recording for;
- the model is a component with one narrow job (return JSON matching a
  schema), not an authority the rest of the program defers to.

The Anthropic SDK is imported lazily inside the client, so the package
installs and the whole test suite runs without the optional `llm` extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000


class GenerationError(RuntimeError):
    """The model could not be reached, or returned nothing usable."""


class Generator(Protocol):
    """Turns a prompt into raw JSON text. Parsing happens elsewhere."""

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str: ...


def tighten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a pydantic-generated schema acceptable to structured outputs.

    Structured outputs require every object to declare `additionalProperties:
    false` and to list all of its properties as required. Pydantic emits
    neither for optional fields, so the schema is walked and tightened rather
    than hand-maintained in parallel with the models — a hand-copy would drift
    the first time a field is renamed.
    """
    tightened = json.loads(json.dumps(schema))  # deep copy, no shared state

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = sorted(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tightened)
    return tightened


@dataclass
class RecordedGenerator:
    """Replays recorded responses. The only generator used in tests.

    Responses are consumed in order. Running out is an error rather than a
    silent fallback, so a test that expects two attempts and gets three fails
    instead of quietly passing.
    """

    responses: list[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise GenerationError(
                f"RecordedGenerator exhausted after {len(self.calls)} call(s); "
                "the pipeline asked for more attempts than were recorded"
            )
        return self.responses.pop(0)


@dataclass
class AnthropicGenerator:
    """Calls the Anthropic Messages API. Never used in tests."""

    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    _client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on extras
                raise GenerationError(
                    "The anthropic package is required for model generation. "
                    'Install it with: pip install "repoonboard[llm]"'
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        client = self._ensure_client()

        # No temperature: sampling parameters are rejected on this model, and
        # determinism here comes from caching on the commit hash, not from
        # asking the model to be deterministic.
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )

        if response.stop_reason == "refusal":
            raise GenerationError("the model declined to answer for this file")

        for block in response.content:
            if block.type == "text":
                return block.text
        raise GenerationError("the model returned no text content")


@dataclass
class CachingGenerator:
    """Wraps a generator with an on-disk cache.

    Keyed on the prompt itself, so a re-run over an unchanged commit costs
    nothing and returns byte-identical content — which is what lets the tour
    be reproducible rather than merely repeatable.
    """

    inner: Generator
    directory: Path

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        from hashlib import blake2b

        digest = blake2b(
            json.dumps([system, user, schema], sort_keys=True).encode("utf-8"),
            digest_size=16,
        ).hexdigest()
        entry = self.directory / f"{digest}.json"

        if entry.is_file():
            return entry.read_text(encoding="utf-8")

        result = self.inner.complete(system, user, schema)
        self.directory.mkdir(parents=True, exist_ok=True)
        entry.write_text(result, encoding="utf-8")
        return result
