"""Price generations from a dated price table.

The bundled table (``data/prices.json``) is a trimmed snapshot of pydantic/genai-prices (MIT),
regenerated with ``scripts/update_prices.py``. Semantics follow that project's
``packages/python/genai_prices/types.py``:

- rates are USD per million tokens (``*_mtok``);
- a tiered rate is a threshold "cliff": the highest tier whose ``start`` is below the
  generation's *total* input tokens applies to all tokens of that type;
- ``prices`` may be a list of conditional prices; the last one whose constraint holds at call
  time wins (``start_date`` for price changes, ``start_time``/``end_time`` for off-peak windows);
- ``price_variants`` overlay rates for a ``service_tier`` (OpenAI priority/flex/fast).

tracecost adds: cache tokens fall back to the input rate (with a note) when a model lists no
cache rate, reasoning tokens bill at ``output_reasoning_mtok`` or else the output rate, and an
unknown model is a flagged $0 line, never a silent one.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any

from tracecost.model import Export, Generation

Rates = dict[str, Any]
MILLION = 1_000_000


@dataclass
class Cost:
    """The priced result for one generation."""

    amount: float
    currency: str = "USD"
    source: str = "table"  # table | supplied:<origin> | unknown-model | no-model
    model_id: str | None = None
    provider_id: str | None = None
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PricedGeneration:
    generation: Generation
    cost: Cost
    recomputed: Cost | None = None  # table price when a supplied cost was used


# --- match clauses -----------------------------------------------------------------------------


def clause_matches(clause: object, value: str) -> bool:
    """Evaluate a genai-prices match clause (case-insensitive)."""
    if not isinstance(clause, dict):
        return False
    v = value.lower()
    if "equals" in clause:
        return v == str(clause["equals"]).lower()
    if "starts_with" in clause:
        return v.startswith(str(clause["starts_with"]).lower())
    if "ends_with" in clause:
        return v.endswith(str(clause["ends_with"]).lower())
    if "contains" in clause:
        return str(clause["contains"]).lower() in v
    if "regex" in clause:
        return re.search(str(clause["regex"]), value, re.IGNORECASE) is not None
    if "or" in clause:
        return any(clause_matches(c, value) for c in clause["or"])
    if "and" in clause:
        return all(clause_matches(c, value) for c in clause["and"])
    return False


# --- table -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    provider_id: str
    model: dict[str, Any]

    @property
    def model_id(self) -> str:
        return str(self.model["id"])


@dataclass(frozen=True)
class PriceTable:
    providers: tuple[dict[str, Any], ...]
    snapshot_date: str = "unknown"
    source: str = "bundled"

    @classmethod
    def load(cls, path: str | Path | None = None) -> PriceTable:
        """Load the bundled snapshot, or a table file of the same shape."""
        if path is None:
            text = resources.files("tracecost").joinpath("data/prices.json").read_text("utf-8")
            source = "bundled"
        else:
            text = Path(path).read_text("utf-8")
            source = str(path)
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}: not a JSON price table ({exc.msg})") from exc
        providers = doc.get("providers") if isinstance(doc, dict) else None
        if not isinstance(providers, list):
            raise ValueError(f"{source}: price table needs a top-level 'providers' list")
        return cls(
            providers=tuple(providers),
            snapshot_date=str(doc.get("snapshot_date", "unknown")),
            source=source,
        )

    def with_overrides(self, path: str | Path) -> PriceTable:
        """Return a table where models in *path* win over the snapshot's."""
        override = PriceTable.load(path)
        by_id = {str(p["id"]): dict(p) for p in self.providers}
        for p in override.providers:
            pid = str(p["id"])
            if pid in by_id:
                existing = by_id[pid]
                new_ids = {str(m["id"]) for m in p.get("models", [])}
                kept = [m for m in existing.get("models", []) if str(m["id"]) not in new_ids]
                merged = {**existing, **{k: v for k, v in p.items() if k != "models"}}
                merged["models"] = list(p.get("models", [])) + kept
                by_id[pid] = merged
            else:
                by_id = {pid: dict(p), **by_id}  # new providers are consulted first
        return replace(
            self, providers=tuple(by_id.values()), source=f"{self.source}+{Path(path).name}"
        )

    @property
    def model_count(self) -> int:
        return sum(len(p.get("models", [])) for p in self.providers)

    def find(self, model: str, provider: str | None = None) -> Match | None:
        hinted: list[dict[str, Any]] = []
        likely: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for p in self.providers:
            if provider and (
                str(p.get("id", "")).lower() == provider.lower()
                or clause_matches(p.get("provider_match"), provider)
            ):
                hinted.append(p)
            elif clause_matches(p.get("model_match"), model):
                likely.append(p)
            else:
                rest.append(p)
        for p in hinted + likely + rest:
            for m in p.get("models", []):
                if clause_matches(m.get("match"), model):
                    return Match(provider_id=str(p["id"]), model=m)
        return None


# --- resolving rates ---------------------------------------------------------------------------


def _constraint_holds(constraint: object, at: dt.datetime) -> bool:
    if not isinstance(constraint, dict):
        return True
    if "start_date" in constraint:
        return at.date() >= dt.date.fromisoformat(str(constraint["start_date"]))
    if "start_time" in constraint and "end_time" in constraint:
        start = dt.time.fromisoformat(str(constraint["start_time"]))
        end = dt.time.fromisoformat(str(constraint["end_time"]))
        now = at.timetz().replace(tzinfo=None)
        return start <= now < end if start <= end else now >= start or now < end
    return True


def resolve_rates(
    model: dict[str, Any], at: dt.datetime, service_tier: str | None
) -> tuple[Rates, list[str]]:
    notes: list[str] = []
    prices = model.get("prices")
    rates: Rates = {}
    if isinstance(prices, list):
        for entry in prices:
            if _constraint_holds(entry.get("constraint"), at):
                rates = dict(entry.get("prices") or {})
        if not rates and prices:
            rates = dict(prices[0].get("prices") or {})
    elif isinstance(prices, dict):
        rates = dict(prices)
    variants = model.get("price_variants") or []
    if service_tier:
        hit = None
        for variant in variants:
            when = variant.get("when") or {}
            if str(
                when.get("service_tier", "")
            ).lower() == service_tier.lower() and _constraint_holds(variant.get("constraint"), at):
                hit = variant
        if hit is not None:
            rates.update(hit.get("prices") or {})
        elif service_tier.lower() != "default":
            known = {str((v.get("when") or {}).get("service_tier", "")).lower() for v in variants}
            if service_tier.lower() in known:
                notes.append(
                    f"service tier '{service_tier}' for {model['id']} is not in force on "
                    f"{at.date().isoformat()}; default rates used"
                )
            else:
                notes.append(
                    f"service tier '{service_tier}' not in the price table for {model['id']}; "
                    "default rates used"
                )
    return rates, notes


def rate(rates: Rates, key: str, total_input_tokens: int) -> float | None:
    value = rates.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        applicable = float(value["base"])
        for tier in sorted(value.get("tiers", []), key=lambda t: int(t["start"])):
            if total_input_tokens > int(tier["start"]):
                applicable = float(tier["price"])
        return applicable
    return float(value)


# --- pricing -----------------------------------------------------------------------------------


def price_from_table(
    generation: Generation, table: PriceTable, at: dt.datetime | None = None
) -> Cost:
    """Price *generation* from the table regardless of any supplied cost."""
    when = at or dt.datetime.now(dt.UTC)
    if not generation.model:
        return Cost(0.0, source="no-model", notes=["generation has no model name; cannot price"])
    match = table.find(generation.model, generation.provider)
    if match is None:
        who = generation.provider or "unknown provider"
        return Cost(
            0.0,
            source="unknown-model",
            notes=[
                f"no price for model '{generation.model}' ({who}); "
                "counted as $0, add it with --prices"
            ],
        )
    rates, notes = resolve_rates(match.model, when, generation.service_tier)
    usage = generation.usage
    total_input = usage.input_tokens
    input_rate = rate(rates, "input_mtok", total_input)
    output_rate = rate(rates, "output_mtok", total_input)
    if input_rate is None and output_rate is None:
        notes.append(f"{match.model_id} has no per-token prices in the table; counted as $0")
    breakdown: dict[str, float] = {}

    def charge(name: str, tokens: int, primary: str, fallback: float | None) -> None:
        r = rate(rates, primary, total_input)
        if r is None and tokens:
            if fallback is None:
                notes.append(
                    f"{match.model_id}: no {primary} rate and no input rate; "
                    f"{name} tokens counted as $0"
                )
                r = 0.0
            else:
                notes.append(
                    f"{match.model_id}: no {primary} rate; {name} tokens billed at the input rate"
                )
                r = fallback
        breakdown[name] = (tokens * (r or 0.0)) / MILLION

    breakdown["input"] = usage.uncached_input_tokens * (input_rate or 0.0) / MILLION
    charge("cache_read", usage.cache_read_tokens, "cache_read_mtok", input_rate)
    charge("cache_write", usage.cache_write_tokens, "cache_write_mtok", input_rate)
    breakdown["output"] = usage.plain_output_tokens * (output_rate or 0.0) / MILLION
    reasoning_rate = rate(rates, "output_reasoning_mtok", total_input)
    breakdown["reasoning"] = (
        usage.reasoning_tokens
        * (reasoning_rate if reasoning_rate is not None else (output_rate or 0.0))
        / MILLION
    )
    return Cost(
        amount=sum(breakdown.values()),
        source="table",
        model_id=match.model_id,
        provider_id=match.provider_id,
        breakdown=breakdown,
        notes=notes,
    )


def price_generation(
    generation: Generation,
    table: PriceTable,
    *,
    at: dt.datetime | None = None,
    recompute: bool = False,
) -> Cost:
    """Supplied cost wins unless *recompute*; the table is the fallback and the audit."""
    supplied = generation.supplied_cost
    if supplied is not None and not recompute:
        match = table.find(generation.model, generation.provider) if generation.model else None
        notes = (
            []
            if supplied.currency.upper() == "USD"
            else [f"supplied cost is in {supplied.currency}; not converted"]
        )
        return Cost(
            amount=supplied.amount,
            currency=supplied.currency,
            source=f"supplied:{supplied.source}",
            model_id=match.model_id if match else None,
            provider_id=match.provider_id if match else None,
            notes=notes,
        )
    return price_from_table(generation, table, at)


def price_export(
    export: Export,
    table: PriceTable,
    *,
    recompute: bool = False,
    at: dt.datetime | None = None,
) -> list[PricedGeneration]:
    """Price every generation, using each span's start time for dated prices when known."""
    starts = {s.span_id: s.start_ns for s in export.spans if s.start_ns is not None}
    priced: list[PricedGeneration] = []
    for gen in export.generations:
        when = at
        if when is None and gen.span_id in starts:
            when = dt.datetime.fromtimestamp(starts[gen.span_id] / 1e9, tz=dt.UTC)
        cost = price_generation(gen, table, at=when, recompute=recompute)
        recomputed = price_from_table(gen, table, when) if gen.supplied_cost is not None else None
        priced.append(PricedGeneration(gen, cost, recomputed))
    return priced
