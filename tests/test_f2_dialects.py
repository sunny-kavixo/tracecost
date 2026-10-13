"""F2: the same LLM call expressed as OTel GenAI, OpenInference and Langfuse loads identically.

Sources checked 2026-10-13:
- OpenInference keys: spec/semantic_conventions.md (Arize-ai/openinference).
- Phoenix spans export: `Client.spans.get_spans_dataframe()` columns `context.span_id`,
  `context.trace_id`, `parent_id`, `name`, `span_kind`, `attributes.<key>`; written with
  `df.to_json(orient="records", lines=True)`.
- Langfuse: `GET /api/public/observations` -> `{"data": [Observation...], "meta": {...}}`
  (cloud.langfuse.com/generated/api/openapi.yml). `usageDetails` keys are *exclusive*:
  `input` excludes `input_cached_tokens` / `cache_read_input_tokens`, `output` excludes
  `output_reasoning_tokens` (langfuse.com/docs/observability/features/token-and-cost-tracking).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_f1_otlp import TRACE, attr, export_request, root_span
from tracecost import Generation, SuppliedCost, Usage, load

CHILD = "7a3f1c2e9b0d4e5f"
ROOT = root_span()["spanId"]

EXPECTED = Generation(
    trace_id=TRACE,
    span_id=CHILD,
    parent_id=str(ROOT),
    name="ChatCompletion",
    model="claude-sonnet-4-5",
    provider="anthropic",
    usage=Usage(
        input_tokens=1000,
        output_tokens=120,
        cache_read_tokens=800,
        cache_write_tokens=100,
        reasoning_tokens=20,
        cache_reported=True,
    ),
    supplied_cost=None,
    session_id="sess-1",
    user_id="u-1",
    service_tier=None,
    kind="llm",
    source="otlp",
)


def gen_ai_file(tmp_path: Path) -> Path:
    span = {
        "traceId": TRACE,
        "spanId": CHILD,
        "parentSpanId": ROOT,
        "name": "ChatCompletion",
        "attributes": [
            attr("gen_ai.operation.name", "chat"),
            attr("gen_ai.provider.name", "anthropic"),
            attr("gen_ai.request.model", "claude-sonnet-4-5"),
            attr("gen_ai.usage.input_tokens", 1000),
            attr("gen_ai.usage.output_tokens", 120),
            attr("gen_ai.usage.cache_read.input_tokens", 800),
            attr("gen_ai.usage.cache_write.input_tokens", 100),
            attr("gen_ai.usage.reasoning.output_tokens", 20),
            attr("gen_ai.conversation.id", "sess-1"),
            attr("user.id", "u-1"),
        ],
    }
    p = tmp_path / "gen_ai.json"
    p.write_text(json.dumps(export_request(root_span(), span)))
    return p


def openinference_otlp_file(tmp_path: Path) -> Path:
    span = {
        "traceId": TRACE,
        "spanId": CHILD,
        "parentSpanId": ROOT,
        "name": "ChatCompletion",
        "attributes": [
            attr("openinference.span.kind", "LLM"),
            attr("llm.provider", "anthropic"),
            attr("llm.model_name", "claude-sonnet-4-5"),
            attr("llm.token_count.prompt", 1000),
            attr("llm.token_count.completion", 120),
            attr("llm.token_count.prompt_details.cache_read", 800),
            attr("llm.token_count.prompt_details.cache_write", 100),
            attr("llm.token_count.completion_details.reasoning", 20),
            attr("session.id", "sess-1"),
            attr("user.id", "u-1"),
        ],
    }
    p = tmp_path / "openinference.json"
    p.write_text(json.dumps(export_request(root_span(), span)))
    return p


def phoenix_jsonl_file(tmp_path: Path) -> Path:
    rows = [
        {
            "name": "invoke_agent planner",
            "span_kind": "AGENT",
            "parent_id": None,
            "start_time": "2026-10-09T12:00:00+00:00",
            "end_time": "2026-10-09T12:00:03+00:00",
            "status_code": "OK",
            "context.span_id": ROOT,
            "context.trace_id": TRACE,
            "attributes.openinference.span.kind": "AGENT",
        },
        {
            "name": "ChatCompletion",
            "span_kind": "LLM",
            "parent_id": ROOT,
            "start_time": "2026-10-09T12:00:01+00:00",
            "end_time": "2026-10-09T12:00:02+00:00",
            "status_code": "OK",
            "context.span_id": CHILD,
            "context.trace_id": TRACE,
            "attributes.openinference.span.kind": "LLM",
            "attributes.llm.provider": "anthropic",
            "attributes.llm.model_name": "claude-sonnet-4-5",
            "attributes.llm.token_count.prompt": 1000,
            "attributes.llm.token_count.completion": 120,
            "attributes.llm.token_count.prompt_details.cache_read": 800,
            "attributes.llm.token_count.prompt_details.cache_write": 100,
            "attributes.llm.token_count.completion_details.reasoning": 20,
            "attributes.session.id": "sess-1",
            "attributes.user.id": "u-1",
        },
    ]
    p = tmp_path / "phoenix.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def langfuse_file(tmp_path: Path, *, with_cost: bool = False) -> Path:
    observations = [
        {
            "id": ROOT,
            "traceId": TRACE,
            "parentObservationId": None,
            "type": "AGENT",
            "name": "invoke_agent planner",
            "startTime": "2026-10-09T12:00:00.000Z",
            "endTime": "2026-10-09T12:00:03.000Z",
            "model": None,
            "usageDetails": {},
            "costDetails": {},
        },
        {
            "id": CHILD,
            "traceId": TRACE,
            "parentObservationId": ROOT,
            "type": "GENERATION",
            "name": "ChatCompletion",
            "startTime": "2026-10-09T12:00:01.000Z",
            "endTime": "2026-10-09T12:00:02.000Z",
            "model": "claude-sonnet-4-5",
            # exclusive buckets: 1000 total input = 100 plain + 800 cache read + 100 cache write
            "usageDetails": {
                "input": 100,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 100,
                "output": 100,
                "output_reasoning_tokens": 20,
                "total": 1120,
            },
            "costDetails": {"input": 0.0003, "output": 0.0015, "total": 0.0018}
            if with_cost
            else {},
            "metadata": {"ls_provider": "anthropic"},
        },
    ]
    traces = [{"id": TRACE, "sessionId": "sess-1", "userId": "u-1", "name": "chat-turn"}]
    p = tmp_path / "langfuse.json"
    p.write_text(json.dumps({"data": observations, "traces": traces, "meta": {"page": 1}}))
    return p


@pytest.mark.parametrize(
    ("make", "source"),
    [
        (gen_ai_file, "otlp"),
        (openinference_otlp_file, "otlp"),
        (phoenix_jsonl_file, "phoenix"),
        (langfuse_file, "langfuse"),
    ],
)
def test_three_dialects_same_generation(tmp_path: Path, make: object, source: str) -> None:
    path = make(tmp_path)  # type: ignore[operator]
    export = load(path)

    assert len(export.spans) == 2
    assert export.spans[0].kind == "agent"
    assert export.spans[0].parent_id is None
    assert len(export.generations) == 1
    got = export.generations[0]
    assert got == Generation(**{**EXPECTED.__dict__, "source": source})


def test_langfuse_cost_details_become_supplied_cost(tmp_path: Path) -> None:
    gen = load(langfuse_file(tmp_path, with_cost=True)).generations[0]
    assert gen.supplied_cost == SuppliedCost(amount=0.0018, currency="USD", source="costDetails")


def test_langfuse_accepts_bare_list_and_openai_cached_key(tmp_path: Path) -> None:
    rows = [
        {
            "id": "g1",
            "traceId": "t1",
            "parentObservationId": None,
            "type": "GENERATION",
            "name": "openai.chat",
            "model": "gpt-5",
            "usageDetails": {
                "input": 86,
                "input_cached_tokens": 17817,
                "output": 25,
                "total": 17928,
            },
        }
    ]
    p = tmp_path / "obs.json"
    p.write_text(json.dumps(rows))

    gen = load(p).generations[0]

    assert gen.usage == Usage(
        input_tokens=17903, output_tokens=25, cache_read_tokens=17817, cache_reported=True
    )
    assert gen.provider is None
    assert gen.session_id is None


def test_openinference_llm_cost_total_is_supplied_cost(tmp_path: Path) -> None:
    span = {
        "traceId": TRACE,
        "spanId": CHILD,
        "name": "ChatCompletion",
        "attributes": [
            attr("openinference.span.kind", "LLM"),
            attr("llm.model_name", "gpt-5"),
            attr("llm.token_count.prompt", 10),
            attr("llm.token_count.completion", 5),
            attr("llm.cost.total", 0.0125),
        ],
    }
    p = tmp_path / "oi.json"
    p.write_text(json.dumps(export_request(span)))

    gen = load(p).generations[0]

    assert gen.supplied_cost == SuppliedCost(amount=0.0125, currency="USD", source="llm.cost.total")
    assert gen.usage.cache_reported is False


def test_tool_span_with_model_attribute_is_not_a_generation(tmp_path: Path) -> None:
    span = {
        "traceId": TRACE,
        "spanId": CHILD,
        "name": "execute_tool search",
        "attributes": [
            attr("gen_ai.operation.name", "execute_tool"),
            attr("gen_ai.request.model", "gpt-5"),
        ],
    }
    p = tmp_path / "tool.json"
    p.write_text(json.dumps(export_request(span)))

    export = load(p)

    assert export.generations == []
    assert export.spans[0].kind == "tool"


def test_mixed_formats_in_one_call(tmp_path: Path) -> None:
    export = load(gen_ai_file(tmp_path), langfuse_file(tmp_path))
    assert len(export.generations) == 2
    assert export.sources == [str(tmp_path / "gen_ai.json"), str(tmp_path / "langfuse.json")]
