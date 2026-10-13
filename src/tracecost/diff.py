"""Compare two rollups by root-span name (phoenix discussion 14736, "whatbroke")."""

from __future__ import annotations

from dataclasses import dataclass, field

from tracecost.rollup import Rollup, Row


@dataclass
class DiffRow:
    key: str
    cost_a: float = 0.0
    cost_b: float = 0.0
    calls_a: int = 0
    calls_b: int = 0
    tool_calls_a: int = 0
    tool_calls_b: int = 0
    tokens_a: int = 0
    tokens_b: int = 0
    present_a: bool = False
    present_b: bool = False

    @property
    def cost_delta(self) -> float:
        return self.cost_b - self.cost_a

    @property
    def status(self) -> str:
        if self.present_a and not self.present_b:
            return "removed"
        if self.present_b and not self.present_a:
            return "added"
        same = (
            abs(self.cost_delta) < 1e-9
            and self.calls_a == self.calls_b
            and self.tool_calls_a == self.tool_calls_b
            and self.tokens_a == self.tokens_b
        )
        return "same" if same else "changed"


@dataclass
class Diff:
    total: DiffRow
    rows: list[DiffRow]
    models: dict[str, tuple[int, int]] = field(default_factory=dict)


def _by_root_name(rollup: Rollup) -> dict[str, Row]:
    """Aggregate root rows that share a name; ids differ between runs, names don't."""
    merged: dict[str, Row] = {}
    for row in rollup.by("root"):
        name = row.name or row.key
        target = merged.setdefault(name, Row(key=name, name=name))
        target.cost += row.cost
        target.usage = target.usage + row.usage
        target.llm_calls += row.llm_calls
        target.tool_calls += row.tool_calls
        target.unpriced_calls += row.unpriced_calls
        target.supplied_calls += row.supplied_calls
        target.trace_ids |= row.trace_ids
    return merged


def _fill(row: DiffRow, src: Row | None, side: str) -> None:
    if src is None:
        return
    setattr(row, f"cost_{side}", src.cost)
    setattr(row, f"calls_{side}", src.llm_calls)
    setattr(row, f"tool_calls_{side}", src.tool_calls)
    setattr(row, f"tokens_{side}", src.usage.total_tokens)
    setattr(row, f"present_{side}", True)


def diff(a: Rollup, b: Rollup) -> Diff:
    roots_a, roots_b = _by_root_name(a), _by_root_name(b)
    rows: list[DiffRow] = []
    for key in sorted(set(roots_a) | set(roots_b)):
        row = DiffRow(key=key)
        _fill(row, roots_a.get(key), "a")
        _fill(row, roots_b.get(key), "b")
        rows.append(row)
    rows.sort(key=lambda r: (-abs(r.cost_delta), r.key))

    total = DiffRow(key="total")
    _fill(total, a.total, "a")
    _fill(total, b.total, "b")
    total.tokens_a = a.total.usage.total_tokens if a.total.llm_calls else 0
    total.tokens_b = b.total.usage.total_tokens if b.total.llm_calls else 0

    models_a = {r.key: r.llm_calls for r in a.by("model")}
    models_b = {r.key: r.llm_calls for r in b.by("model")}
    models = {
        m: (models_a.get(m, 0), models_b.get(m, 0)) for m in sorted(set(models_a) | set(models_b))
    }
    return Diff(total=total, rows=rows, models=models)


__all__ = ["Diff", "DiffRow", "diff"]
