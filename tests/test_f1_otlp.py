"""F1: read OTLP JSON / JSONL exports that carry OTel GenAI attributes.

Attribute names follow the OpenTelemetry GenAI semantic conventions registry
(open-telemetry/semantic-conventions-genai, model/gen-ai/registry.yaml):
`gen_ai.usage.input_tokens` includes cache reads and writes, `gen_ai.usage.output_tokens`
includes reasoning tokens, and the detailed counts are
`gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens` and
`gen_ai.usage.reasoning.output_tokens`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracecost import Usage, load

TRACE = "5b8aa5a2d2c872e8321cf37308d69df2"
ROOT = "051581bf3cb55c13"
CHILD = "7a3f1c2e9b0d4e5f"


def attr(key: str, value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def chat_span(**overrides: object) -> dict[str, object]:
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-5",
        "gen_ai.response.model": "gpt-5-2025-08-07",
        "gen_ai.usage.input_tokens": 300,
        "gen_ai.usage.output_tokens": 50,
        "gen_ai.usage.cache_read.input_tokens": 40,
        "gen_ai.usage.cache_write.input_tokens": 10,
        "gen_ai.usage.reasoning.output_tokens": 5,
        "gen_ai.conversation.id": "sess-1",
        "user.id": "u-1",
    }
    attributes.update(overrides)
    return {
        "traceId": TRACE,
        "spanId": CHILD,
        "parentSpanId": ROOT,
        "name": "chat gpt-5",
        "kind": 3,
        "startTimeUnixNano": "1760000000000000000",
        "endTimeUnixNano": "1760000001000000000",
        "attributes": [attr(k, v) for k, v in attributes.items() if v is not None],
    }


def root_span() -> dict[str, object]:
    return {
        "traceId": TRACE,
        "spanId": ROOT,
        "name": "invoke_agent planner",
        "kind": 1,
        "startTimeUnixNano": "1759999999000000000",
        "endTimeUnixNano": "1760000002000000000",
        "attributes": [attr("gen_ai.operation.name", "invoke_agent")],
    }


def export_request(*spans: dict[str, object]) -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [attr("service.name", "demo-agent")]},
                "scopeSpans": [{"scope": {"name": "demo"}, "spans": list(spans)}],
            }
        ]
    }


@pytest.fixture
def otlp_json(tmp_path: Path) -> Path:
    p = tmp_path / "traces.json"
    p.write_text(json.dumps(export_request(root_span(), chat_span())))
    return p


def test_reads_generations_with_cache_tokens(otlp_json: Path) -> None:
    export = load(otlp_json)

    assert [s.span_id for s in export.spans] == [ROOT, CHILD]
    assert len(export.generations) == 1
    gen = export.generations[0]
    assert gen.trace_id == TRACE
    assert gen.span_id == CHILD
    assert gen.parent_id == ROOT
    assert gen.model == "gpt-5-2025-08-07"  # response model wins over request model
    assert gen.provider == "openai"
    assert gen.usage == Usage(
        input_tokens=300,
        output_tokens=50,
        cache_read_tokens=40,
        cache_write_tokens=10,
        reasoning_tokens=5,
        cache_reported=True,
    )
    assert gen.session_id == "sess-1"
    assert gen.user_id == "u-1"
    assert gen.supplied_cost is None
    assert gen.source == "otlp"


def test_root_span_without_usage_is_a_span_not_a_generation(otlp_json: Path) -> None:
    export = load(otlp_json)
    root = export.spans[0]
    assert root.parent_id is None
    assert root.kind == "agent"
    assert root.name == "invoke_agent planner"


def test_jsonl_one_request_per_line(tmp_path: Path) -> None:
    p = tmp_path / "traces.jsonl"
    lines = [json.dumps(export_request(root_span())), json.dumps(export_request(chat_span()))]
    p.write_text("\n".join(lines) + "\n")

    export = load(p)

    assert len(export.spans) == 2
    assert len(export.generations) == 1


def test_cache_creation_alias_and_missing_cache_attributes(tmp_path: Path) -> None:
    """Older exporters wrote `gen_ai.usage.cache_creation.input_tokens` (mlflow#22745).

    A span with no cache attributes at all is loaded with cache_reported=False so the report can
    warn that zero cache may mean "not reported" (ollama#15758).
    """
    aliased = chat_span(
        **{
            "gen_ai.usage.cache_write.input_tokens": None,
            "gen_ai.usage.cache_creation.input_tokens": 10,
        }
    )
    silent = chat_span(
        **{
            "gen_ai.usage.cache_read.input_tokens": None,
            "gen_ai.usage.cache_write.input_tokens": None,
            "gen_ai.usage.reasoning.output_tokens": None,
        }
    )
    silent["spanId"] = "0000000000000001"
    p = tmp_path / "t.json"
    p.write_text(json.dumps(export_request(aliased, silent)))

    gens = load(p).generations

    assert gens[0].usage.cache_write_tokens == 10
    assert gens[1].usage == Usage(input_tokens=300, output_tokens=50, cache_reported=False)


def test_deprecated_gen_ai_system_and_request_model_fallback(tmp_path: Path) -> None:
    span = chat_span(
        **{
            "gen_ai.provider.name": None,
            "gen_ai.system": "anthropic",
            "gen_ai.response.model": None,
        }
    )
    p = tmp_path / "t.json"
    p.write_text(json.dumps(export_request(span)))

    gen = load(p).generations[0]

    assert gen.provider == "anthropic"
    assert gen.model == "gpt-5"


def test_multiple_files_are_concatenated(otlp_json: Path, tmp_path: Path) -> None:
    second = tmp_path / "second.json"
    span = chat_span()
    span["traceId"] = "a" * 32
    span["spanId"] = "b" * 16
    second.write_text(json.dumps(export_request(span)))

    export = load(otlp_json, second)

    assert len(export.generations) == 2
    assert {g.trace_id for g in export.generations} == {TRACE, "a" * 32}


def test_unreadable_file_raises_with_path(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match=r"bad\.json"):
        load(bad)
