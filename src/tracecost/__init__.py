"""tracecost: recompute what your LLM traces cost, offline, from exported spans."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from tracecost.model import Export, Generation, Span, SuppliedCost, Usage
from tracecost.readers import load

try:
    __version__ = version("tracecost")
except PackageNotFoundError:  # pragma: no cover - editable checkout without metadata
    __version__ = "0.0.0"

__all__ = [
    "Export",
    "Generation",
    "Span",
    "SuppliedCost",
    "Usage",
    "__version__",
    "load",
]
