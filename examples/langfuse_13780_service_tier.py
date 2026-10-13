"""langfuse#13780 — "incorrect OpenAI models pricing for 'priority' service tier"
https://github.com/langfuse/langfuse/issues/13780

OpenAI bills the priority and flex tiers at different rates from the default tier, and the tier
is in the response (`openai.response.service_tier` in the OTel OpenAI extension registry). A price
table keyed on model alone gets it wrong. tracecost applies the snapshot's service-tier variants.

    uv run python examples/langfuse_13780_service_tier.py
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import chat, hex_id, load, price_and_rollup, show, write_otlp

AT = dt.datetime(2026, 10, 13, tzinfo=dt.UTC)


def main() -> int:
    calls = [
        chat(
            hex_id(1, 32),
            hex_id(1),
            "gpt-4o",
            "openai",
            input_tokens=1_000_000,
            output_tokens=100_000,
            extra={"openai.response.service_tier": "default"},
        ),
        chat(
            hex_id(2, 32),
            hex_id(2),
            "gpt-4o",
            "openai",
            input_tokens=1_000_000,
            output_tokens=100_000,
            extra={"openai.response.service_tier": "priority"},
        ),
        chat(
            hex_id(3, 32),
            hex_id(3),
            "gpt-4o",
            "openai",
            input_tokens=1_000_000,
            output_tokens=100_000,
            extra={"openai.response.service_tier": "scale"},
        ),  # not a known tier
    ]
    with tempfile.TemporaryDirectory() as tmp:
        export = load(write_otlp(Path(tmp) / "traces.json", calls))

    show(export, "trace", at=AT)
    _, priced, _ = price_and_rollup(export, at=AT)
    default, priority, unknown = priced
    assert priority.cost.amount > default.cost.amount, "priority tier must cost more"
    assert unknown.cost.amount == default.cost.amount
    assert any("scale" in n for n in unknown.cost.notes), unknown.cost.notes
    print(
        f"default ${default.cost.amount:.4f} · priority ${priority.cost.amount:.4f} · "
        f"unknown tier 'scale' priced as default with a note"
    )
    print("OK: service tiers change the price; unknown tiers are flagged, not guessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
