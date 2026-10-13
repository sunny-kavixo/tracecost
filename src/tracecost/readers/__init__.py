"""Input formats. `load` sniffs each file and dispatches to the matching reader."""

from __future__ import annotations

from pathlib import Path

from tracecost.model import Export
from tracecost.readers import otlp


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
    raise ValueError(
        f"{path}: unrecognized format. Expected OTLP JSON/JSONL (resourceSpans), "
        "a Langfuse observations export, or Phoenix spans JSONL."
    )


READERS = {"otlp": otlp.read}


def load(*paths: str | Path) -> Export:
    """Read one or more export files of any supported format into one Export."""
    export = Export()
    for raw_path in paths:
        path = Path(raw_path)
        fmt = sniff(path)
        export.extend(READERS[fmt](path))
    return export
