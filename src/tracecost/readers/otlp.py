"""Read OTLP/JSON trace exports.

Accepts the JSON encoding of ``ExportTraceServiceRequest`` as written by the OpenTelemetry SDK
and Collector file exporters: one document (``{"resourceSpans": [...]}``), a JSON array of such
documents, or JSON Lines with one document per line. Both the canonical lowerCamelCase field
names and snake_case are accepted, because exporters disagree.

Attribute dialects (OTel GenAI, OpenInference) are tables in :mod:`tracecost.readers.dialects`.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from tracecost.model import Export, Generation, Span, SuppliedCost, Usage
from tracecost.readers import dialects as d


def looks_like_otlp(doc: object) -> bool:
    if isinstance(doc, list):
        return bool(doc) and all(looks_like_otlp(item) for item in doc)
    return isinstance(doc, dict) and ("resourceSpans" in doc or "resource_spans" in doc)


def parse_documents(path: Path) -> list[Any]:
    """Return the JSON documents in *path*: one, an array of them, or one per line."""
    with path.open("rb") as raw:
        compressed = raw.read(2) == b"\x1f\x8b"
    if path.suffix.lower() == ".gz" or compressed:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    try:
        doc = json.loads(stripped)
    except json.JSONDecodeError:
        docs: list[Any] = []
        for lineno, line in enumerate(stripped.splitlines(), 1):
            if not line.strip():
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: line {lineno} is not JSON ({exc.msg})") from exc
        return docs
    return doc if isinstance(doc, list) else [doc]


def _get(obj: dict[str, Any], camel: str, snake: str) -> Any:
    value = obj.get(camel)
    return obj.get(snake) if value is None else value


def attribute_value(value: dict[str, Any]) -> object:
    """Decode one OTLP ``AnyValue``."""
    if "stringValue" in value or "string_value" in value:
        return _get(value, "stringValue", "string_value")
    if "intValue" in value or "int_value" in value:
        return int(_get(value, "intValue", "int_value"))
    if "doubleValue" in value or "double_value" in value:
        return float(_get(value, "doubleValue", "double_value"))
    if "boolValue" in value or "bool_value" in value:
        return bool(_get(value, "boolValue", "bool_value"))
    if "arrayValue" in value or "array_value" in value:
        inner = _get(value, "arrayValue", "array_value") or {}
        return [attribute_value(v) for v in inner.get("values", [])]
    if "kvlistValue" in value or "kvlist_value" in value:
        inner = _get(value, "kvlistValue", "kvlist_value") or {}
        return attributes_to_dict(inner.get("values", []))
    if "bytesValue" in value or "bytes_value" in value:
        return _get(value, "bytesValue", "bytes_value")
    return None


def attributes_to_dict(attributes: Iterable[dict[str, Any]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for item in attributes:
        key = item.get("key")
        if isinstance(key, str):
            out[key] = attribute_value(item.get("value") or {})
    return out


def iter_spans(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for resource_spans in _get(doc, "resourceSpans", "resource_spans") or []:
        for scope_spans in _get(resource_spans, "scopeSpans", "scope_spans") or []:
            yield from scope_spans.get("spans") or []


def _ns(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def convert_span(raw: dict[str, Any]) -> tuple[Span, Generation | None]:
    attrs = attributes_to_dict(raw.get("attributes") or [])
    trace_id = str(_get(raw, "traceId", "trace_id") or "")
    span_id = str(_get(raw, "spanId", "span_id") or "")
    parent_raw = _get(raw, "parentSpanId", "parent_span_id")
    parent_id = str(parent_raw) if parent_raw else None
    name = str(raw.get("name") or "")
    dialect = d.detect(attrs)
    kind = d.normalized_kind(dialect, attrs) if dialect else "other"
    session_id = d.first_str(attrs, ("gen_ai.conversation.id", "session.id"))
    user_id = d.first_str(attrs, ("user.id", "enduser.id"))
    span = Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        name=name,
        kind=kind,
        start_ns=_ns(_get(raw, "startTimeUnixNano", "start_time_unix_nano")),
        end_ns=_ns(_get(raw, "endTimeUnixNano", "end_time_unix_nano")),
        session_id=session_id,
        user_id=user_id,
    )
    if dialect is None:
        return span, None
    generation = generation_from_attributes(span, dialect, attrs, source="otlp")
    return span, generation


def generation_from_attributes(
    span: Span, dialect: d.Dialect, attrs: d.Attributes, *, source: str
) -> Generation | None:
    """Build a Generation if the span carries a model or token usage; otherwise None."""
    model = d.first_str(attrs, dialect.model)
    input_tokens = d.first_int(attrs, dialect.input_tokens)
    output_tokens = d.first_int(attrs, dialect.output_tokens)
    cache_read = d.first_int(attrs, dialect.cache_read)
    cache_write = d.first_int(attrs, dialect.cache_write)
    reasoning = d.first_int(attrs, dialect.reasoning)
    has_usage = any(v is not None for v in (input_tokens, output_tokens, cache_read, cache_write))
    kind = span.kind if span.kind in d.GENERATION_KINDS else "llm"
    if not has_usage and model is None:
        return None
    if span.kind not in d.GENERATION_KINDS and span.kind != "other":
        # A tool/agent/chain span that happens to mention a model is not a billable call.
        return None
    usage = Usage(
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cache_read_tokens=cache_read or 0,
        cache_write_tokens=cache_write or 0,
        reasoning_tokens=reasoning or 0,
        cache_reported=cache_read is not None or cache_write is not None,
    )
    supplied = None
    amount = d.first_float(attrs, dialect.cost_total)
    if amount is not None:
        supplied = SuppliedCost(
            amount=amount,
            currency=d.first_str(attrs, dialect.cost_currency) or "USD",
            source=d.first_str(attrs, dialect.cost_source) or dialect.cost_total[0],
        )
    return Generation(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_id=span.parent_id,
        name=span.name,
        model=model,
        provider=d.first_str(attrs, dialect.provider),
        usage=usage,
        supplied_cost=supplied,
        session_id=span.session_id,
        user_id=span.user_id,
        service_tier=d.first_str(attrs, dialect.service_tier),
        kind=kind,
        source=source,
    )


def read(path: Path) -> Export:
    docs = parse_documents(path)
    export = Export(sources=[str(path)])
    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError(f"{path}: expected OTLP JSON objects, found {type(doc).__name__}")
        for raw in iter_spans(doc):
            span, generation = convert_span(raw)
            export.spans.append(span)
            if generation is not None:
                export.generations.append(generation)
    return export
