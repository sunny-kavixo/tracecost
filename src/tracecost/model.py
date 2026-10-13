"""The one shape every reader produces and every downstream step consumes."""

from __future__ import annotations

from dataclasses import dataclass, field

# Span kinds are normalized across dialects: OTel `gen_ai.operation.name`, OpenInference
# `openinference.span.kind` and Langfuse observation `type` all map onto this small set.
SPAN_KINDS = ("llm", "embedding", "tool", "agent", "chain", "retriever", "other")


@dataclass(frozen=True)
class Usage:
    """Token counts for one LLM call, in OpenTelemetry GenAI semantics.

    `input_tokens` is the total prompt size *including* cache reads and writes, and
    `output_tokens` is the total completion size *including* reasoning tokens. That is how the
    OTel registry defines them ("The value SHOULD be included in gen_ai.usage.input_tokens").
    Readers for dialects that store exclusive counts (Langfuse) convert on the way in.

    `cache_reported` records whether the exporter wrote *any* cache attribute. Zero cache tokens
    with `cache_reported=False` may mean "no cache" or "not reported" (ollama#15758), and the
    report says so.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cache_reported: bool = False

    @property
    def uncached_input_tokens(self) -> int:
        """Input tokens billed at the plain input rate."""
        return max(self.input_tokens - self.cache_read_tokens - self.cache_write_tokens, 0)

    @property
    def plain_output_tokens(self) -> int:
        """Output tokens that are not reasoning tokens."""
        return max(self.output_tokens - self.reasoning_tokens, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_reported=self.cache_reported or other.cache_reported,
        )


@dataclass(frozen=True)
class SuppliedCost:
    """A cost the instrumentation already computed and attached to the span."""

    amount: float
    currency: str = "USD"
    source: str = "supplied"


@dataclass(frozen=True)
class Span:
    """Any span in the export, generation or not. Needed to roll cost up to roots."""

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str = "other"
    start_ns: int | None = None
    end_ns: int | None = None
    session_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class Generation:
    """One LLM (or embedding) call with everything pricing and rollups need."""

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    model: str | None
    provider: str | None
    usage: Usage
    supplied_cost: SuppliedCost | None = None
    session_id: str | None = None
    user_id: str | None = None
    service_tier: str | None = None
    kind: str = "llm"
    source: str = "otlp"


@dataclass
class Export:
    """Everything read from one or more files."""

    spans: list[Span] = field(default_factory=list)
    generations: list[Generation] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # trace id -> (session id, user id) from trace-level records (Langfuse traces export)
    trace_meta: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    def extend(self, other: Export) -> None:
        self.spans.extend(other.spans)
        self.generations.extend(other.generations)
        self.sources.extend(other.sources)
        self.trace_meta.update(other.trace_meta)
