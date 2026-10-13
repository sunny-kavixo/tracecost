"""Attribute-name tables for the span dialects tracecost understands.

Each dialect is data: an ordered tuple of candidate keys per field, first match wins. Adding a
dialect or an alias is a table entry, not a code path.

Sources, checked 2026-10-13:

- OTel GenAI: ``model/gen-ai/registry.yaml`` in open-telemetry/semantic-conventions-genai.
  ``gen_ai.usage.input_tokens`` includes cache reads/writes; ``gen_ai.usage.output_tokens``
  includes reasoning. ``gen_ai.provider.name`` replaced the removed ``gen_ai.system``;
  ``gen_ai.usage.prompt_tokens`` / ``completion_tokens`` are the pre-2024 names some exporters
  still write, and ``gen_ai.usage.cache_creation.input_tokens`` is the spelling mlflow#22745
  received. Service tier lives in the OpenAI extension registry (``model/openai/registry.yaml``).
- OpenInference: ``spec/semantic_conventions.md`` in Arize-ai/openinference. Token counts are
  ``llm.token_count.prompt`` / ``completion`` with ``prompt_details.cache_read`` /
  ``cache_write`` and ``completion_details.reasoning``; costs are ``llm.cost.*`` in USD.
- Generic supplied cost: ``cost.amount`` / ``cost.currency`` / ``cost.source``, the shape
  proposed in phoenix#13655 for non-token costs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

Attributes = Mapping[str, object]


@dataclass(frozen=True)
class Dialect:
    name: str
    model: tuple[str, ...]
    provider: tuple[str, ...]
    input_tokens: tuple[str, ...]
    output_tokens: tuple[str, ...]
    cache_read: tuple[str, ...]
    cache_write: tuple[str, ...]
    reasoning: tuple[str, ...]
    session: tuple[str, ...]
    user: tuple[str, ...]
    service_tier: tuple[str, ...]
    cost_total: tuple[str, ...]
    cost_currency: tuple[str, ...]
    cost_source: tuple[str, ...]
    kind_key: tuple[str, ...]
    marker_prefixes: tuple[str, ...]

    def matches(self, attrs: Attributes) -> bool:
        return any(k.startswith(self.marker_prefixes) for k in attrs)


GEN_AI = Dialect(
    name="gen_ai",
    model=("gen_ai.response.model", "gen_ai.request.model"),
    provider=("gen_ai.provider.name", "gen_ai.system"),
    input_tokens=("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
    output_tokens=("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
    cache_read=(
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_read_input_tokens",
    ),
    cache_write=(
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
    ),
    reasoning=("gen_ai.usage.reasoning.output_tokens",),
    session=("gen_ai.conversation.id", "session.id"),
    user=("user.id", "enduser.id"),
    service_tier=("openai.response.service_tier", "openai.request.service_tier"),
    cost_total=("cost.amount",),
    cost_currency=("cost.currency",),
    cost_source=("cost.source",),
    kind_key=("gen_ai.operation.name",),
    marker_prefixes=("gen_ai.",),
)

OPENINFERENCE = Dialect(
    name="openinference",
    model=("llm.response.model_name", "llm.model_name", "llm.request.model_name"),
    provider=("llm.provider", "llm.system"),
    input_tokens=("llm.token_count.prompt",),
    output_tokens=("llm.token_count.completion",),
    cache_read=("llm.token_count.prompt_details.cache_read",),
    cache_write=("llm.token_count.prompt_details.cache_write",),
    reasoning=("llm.token_count.completion_details.reasoning",),
    session=("session.id",),
    user=("user.id",),
    service_tier=("llm.invocation_parameters.service_tier",),
    cost_total=("llm.cost.total", "cost.amount"),
    cost_currency=("cost.currency",),
    cost_source=("cost.source",),
    kind_key=("openinference.span.kind",),
    marker_prefixes=("openinference.", "llm."),
)

DIALECTS: tuple[Dialect, ...] = (GEN_AI, OPENINFERENCE)

# gen_ai.operation.name values (registry enum) and OpenInference span kinds → normalized kind.
KIND_BY_OPERATION: dict[str, str] = {
    "chat": "llm",
    "text_completion": "llm",
    "generate_content": "llm",
    "fetch_response": "llm",
    "embeddings": "embedding",
    "execute_tool": "tool",
    "invoke_agent": "agent",
    "create_agent": "agent",
    "invoke_workflow": "chain",
    "plan": "chain",
    "retrieval": "retriever",
    # OpenInference span kinds
    "LLM": "llm",
    "EMBEDDING": "embedding",
    "TOOL": "tool",
    "AGENT": "agent",
    "CHAIN": "chain",
    "RETRIEVER": "retriever",
}

GENERATION_KINDS = frozenset({"llm", "embedding"})


def first(attrs: Attributes, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = attrs.get(key)
        if value is not None:
            return value
    return None


def first_int(attrs: Attributes, keys: tuple[str, ...]) -> int | None:
    value = first(attrs, keys)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def first_float(attrs: Attributes, keys: tuple[str, ...]) -> float | None:
    value = first(attrs, keys)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def first_str(attrs: Attributes, keys: tuple[str, ...]) -> str | None:
    value = first(attrs, keys)
    return None if value is None else str(value)


def detect(attrs: Attributes) -> Dialect | None:
    for dialect in DIALECTS:
        if dialect.matches(attrs):
            return dialect
    return None


def normalized_kind(dialect: Dialect, attrs: Attributes) -> str:
    raw = first_str(attrs, dialect.kind_key)
    if raw is None:
        return "other"
    return KIND_BY_OPERATION.get(raw, "other")
