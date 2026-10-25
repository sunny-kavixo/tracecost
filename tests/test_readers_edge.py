"""Edge cases in the readers that real exports hit: snake_case OTLP, odd AnyValues, bad rows."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tests.test_f1_otlp import TRACE, attr, export_request
from tracecost import load
from tracecost.readers import otlp, phoenix
from tracecost.readers.langfuse import usage_from_details


def test_snake_case_otlp_and_array_values(tmp_path: Path) -> None:
    doc = {
        "resource_spans": [
            {
                "scope_spans": [
                    {
                        "spans": [
                            {
                                "trace_id": TRACE,
                                "span_id": "0000000000000001",
                                "parent_span_id": "",
                                "name": "chat",
                                "start_time_unix_nano": "1700000000000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"string_value": "gpt-4o"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"int_value": 10},
                                    },
                                    {
                                        "key": "gen_ai.usage.output_tokens",
                                        "value": {"double_value": 2.0},
                                    },
                                    {
                                        "key": "gen_ai.response.finish_reasons",
                                        "value": {
                                            "array_value": {"values": [{"string_value": "stop"}]}
                                        },
                                    },
                                    {
                                        "key": "meta",
                                        "value": {
                                            "kvlist_value": {
                                                "values": [
                                                    {"key": "a", "value": {"bool_value": True}}
                                                ]
                                            }
                                        },
                                    },
                                    {"key": "blob", "value": {"bytes_value": "AAEC"}},
                                    {"key": "weird", "value": {}},
                                    {"value": {"string_value": "no key"}},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    p = tmp_path / "snake.json"
    p.write_text(json.dumps(doc))
    export = load(p)
    assert export.spans[0].parent_id is None
    assert export.spans[0].start_ns == 1_700_000_000_000_000_000
    assert export.generations[0].usage.input_tokens == 10
    assert export.generations[0].usage.output_tokens == 2


def test_attribute_value_decoding() -> None:
    assert otlp.attribute_value({"stringValue": "x"}) == "x"
    assert otlp.attribute_value({"intValue": "7"}) == 7
    assert otlp.attribute_value({"boolValue": False}) is False
    assert otlp.attribute_value({"arrayValue": {"values": [{"intValue": 1}]}}) == [1]
    assert otlp.attribute_value(
        {"kvlistValue": {"values": [{"key": "k", "value": {"intValue": 1}}]}}
    ) == {"k": 1}
    assert otlp.attribute_value({"bytesValue": "AA=="}) == "AA=="
    assert otlp.attribute_value({}) is None


def test_int_and_float_attribute_coercion(tmp_path: Path) -> None:
    span = {
        "traceId": TRACE,
        "spanId": "0000000000000001",
        "name": "chat",
        "startTimeUnixNano": "not-a-number",
        "attributes": [
            attr("gen_ai.request.model", "gpt-4o"),
            {"key": "gen_ai.usage.input_tokens", "value": {"stringValue": "12.0"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"stringValue": "nope"}},
            {"key": "gen_ai.usage.cache_read.input_tokens", "value": {"boolValue": True}},
            {"key": "cost.amount", "value": {"stringValue": "0.5"}},
        ],
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(export_request(span)))
    gen = load(p).generations[0]
    assert gen.usage.input_tokens == 12
    assert gen.usage.output_tokens == 0
    assert gen.usage.cache_read_tokens == 0
    assert gen.supplied_cost is not None
    assert gen.supplied_cost.amount == 0.5


def test_otlp_document_must_be_objects(tmp_path: Path) -> None:
    p = tmp_path / "t.json"
    p.write_text(json.dumps([{"resourceSpans": []}, 42]))
    with pytest.raises(ValueError, match="expected OTLP JSON objects"):
        load(p)


def test_empty_file_and_bad_jsonl_line(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("   \n")
    with pytest.raises(ValueError, match="empty"):
        load(empty)
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"resourceSpans": []}\n{oops\n')
    with pytest.raises(ValueError, match="line 2"):
        load(bad)


def test_gzip_otlp_matches_uncompressed_with_suffix_and_magic(tmp_path: Path) -> None:
    doc = export_request(
        {
            "traceId": TRACE,
            "spanId": "0000000000000001",
            "name": "chat",
            "attributes": [
                attr("gen_ai.request.model", "gpt-4o"),
                attr("gen_ai.usage.input_tokens", 10),
                attr("gen_ai.usage.output_tokens", 2),
            ],
        }
    )
    payload = json.dumps(doc)
    plain = tmp_path / "traces.json"
    plain.write_text(payload, encoding="utf-8")
    with_suffix = tmp_path / "traces.json.gz"
    without_suffix = tmp_path / "traces-compressed.json"
    for path in (with_suffix, without_suffix):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(payload)

    expected = load(plain)
    for compressed in (with_suffix, without_suffix):
        got = load(compressed)
        assert got.spans == expected.spans
        assert got.generations == expected.generations
        assert got.trace_meta == expected.trace_meta


def test_gzip_langfuse_matches_uncompressed(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "data": [
                {
                    "id": "generation-1",
                    "traceId": "trace-1",
                    "type": "GENERATION",
                    "name": "chat",
                    "model": "gpt-4o",
                    "usageDetails": {"input": 3, "output": 2},
                }
            ]
        }
    )
    plain = tmp_path / "langfuse.json"
    plain.write_text(payload, encoding="utf-8")
    compressed = tmp_path / "langfuse.json.gz"
    with gzip.open(compressed, "wt", encoding="utf-8") as handle:
        handle.write(payload)

    expected = load(plain)
    got = load(compressed)
    assert got.spans == expected.spans
    assert got.generations == expected.generations
    assert got.trace_meta == expected.trace_meta


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nothere"):
        load(tmp_path / "nothere.json")


def test_phoenix_nested_attributes_epoch_times_and_bad_row(tmp_path: Path) -> None:
    rows = [
        {
            "name": "llm",
            "span_kind": "LLM",
            "parent_id": None,
            "start_time": 1_700_000_000_000,
            "end_time": 1_700_000_001_000,
            "context.span_id": "s1",
            "context.trace_id": "t1",
            "attributes": {
                "llm": {"model_name": "gpt-4o", "token_count": {"prompt": 5, "completion": 1}}
            },
        }
    ]
    p = tmp_path / "phoenix.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    export = load(p)
    assert export.spans[0].start_ns == 1_700_000_000_000_000_000
    assert export.generations[0].model == "gpt-4o"
    assert export.generations[0].usage.input_tokens == 5

    assert phoenix.looks_like_phoenix([]) is False
    assert phoenix.looks_like_phoenix({"context.span_id": "x"}) is True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"context.span_id": "s"}, "not-a-row"]))
    with pytest.raises(ValueError, match="unrecognized format"):
        load(bad)


def test_phoenix_bad_timestamp_string(tmp_path: Path) -> None:
    rows = [
        {
            "name": "x",
            "span_kind": "CHAIN",
            "start_time": "yesterday",
            "context.span_id": "s",
            "context.trace_id": "t",
        }
    ]
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps(rows[0]) + "\n")
    export = load(p)
    assert export.spans[0].start_ns is None
    assert export.spans[0].kind == "chain"
    assert export.generations == []


def test_langfuse_usage_conversion_variants() -> None:
    usage = usage_from_details(
        {
            "input": "100",
            "input_cache_read_tokens": 50,
            "cache_write_input_tokens": 25,
            "output": 10,
            "output_reasoning_tokens": 4,
            "output_audio_tokens": 1,
            "total": 190,
        }
    )
    assert usage.input_tokens == 175
    assert usage.cache_read_tokens == 50
    assert usage.cache_write_tokens == 25
    assert usage.output_tokens == 15
    assert usage.reasoning_tokens == 4
    assert usage.cache_reported is True
    assert usage_from_details({"input": None, "output": "x"}).total_tokens == 0


def test_langfuse_legacy_usage_and_bad_record(tmp_path: Path) -> None:
    obs = {
        "id": "g",
        "traceId": "t",
        "type": "GENERATION",
        "name": "legacy",
        "model": "gpt-4o",
        "startTime": "2026-01-01T00:00:00Z",
        "usage": {"input": 3, "output": 2, "total": 5, "unit": "TOKENS", "totalCost": 0.01},
        "modelParameters": {"service_tier": "priority"},
    }
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({"data": [obs]}))
    gen = load(p).generations[0]
    assert gen.usage.input_tokens == 3
    assert gen.supplied_cost is not None
    assert gen.supplied_cost.source == "usage.totalCost"
    assert gen.service_tier == "priority"

    deprecated = dict(obs, usage=None, calculatedTotalCost=0.02, startTime="bad")
    p.write_text(json.dumps([deprecated]))
    gen = load(p).generations[0]
    assert gen.supplied_cost is not None
    assert gen.supplied_cost.source == "calculatedTotalCost"

    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps({"data": [obs], "traces": [{"nope": 1}]}))
    with pytest.raises(ValueError, match="unrecognized format"):
        load(mixed)


def test_root_index_breaks_parent_cycles(tmp_path: Path) -> None:
    a = {"traceId": TRACE, "spanId": "a", "parentSpanId": "b", "name": "a", "attributes": []}
    b = {"traceId": TRACE, "spanId": "b", "parentSpanId": "a", "name": "b", "attributes": []}
    g = {
        "traceId": TRACE,
        "spanId": "g",
        "parentSpanId": "a",
        "name": "chat",
        "attributes": [
            attr("gen_ai.request.model", "gpt-4o"),
            attr("gen_ai.usage.input_tokens", 1),
        ],
    }
    p = tmp_path / "cycle.json"
    p.write_text(json.dumps(export_request(a, b, g)))
    export = load(p)
    from tracecost.prices import PriceTable, price_export
    from tracecost.rollup import rollup

    result = rollup(export, price_export(export, PriceTable.load()))
    assert len(result.by("root")) == 1
