"""Input formats. `load` sniffs each file and dispatches to the matching reader."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tracecost.model import Export
from tracecost.readers import langfuse, otlp, phoenix

READERS = {"otlp": otlp.read, "phoenix": phoenix.read, "langfuse": langfuse.read}


def sniff(path: Path) -> str:
    """Return the format name for *path* or raise ValueError naming the file."""
    try:
        docs = otlp.parse_documents(path)
    except OSError as exc:
        raise ValueError(f"{path}: {exc.strerror or exc}") from exc
    if not docs:
        raise ValueError(f"{path}: file is empty")
    if any(otlp.looks_like_otlp(doc) for doc in docs):
        return "otlp"
    if all(phoenix.looks_like_phoenix(doc) for doc in docs):
        return "phoenix"
    if all(langfuse.looks_like_langfuse(doc) for doc in docs):
        return "langfuse"
    raise ValueError(
        f"{path}: unrecognized format. Expected OTLP JSON/JSONL (resourceSpans), "
        "Phoenix spans JSONL (context.span_id) or a Langfuse observations export (traceId)."
    )


def apply_trace_meta(export: Export) -> None:
    """Fill session/user ids from Langfuse trace records onto spans and generations."""
    if not export.trace_meta:
        return
    for i, span in enumerate(export.spans):
        meta = export.trace_meta.get(span.trace_id)
        if meta and (span.session_id is None or span.user_id is None):
            export.spans[i] = replace(
                span,
                session_id=span.session_id or meta[0],
                user_id=span.user_id or meta[1],
            )
    for i, gen in enumerate(export.generations):
        meta = export.trace_meta.get(gen.trace_id)
        if meta and (gen.session_id is None or gen.user_id is None):
            export.generations[i] = replace(
                gen,
                session_id=gen.session_id or meta[0],
                user_id=gen.user_id or meta[1],
            )


def load(*paths: str | Path) -> Export:
    """Read one or more export files of any supported format into one Export."""
    export = Export()
    for raw_path in paths:
        path = Path(raw_path)
        export.extend(READERS[sniff(path)](path))
    apply_trace_meta(export)
    return export
