"""Read Phoenix span exports.

Phoenix's client returns spans as a flattened DataFrame
(``Client.spans.get_spans_dataframe()``; see ``phoenix/trace/trace_dataset.py`` and
``phoenix/client/helpers/spans``): one row per span with ``context.span_id``,
``context.trace_id``, ``parent_id``, ``name``, ``span_kind``, ``start_time``, ``end_time``,
``status_code`` and every attribute under an ``attributes.`` prefix. Write it out with
``df.to_json("spans.jsonl", orient="records", lines=True)`` and point tracecost at the file.
A JSON array of the same records, or records with a nested ``attributes`` object, also work.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from tracecost.model import Export, Span
from tracecost.readers import dialects as d
from tracecost.readers.otlp import generation_from_attributes, parse_documents


def looks_like_phoenix(doc: object) -> bool:
    if isinstance(doc, list):
        return bool(doc) and all(looks_like_phoenix(item) for item in doc)
    return isinstance(doc, dict) and "context.span_id" in doc


def _iso_to_ns(value: object) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        # pandas writes epoch milliseconds by default
        return int(value * 1_000_000)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)
        except ValueError:
            return None
    return None


def row_attributes(row: dict[str, Any]) -> dict[str, object]:
    attrs: dict[str, object] = {}
    nested = row.get("attributes")
    if isinstance(nested, dict):
        attrs.update(_flatten(nested))
    for key, value in row.items():
        if key.startswith("attributes.") and value is not None:
            attrs[key[len("attributes.") :]] = value
    return attrs


def _flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in obj.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full + "."))
        elif value is not None:
            out[full] = value
    return out


def convert_row(row: dict[str, Any]) -> tuple[Span, Any]:
    attrs = row_attributes(row)
    if "openinference.span.kind" not in attrs and row.get("span_kind"):
        attrs["openinference.span.kind"] = row["span_kind"]
    dialect = d.detect(attrs) or d.OPENINFERENCE
    parent = row.get("parent_id")
    span = Span(
        trace_id=str(row.get("context.trace_id") or ""),
        span_id=str(row.get("context.span_id") or ""),
        parent_id=str(parent) if parent else None,
        name=str(row.get("name") or ""),
        kind=d.normalized_kind(dialect, attrs),
        start_ns=_iso_to_ns(row.get("start_time")),
        end_ns=_iso_to_ns(row.get("end_time")),
        session_id=d.first_str(attrs, ("session.id", "gen_ai.conversation.id")),
        user_id=d.first_str(attrs, ("user.id", "enduser.id")),
    )
    return span, generation_from_attributes(span, dialect, attrs, source="phoenix")


def read(path: Path) -> Export:
    export = Export(sources=[str(path)])
    for doc in parse_documents(path):
        rows = doc if isinstance(doc, list) else [doc]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{path}: expected span records, found {type(row).__name__}")
            span, generation = convert_row(row)
            export.spans.append(span)
            if generation is not None:
                export.generations.append(generation)
    return export
