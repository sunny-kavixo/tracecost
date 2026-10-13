"""Roll priced generations up to root span, trace, session, user and model.

Only generations carry usage, so a root span's cost is the sum over every generation below
it, however deep (langfuse#16077). Totals per session, user and model count every row with no
top-N cutoff (langfuse#18210, #15109). A generation whose parent is missing from the export is
treated as its own root rather than dropped.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from tracecost.model import Export, Span, Usage
from tracecost.prices import PricedGeneration

DIMENSIONS = ("root", "trace", "session", "user", "model")
NONE_KEY = "(none)"


@dataclass
class Row:
    key: str
    name: str = ""
    cost: float = 0.0
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    tool_calls: int = 0
    unpriced_calls: int = 0
    supplied_calls: int = 0
    trace_ids: set[str] = field(default_factory=set)

    @property
    def traces(self) -> int:
        return len(self.trace_ids)

    def add(self, priced: PricedGeneration) -> None:
        self.cost += priced.cost.amount
        self.usage = self.usage + priced.generation.usage
        self.llm_calls += 1
        self.trace_ids.add(priced.generation.trace_id)
        if priced.cost.source in ("unknown-model", "no-model"):
            self.unpriced_calls += 1
        if priced.cost.source.startswith("supplied"):
            self.supplied_calls += 1


@dataclass
class Rollup:
    total: Row
    rows: dict[str, list[Row]]

    def by(self, dimension: str) -> list[Row]:
        if dimension not in self.rows:
            raise ValueError(
                f"unknown dimension {dimension!r}; expected one of {', '.join(DIMENSIONS)}"
            )
        return self.rows[dimension]


def _root_index(spans: list[Span]) -> dict[tuple[str, str], Span]:
    """Map every (trace_id, span_id) to its root span within the export."""
    by_id = {(s.trace_id, s.span_id): s for s in spans}
    roots: dict[tuple[str, str], Span] = {}
    for span in spans:
        node = span
        seen = {(node.trace_id, node.span_id)}
        while node.parent_id is not None:
            parent = by_id.get((node.trace_id, node.parent_id))
            if parent is None or (parent.trace_id, parent.span_id) in seen:
                break
            seen.add((parent.trace_id, parent.span_id))
            node = parent
        roots[(span.trace_id, span.span_id)] = node
    return roots


def _sorted(rows: dict[str, Row]) -> list[Row]:
    return sorted(rows.values(), key=lambda r: (-r.cost, r.key))


def rollup(export: Export, priced: list[PricedGeneration]) -> Rollup:
    roots = _root_index(export.spans)
    tool_counts: dict[tuple[str, str], int] = defaultdict(int)
    for span in export.spans:
        if span.kind == "tool":
            root = roots[(span.trace_id, span.span_id)]
            tool_counts[(root.trace_id, root.span_id)] += 1
    # the earliest root per trace names the trace
    trace_root: dict[str, Span] = {}
    for span in export.spans:
        root = roots[(span.trace_id, span.span_id)]
        current = trace_root.get(span.trace_id)
        if current is None or (root.start_ns or 0) < (current.start_ns or 0):
            trace_root[span.trace_id] = root

    tables: dict[str, dict[str, Row]] = {dim: {} for dim in DIMENSIONS}
    total = Row(key="total")
    for pg in priced:
        gen = pg.generation
        gen_root = roots.get((gen.trace_id, gen.span_id))
        root_key = f"{gen.trace_id}/{gen_root.span_id if gen_root else gen.span_id}"
        root_name = gen_root.name if gen_root else gen.name
        session = gen.session_id or (gen_root.session_id if gen_root else None) or NONE_KEY
        user = gen.user_id or (gen_root.user_id if gen_root else None) or NONE_KEY
        trace_name = trace_root[gen.trace_id].name if gen.trace_id in trace_root else root_name
        keys = {
            "root": (root_key, root_name),
            "trace": (gen.trace_id, trace_name),
            "session": (session, ""),
            "user": (user, ""),
            "model": (gen.model or "(unknown model)", gen.provider or ""),
        }
        for dim, (key, name) in keys.items():
            row = tables[dim].setdefault(key, Row(key=key, name=name))
            row.add(pg)
        total.add(pg)
    for (trace_id, span_id), count in tool_counts.items():
        key = f"{trace_id}/{span_id}"
        if key in tables["root"]:
            tables["root"][key].tool_calls += count
        if trace_id in tables["trace"]:
            tables["trace"][trace_id].tool_calls += count
        total.tool_calls += count
    return Rollup(total=total, rows={dim: _sorted(rows) for dim, rows in tables.items()})
