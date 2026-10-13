"""Helpers shared by the example scripts: build small OTLP/JSON and Langfuse exports.

The examples are the acceptance scenarios from the issues tracecost was built for. Each one
writes its export to a temporary directory, runs the library the way the CLI does, prints the
result and asserts the property the issue asked for. They need no network and no API keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tracecost import Export, load
from tracecost.prices import PricedGeneration, PriceTable, price_export
from tracecost.report import collect_warnings, cost_table, header
from tracecost.rollup import Rollup, rollup

NS = 1_000_000_000
T0 = 1_791_795_600 * NS  # 2026-10-12T09:00:00Z


def attr(key: str, value: object) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def span(
    trace_id: str,
    span_id: str,
    name: str,
    attributes: dict[str, object],
    *,
    parent: str | None = None,
    start: int = T0,
    duration_s: float = 1.0,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 3,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + int(duration_s * NS)),
        "attributes": [attr(k, v) for k, v in attributes.items() if v is not None],
    }
    if parent:
        doc["parentSpanId"] = parent
    return doc


def chat(
    trace_id: str,
    span_id: str,
    model: str,
    provider: str,
    *,
    parent: str | None = None,
    input_tokens: int,
    output_tokens: int,
    cache_read: int | None = None,
    cache_write: int | None = None,
    reasoning: int | None = None,
    session: str | None = None,
    user: str | None = None,
    start: int = T0,
    extra: dict[str, object] | None = None,
) -> dict[str, Any]:
    attributes: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.cache_read.input_tokens": cache_read,
        "gen_ai.usage.cache_write.input_tokens": cache_write,
        "gen_ai.usage.reasoning.output_tokens": reasoning,
        "gen_ai.conversation.id": session,
        "user.id": user,
    }
    if extra:
        attributes.update(extra)
    return span(trace_id, span_id, f"chat {model}", attributes, parent=parent, start=start)


def agent(
    trace_id: str,
    span_id: str,
    name: str,
    *,
    session: str | None = None,
    user: str | None = None,
    start: int = T0,
    duration_s: float = 5.0,
) -> dict[str, Any]:
    return span(
        trace_id,
        span_id,
        name,
        {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": name,
            "gen_ai.conversation.id": session,
            "user.id": user,
        },
        start=start,
        duration_s=duration_s,
    )


def tool(trace_id: str, span_id: str, name: str, *, parent: str, start: int = T0) -> dict[str, Any]:
    return span(
        trace_id,
        span_id,
        f"execute_tool {name}",
        {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name},
        parent=parent,
        start=start,
        duration_s=0.3,
    )


def otlp_request(spans: list[dict[str, Any]], service: str = "demo-agent") -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [attr("service.name", service)]},
                "scopeSpans": [{"scope": {"name": "examples"}, "spans": spans}],
            }
        ]
    }


def write_otlp(path: Path, spans: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(otlp_request(spans), indent=1))
    return path


def write_jsonl(path: Path, requests: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in requests) + "\n")
    return path


def hex_id(n: int, width: int = 16) -> str:
    return f"{n:0{width}x}"


def price_and_rollup(
    export: Export, table: PriceTable | None = None, **kwargs: Any
) -> tuple[PriceTable, list[PricedGeneration], Rollup]:
    table = table or PriceTable.load()
    priced = price_export(export, table, **kwargs)
    return table, priced, rollup(export, priced)


def show(
    export: Export, dimension: str = "root", table: PriceTable | None = None, **kw: Any
) -> Rollup:
    table, priced, result = price_and_rollup(export, table, **kw)
    print(header(export, table, result))
    print()
    print(cost_table(result, dimension))
    for line in collect_warnings(priced, table).lines():
        print(f"⚠ {line}")
    print()
    return result


__all__ = [
    "T0",
    "agent",
    "attr",
    "chat",
    "hex_id",
    "load",
    "otlp_request",
    "price_and_rollup",
    "show",
    "span",
    "tool",
    "write_jsonl",
    "write_otlp",
]
