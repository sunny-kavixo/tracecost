"""F5: a cost the instrumentation already computed wins over the table, and is labelled.

phoenix#13655: "Phoenix re-derives cost instead of trusting the value we already have." A span
can carry a cost from GPU time, an external OCR service or a user-maintained table; tracecost
reports that number with its source, and `--recompute` shows what the token table would have
said instead, so the two can be audited against each other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_f1_otlp import TRACE, attr, export_request
from tests.test_f3_pricing import SMALL_TABLE
from tracecost import Generation, SuppliedCost, Usage, load
from tracecost.prices import PriceTable, price_export, price_generation


@pytest.fixture
def table(tmp_path: Path) -> PriceTable:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(SMALL_TABLE))
    return PriceTable.load(p)


def gen(supplied: SuppliedCost | None, model: str | None = "acme-flat") -> Generation:
    return Generation(
        trace_id="t",
        span_id="s",
        parent_id=None,
        name="chat",
        model=model,
        provider="acme",
        usage=Usage(1_000_000, 1_000_000),
        supplied_cost=supplied,
    )


def test_supplied_cost_wins_and_is_labelled(table: PriceTable) -> None:
    cost = price_generation(gen(SuppliedCost(0.42, source="gpu_time")), table)

    assert cost.amount == pytest.approx(0.42)
    assert cost.source == "supplied:gpu_time"
    assert cost.model_id == "acme-flat"  # still resolved, for grouping
    assert cost.breakdown == {}


def test_recompute_prices_from_tokens_instead(table: PriceTable) -> None:
    cost = price_generation(gen(SuppliedCost(0.42, source="gpu_time")), table, recompute=True)
    assert cost.source == "table"
    assert cost.amount == pytest.approx(2.0 + 8.0)


def test_recompute_reports_delta(table: PriceTable, tmp_path: Path) -> None:
    span = {
        "traceId": TRACE,
        "spanId": "0000000000000001",
        "name": "ChatCompletion",
        "attributes": [
            attr("gen_ai.operation.name", "chat"),
            attr("gen_ai.provider.name", "acme"),
            attr("gen_ai.request.model", "acme-flat"),
            attr("gen_ai.usage.input_tokens", 1_000_000),
            attr("gen_ai.usage.output_tokens", 1_000_000),
            attr("cost.amount", 9.5),
            attr("cost.currency", "USD"),
            attr("cost.source", "vendor_invoice"),
        ],
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(export_request(span)))

    priced = price_export(load(p), table)

    assert len(priced) == 1
    assert priced[0].cost.amount == pytest.approx(9.5)
    assert priced[0].cost.source == "supplied:vendor_invoice"
    assert priced[0].recomputed is not None
    assert priced[0].recomputed.amount == pytest.approx(10.0)
    assert priced[0].recomputed.source == "table"


def test_non_usd_supplied_cost_is_kept_with_a_note(table: PriceTable) -> None:
    cost = price_generation(gen(SuppliedCost(100.0, currency="THB", source="ocr")), table)
    assert cost.currency == "THB"
    assert any("THB" in n for n in cost.notes)


def test_supplied_cost_on_unknown_model_is_still_used(table: PriceTable) -> None:
    cost = price_generation(gen(SuppliedCost(1.5, source="ocr"), model="document-ai"), table)
    assert cost.amount == pytest.approx(1.5)
    assert cost.model_id is None
    assert cost.source == "supplied:ocr"


def test_no_supplied_cost_has_no_recomputed(table: PriceTable, tmp_path: Path) -> None:
    span = {
        "traceId": TRACE,
        "spanId": "0000000000000002",
        "name": "chat",
        "attributes": [
            attr("gen_ai.request.model", "acme-flat"),
            attr("gen_ai.usage.input_tokens", 10),
        ],
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(export_request(span)))
    priced = price_export(load(p), table)
    assert priced[0].recomputed is None
