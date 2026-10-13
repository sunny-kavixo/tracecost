"""Read Langfuse observation exports.

Input is what ``GET /api/public/observations`` (or ``/v2/observations``) returns, saved to a
file: ``{"data": [Observation, ...], "meta": {...}}``, or a bare JSON array of observations, or
JSON Lines. Trace records from ``GET /api/public/traces`` (``{"data": [Trace, ...]}``) can be
loaded alongside, or placed under a ``traces`` key, to supply ``sessionId`` and ``userId``.
Field names follow ``cloud.langfuse.com/generated/api/openapi.yml`` (checked 2026-10-13).

Langfuse stores usage in *exclusive* buckets (``langfuse.com/docs/observability/features/
token-and-cost-tracking``): ``input`` excludes ``input_cached_tokens`` and
``cache_read_input_tokens``; ``output`` excludes ``output_reasoning_tokens``. tracecost's
``Usage`` is inclusive, like OpenTelemetry, so this reader adds the detail buckets back.
``costDetails.total`` (or the deprecated ``calculatedTotalCost`` / ``usage.totalCost``) becomes a
supplied cost, because Langfuse computed it at ingest and the user may want to audit it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from tracecost.model import Export, Generation, Span, SuppliedCost, Usage
from tracecost.readers.otlp import parse_documents

KIND_BY_TYPE = {
    "GENERATION": "llm",
    "EMBEDDING": "embedding",
    "TOOL": "tool",
    "AGENT": "agent",
    "CHAIN": "chain",
    "RETRIEVER": "retriever",
    "SPAN": "other",
    "EVENT": "other",
    "EVALUATOR": "other",
    "GUARDRAIL": "other",
}

CACHE_READ_KEYS = ("input_cached_tokens", "cache_read_input_tokens", "input_cache_read_tokens")
CACHE_WRITE_KEYS = (
    "cache_creation_input_tokens",
    "input_cache_creation_tokens",
    "cache_write_input_tokens",
    "input_cache_write_tokens",
)
REASONING_KEYS = ("output_reasoning_tokens",)


def _is_observation(item: object) -> bool:
    return (
        isinstance(item, dict) and "traceId" in item and ("type" in item or "usageDetails" in item)
    )


def _is_trace(item: object) -> bool:
    return (
        isinstance(item, dict)
        and "id" in item
        and "traceId" not in item
        and ("sessionId" in item or "userId" in item)
    )


def _records(doc: object) -> list[Any]:
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        out: list[Any] = []
        data = doc.get("data")
        if isinstance(data, list):
            out.extend(data)
        traces = doc.get("traces")
        if isinstance(traces, list):
            out.extend(traces)
        if not out and (_is_observation(doc) or _is_trace(doc)):
            out.append(doc)
        return out
    return []


def looks_like_langfuse(doc: object) -> bool:
    records = _records(doc)
    return bool(records) and all(_is_observation(r) or _is_trace(r) for r in records)


def _iso_to_ns(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return None


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(float(str(value)))
    except ValueError:
        return 0


def usage_from_details(details: dict[str, Any]) -> Usage:
    """Convert Langfuse's exclusive buckets to inclusive OTel-style counts."""
    cache_read = sum(_int(details.get(k)) for k in CACHE_READ_KEYS)
    cache_write = sum(_int(details.get(k)) for k in CACHE_WRITE_KEYS)
    reasoning = sum(_int(details.get(k)) for k in REASONING_KEYS)
    input_total = _int(details.get("input")) + sum(
        _int(v) for k, v in details.items() if k.startswith("input_") or k in CACHE_READ_KEYS
    )
    input_total += sum(_int(details.get(k)) for k in CACHE_WRITE_KEYS if not k.startswith("input_"))
    output_total = _int(details.get("output")) + sum(
        _int(v) for k, v in details.items() if k.startswith("output_")
    )
    return Usage(
        input_tokens=input_total,
        output_tokens=output_total,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        cache_reported=any(k in details for k in CACHE_READ_KEYS + CACHE_WRITE_KEYS),
    )


def convert_observation(obs: dict[str, Any]) -> tuple[Span, Generation | None]:
    kind = KIND_BY_TYPE.get(str(obs.get("type") or "").upper(), "other")
    parent = obs.get("parentObservationId")
    raw_metadata = obs.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    span = Span(
        trace_id=str(obs.get("traceId") or ""),
        span_id=str(obs.get("id") or ""),
        parent_id=str(parent) if parent else None,
        name=str(obs.get("name") or ""),
        kind=kind,
        start_ns=_iso_to_ns(obs.get("startTime")),
        end_ns=_iso_to_ns(obs.get("endTime")),
    )
    details = obs.get("usageDetails")
    raw_usage = obs.get("usage")
    legacy: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    if kind not in ("llm", "embedding"):
        return span, None
    if isinstance(details, dict) and details:
        usage = usage_from_details(details)
    else:
        usage = Usage(
            input_tokens=_int(legacy.get("input")), output_tokens=_int(legacy.get("output"))
        )
    supplied = None
    costs = obs.get("costDetails")
    if isinstance(costs, dict) and costs.get("total") is not None:
        supplied = SuppliedCost(amount=float(costs["total"]), source="costDetails")
    elif obs.get("calculatedTotalCost") is not None:
        supplied = SuppliedCost(
            amount=float(obs["calculatedTotalCost"]), source="calculatedTotalCost"
        )
    elif legacy.get("totalCost") is not None:
        supplied = SuppliedCost(amount=float(legacy["totalCost"]), source="usage.totalCost")
    raw_params = obs.get("modelParameters")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    provider = metadata.get("ls_provider")  # written by the LangChain integration
    generation = Generation(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_id=span.parent_id,
        name=span.name,
        model=str(obs["model"]) if obs.get("model") else None,
        provider=str(provider) if provider else None,
        usage=usage,
        supplied_cost=supplied,
        service_tier=str(params["service_tier"]) if params.get("service_tier") else None,
        kind=kind,
        source="langfuse",
    )
    return span, generation


def read(path: Path) -> Export:
    export = Export(sources=[str(path)])
    for doc in parse_documents(path):
        for record in _records(doc):
            if _is_observation(record):
                span, generation = convert_observation(record)
                export.spans.append(span)
                if generation is not None:
                    export.generations.append(generation)
            elif _is_trace(record):
                export.trace_meta[str(record["id"])] = (
                    str(record["sessionId"]) if record.get("sessionId") else None,
                    str(record["userId"]) if record.get("userId") else None,
                )
            else:
                raise ValueError(f"{path}: record is neither a Langfuse observation nor a trace")
    return export
