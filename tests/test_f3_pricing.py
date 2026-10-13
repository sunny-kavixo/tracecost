"""F3: price every generation from the bundled dated table, flag what can't be priced.

Pricing semantics follow pydantic/genai-prices (packages/python/genai_prices/types.py):
rates are USD per million tokens; tiered prices are threshold "cliffs" selected by the
generation's *total* input tokens; `price_variants` select by `service_tier`; conditional price
lists pick the last entry whose `start_date` is on or before the call. Reasoning tokens bill at
the output rate unless the model lists `output_reasoning_mtok`.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tracecost import Generation, Usage
from tracecost.prices import PriceTable, price_generation

SMALL_TABLE = {
    "snapshot_date": "2026-10-01",
    "providers": [
        {
            "id": "acme",
            "name": "Acme",
            "provider_match": {"contains": "acme"},
            "model_match": {"starts_with": "acme-"},
            "models": [
                {
                    "id": "acme-flat",
                    "match": {"starts_with": "acme-flat"},
                    "prices": {
                        "input_mtok": 2.0,
                        "cache_read_mtok": 0.5,
                        "cache_write_mtok": 2.5,
                        "output_mtok": 8.0,
                    },
                    "price_variants": [
                        {"when": {"service_tier": "default"}, "prices": {}},
                        {
                            "when": {"service_tier": "priority"},
                            "prices": {"input_mtok": 4.0, "output_mtok": 16.0},
                        },
                    ],
                },
                {
                    "id": "acme-tiered",
                    "match": {"equals": "acme-tiered"},
                    "prices": {
                        "input_mtok": {"base": 3.0, "tiers": [{"start": 200000, "price": 6.0}]},
                        "output_mtok": {"base": 15.0, "tiers": [{"start": 200000, "price": 30.0}]},
                    },
                },
                {
                    "id": "acme-dated",
                    "match": {"equals": "acme-dated"},
                    "prices": [
                        {"prices": {"input_mtok": 10.0, "output_mtok": 20.0}},
                        {
                            "constraint": {"start_date": "2026-06-01"},
                            "prices": {"input_mtok": 1.0, "output_mtok": 2.0},
                        },
                    ],
                },
                {
                    "id": "acme-nocache",
                    "match": {"equals": "acme-nocache"},
                    "prices": {"input_mtok": 1.0, "output_mtok": 1.0},
                },
            ],
        }
    ],
}


def gen(model: str, usage: Usage, **kw: object) -> Generation:
    return Generation(
        trace_id="t",
        span_id="s",
        parent_id=None,
        name="chat",
        model=model,
        provider=kw.pop("provider", "acme"),  # type: ignore[arg-type]
        usage=usage,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def table(tmp_path: Path) -> PriceTable:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(SMALL_TABLE))
    return PriceTable.load(p)


def test_cache_and_tier_pricing(table: PriceTable) -> None:
    # 1,000,000 input of which 400,000 cache read and 100,000 cache write; 10,000 output
    usage = Usage(
        1_000_000,
        10_000,
        cache_read_tokens=400_000,
        cache_write_tokens=100_000,
        cache_reported=True,
    )
    cost = price_generation(gen("acme-flat-2026", usage), table)

    assert cost.source == "table"
    assert cost.model_id == "acme-flat"
    assert cost.provider_id == "acme"
    # 500k uncached * 2.0 + 400k * 0.5 + 100k * 2.5 + 10k * 8.0, all per million
    assert cost.amount == pytest.approx(1.0 + 0.2 + 0.25 + 0.08)
    assert cost.breakdown["input"] == pytest.approx(1.0)
    assert cost.breakdown["cache_read"] == pytest.approx(0.2)
    assert cost.breakdown["cache_write"] == pytest.approx(0.25)
    assert cost.breakdown["output"] == pytest.approx(0.08)
    assert cost.notes == []


def test_tier_cliff_applies_to_all_tokens_once_crossed(table: PriceTable) -> None:
    below = price_generation(gen("acme-tiered", Usage(199_999, 1_000)), table)
    above = price_generation(gen("acme-tiered", Usage(200_001, 1_000)), table)

    assert below.amount == pytest.approx(199_999 * 3.0 / 1e6 + 1_000 * 15.0 / 1e6)
    assert above.amount == pytest.approx(200_001 * 6.0 / 1e6 + 1_000 * 30.0 / 1e6)


def test_service_tier_variant(table: PriceTable) -> None:
    usage = Usage(1_000_000, 1_000_000)
    default = price_generation(gen("acme-flat", usage), table)
    priority = price_generation(gen("acme-flat", usage, service_tier="priority"), table)
    unknown_tier = price_generation(gen("acme-flat", usage, service_tier="turbo"), table)

    assert default.amount == pytest.approx(2.0 + 8.0)
    assert priority.amount == pytest.approx(4.0 + 16.0)
    assert unknown_tier.amount == pytest.approx(2.0 + 8.0)
    assert any("turbo" in n for n in unknown_tier.notes)


def test_dated_prices_pick_the_price_in_force_at_call_time(table: PriceTable) -> None:
    usage = Usage(1_000_000, 0)
    before = price_generation(
        gen("acme-dated", usage), table, at=dt.datetime(2026, 1, 15, tzinfo=dt.UTC)
    )
    after = price_generation(
        gen("acme-dated", usage), table, at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    )

    assert before.amount == pytest.approx(10.0)
    assert after.amount == pytest.approx(1.0)


def test_reasoning_tokens_bill_at_output_rate(table: PriceTable) -> None:
    usage = Usage(0, 1_000_000, reasoning_tokens=250_000)
    cost = price_generation(gen("acme-flat", usage), table)
    assert cost.amount == pytest.approx(8.0)
    assert cost.breakdown["reasoning"] == pytest.approx(2.0)
    assert cost.breakdown["output"] == pytest.approx(6.0)


def test_cache_tokens_without_cache_rate_fall_back_to_input_rate_with_note(
    table: PriceTable,
) -> None:
    usage = Usage(1_000_000, 0, cache_read_tokens=1_000_000, cache_reported=True)
    cost = price_generation(gen("acme-nocache", usage), table)
    assert cost.amount == pytest.approx(1.0)
    assert any("cache_read" in n for n in cost.notes)


def test_unknown_model_is_flagged(table: PriceTable) -> None:
    cost = price_generation(gen("my-finetune-v2", Usage(1000, 10)), table)
    assert cost.source == "unknown-model"
    assert cost.amount == 0.0
    assert cost.model_id is None
    assert any("my-finetune-v2" in n for n in cost.notes)


def test_model_found_without_provider_hint(table: PriceTable) -> None:
    cost = price_generation(gen("acme-flat", Usage(1_000_000, 0), provider=None), table)
    assert cost.provider_id == "acme"
    assert cost.amount == pytest.approx(2.0)


def test_prices_override_adds_and_replaces_models(table: PriceTable, tmp_path: Path) -> None:
    override = {
        "providers": [
            {
                "id": "acme",
                "models": [
                    {
                        "id": "acme-flat",
                        "match": {"starts_with": "acme-flat"},
                        "prices": {"input_mtok": 100.0, "output_mtok": 100.0},
                    }
                ],
            },
            {
                "id": "local",
                "provider_match": {"contains": "local"},
                "models": [
                    {
                        "id": "my-finetune-v2",
                        "match": {"equals": "my-finetune-v2"},
                        "prices": {"input_mtok": 0.1, "output_mtok": 0.2},
                    }
                ],
            },
        ]
    }
    p = tmp_path / "override.json"
    p.write_text(json.dumps(override))
    merged = table.with_overrides(p)

    assert price_generation(gen("acme-flat", Usage(1_000_000, 0)), merged).amount == pytest.approx(
        100.0
    )
    mine = price_generation(
        gen("my-finetune-v2", Usage(1_000_000, 1_000_000), provider=None), merged
    )
    assert mine.amount == pytest.approx(0.1 + 0.2)
    assert mine.provider_id == "local"


def test_bundled_snapshot_loads_and_prices_a_known_model() -> None:
    table = PriceTable.load()
    assert table.snapshot_date >= "2026-10-13"
    assert table.model_count > 1000
    # gpt-4o has been $2.50 / $1.25 cached / $10.00 per million since 2024-08
    cost = price_generation(
        gen(
            "gpt-4o-2024-11-20",
            Usage(1_000_000, 1_000_000, cache_read_tokens=1_000_000, cache_reported=True),
            provider="openai",
        ),
        table,
        at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    assert cost.model_id == "gpt-4o"
    assert cost.amount == pytest.approx(1.25 + 10.0)


def test_bundled_snapshot_claude_tiers_and_cache_write() -> None:
    table = PriceTable.load()
    usage = Usage(300_000, 1_000, cache_write_tokens=100_000, cache_reported=True)
    cost = price_generation(gen("claude-sonnet-4-5-20250929", usage, provider="anthropic"), table)
    # above the 200k cliff: input 6.0, cache write 7.5, output 22.5 per million
    assert cost.model_id == "claude-sonnet-4-5"
    assert cost.amount == pytest.approx(
        200_000 * 6.0 / 1e6 + 100_000 * 7.5 / 1e6 + 1_000 * 22.5 / 1e6
    )
