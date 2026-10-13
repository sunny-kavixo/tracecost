"""Turn rollups and diffs into a terminal table, JSON, and a list of warnings."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from tracecost import __version__
from tracecost.diff import Diff
from tracecost.model import Export, Usage
from tracecost.prices import PricedGeneration, PriceTable
from tracecost.rollup import Rollup, Row

KEY_WIDTH = 36


@dataclass
class Warnings:
    """Aggregated findings a reader should act on."""

    unknown_models: Counter[str] = field(default_factory=Counter)
    no_model: int = 0
    cache_unreported: Counter[str] = field(default_factory=Counter)
    supplied: Counter[str] = field(default_factory=Counter)
    supplied_total: float = 0.0
    recomputed_total: float = 0.0
    notes: Counter[str] = field(default_factory=Counter)

    def lines(self) -> list[str]:
        out: list[str] = []
        for model, n in self.unknown_models.most_common():
            verb = "has" if n == 1 else "have"
            out.append(
                f"{_plural(n, 'generation')} on model {model!r} {verb} no price "
                "(counted as $0). Add it with --prices."
            )
        if self.no_model:
            out.append(f"{_plural(self.no_model, 'generation')} without a model name; not priced.")
        for provider, n in self.cache_unreported.most_common():
            verb = "reports" if n == 1 else "report"
            out.append(
                f"{_plural(n, 'generation')} from {provider} {verb} no cache attributes "
                "although the model bills cached tokens; zero cache may mean 'not reported' "
                "(ollama#15758). Verify the exporter."
            )
        if self.supplied:
            sources = ", ".join(f"{s} ({n})" for s, n in self.supplied.most_common())
            n = sum(self.supplied.values())
            verb = "uses" if n == 1 else "use"
            out.append(
                f"{_plural(n, 'generation')} {verb} a supplied cost [{sources}] totalling "
                f"${self.supplied_total:,.4f}; the token table says ${self.recomputed_total:,.4f}. "
                "Use --recompute to price from tokens."
            )
        for note, n in self.notes.most_common():
            out.append(f"{note} (x{n})" if n > 1 else note)
        return out


def collect_warnings(priced: list[PricedGeneration], table: PriceTable) -> Warnings:
    w = Warnings()
    for pg in priced:
        cost, gen = pg.cost, pg.generation
        if cost.source == "unknown-model":
            w.unknown_models[gen.model or "?"] += 1
            continue
        if cost.source == "no-model":
            w.no_model += 1
            continue
        if cost.source.startswith("supplied:"):
            w.supplied[cost.source.removeprefix("supplied:")] += 1
            w.supplied_total += cost.amount
            if pg.recomputed is not None:
                w.recomputed_total += pg.recomputed.amount
        for note in cost.notes:
            w.notes[note] += 1
        if not gen.usage.cache_reported and gen.model:
            match = table.find(gen.model, gen.provider)
            if match is not None and _bills_cache(match.model):
                w.cache_unreported[match.provider_id] += 1
    return w


def _bills_cache(model: dict[str, Any]) -> bool:
    prices = model.get("prices")
    if isinstance(prices, list):
        prices = prices[-1].get("prices") if prices else {}
    return isinstance(prices, dict) and "cache_read_mtok" in prices


# --- formatting helpers ------------------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}"


def _money(value: float) -> str:
    return f"${value:,.4f}"


def _trunc(text: str, width: int = KEY_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _label(row: Row, dimension: str) -> str:
    if dimension == "root":
        trace, _, span = row.key.partition("/")
        return f"{row.name or span} …{trace[-6:]}" if trace else row.key
    if dimension == "trace":
        return f"{row.name} …{row.key[-6:]}" if row.name else row.key
    if dimension == "model" and row.name:
        return f"{row.key} ({row.name})"
    return row.key


def header(export: Export, table: PriceTable, rollup: Rollup) -> str:
    traces = rollup.total.traces
    return (
        f"tracecost {__version__} · {len(export.generations)} generations · {traces} traces · "
        f"prices {table.snapshot_date} ({table.source})"
    )


def cost_table(rollup: Rollup, dimension: str, top: int = 20) -> str:
    rows = rollup.by(dimension)
    cols = ("calls", "input", "cache-read", "output", "cost")
    lines = [
        f"{'by ' + dimension:<{KEY_WIDTH}} {cols[0]:>6} {cols[1]:>11} {cols[2]:>11} "
        f"{cols[3]:>9} {cols[4]:>11}"
    ]

    def fmt(label: str, r: Row) -> str:
        return (
            f"{_trunc(label):<{KEY_WIDTH}} {_n(r.llm_calls):>6} {_n(r.usage.input_tokens):>11} "
            f"{_n(r.usage.cache_read_tokens):>11} {_n(r.usage.output_tokens):>9} "
            f"{_money(r.cost):>11}"
        )

    for row in rows[:top]:
        lines.append(fmt(_label(row, dimension), row))
    if len(rows) > top:
        lines.append(f"{'… ' + _n(len(rows) - top) + ' more rows':<{KEY_WIDTH}}")
    lines.append(fmt("total", rollup.total))
    return "\n".join(lines)


def diff_table(result: Diff, top: int = 20) -> str:
    lines = [f"{'root span':<{KEY_WIDTH}} {'calls':>11} {'tokens':>23} {'cost':>23}  "]
    arrow = " → "

    def fmt(label: str, r: Any) -> str:
        marker = {
            "added": "+",
            "removed": "-",
            "changed": "▲" if r.cost_delta > 0 else "▼",
            "same": " ",
        }
        mark = marker.get(r.status, " ") if label != "total" else ""
        return (
            f"{_trunc(label):<{KEY_WIDTH}} {_n(r.calls_a) + arrow + _n(r.calls_b):>11} "
            f"{_n(r.tokens_a) + arrow + _n(r.tokens_b):>23} "
            f"{_money(r.cost_a) + arrow + _money(r.cost_b):>23}  {mark}"
        )

    for row in result.rows[:top]:
        lines.append(fmt(row.key, row))
    if len(result.rows) > top:
        lines.append(f"… {_n(len(result.rows) - top)} more rows")
    lines.append(fmt("total", result.total))
    if result.models:
        lines.append("")
        lines.append("models (calls)")
        for model, (a, b) in result.models.items():
            lines.append(f"  {_trunc(model, 40):<40} {_n(a):>6}{arrow}{_n(b)}")
    return "\n".join(lines)


# --- JSON --------------------------------------------------------------------------------------


def _usage_dict(usage: Usage) -> dict[str, int | bool]:
    return {
        "input": usage.input_tokens,
        "output": usage.output_tokens,
        "cache_read": usage.cache_read_tokens,
        "cache_write": usage.cache_write_tokens,
        "reasoning": usage.reasoning_tokens,
        "cache_reported": usage.cache_reported,
    }


def _row_dict(row: Row) -> dict[str, Any]:
    return {
        "key": row.key,
        "name": row.name,
        "cost": round(row.cost, 8),
        "llm_calls": row.llm_calls,
        "tool_calls": row.tool_calls,
        "traces": row.traces,
        "unpriced_calls": row.unpriced_calls,
        "supplied_calls": row.supplied_calls,
        "tokens": _usage_dict(row.usage),
    }


def cost_json(
    export: Export,
    table: PriceTable,
    rollup: Rollup,
    priced: list[PricedGeneration],
    dimension: str,
    warnings: Warnings,
) -> str:
    doc = {
        "tracecost": __version__,
        "prices": {"snapshot_date": table.snapshot_date, "source": table.source},
        "inputs": export.sources,
        "by": dimension,
        "total": _row_dict(rollup.total),
        "rows": [_row_dict(r) for r in rollup.by(dimension)],
        "warnings": warnings.lines(),
        "generations": [
            {
                "trace_id": pg.generation.trace_id,
                "span_id": pg.generation.span_id,
                "parent_id": pg.generation.parent_id,
                "name": pg.generation.name,
                "model": pg.generation.model,
                "provider": pg.generation.provider,
                "session_id": pg.generation.session_id,
                "user_id": pg.generation.user_id,
                "service_tier": pg.generation.service_tier,
                "tokens": _usage_dict(pg.generation.usage),
                "cost": round(pg.cost.amount, 8),
                "currency": pg.cost.currency,
                "cost_source": pg.cost.source,
                "price_model_id": pg.cost.model_id,
                "price_provider_id": pg.cost.provider_id,
                "breakdown": {k: round(v, 8) for k, v in pg.cost.breakdown.items()},
                "recomputed_cost": None
                if pg.recomputed is None
                else round(pg.recomputed.amount, 8),
                "notes": pg.cost.notes,
            }
            for pg in priced
        ],
    }
    return json.dumps(doc, indent=2)


def diff_json(result: Diff, table: PriceTable) -> str:
    doc = {
        "tracecost": __version__,
        "prices": {"snapshot_date": table.snapshot_date, "source": table.source},
        "total": {**asdict(result.total), "cost_delta": result.total.cost_delta},
        "rows": [
            {**asdict(r), "cost_delta": r.cost_delta, "status": r.status} for r in result.rows
        ],
        "models": {m: {"calls_a": a, "calls_b": b} for m, (a, b) in result.models.items()},
    }
    return json.dumps(doc, indent=2)
