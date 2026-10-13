"""mlflow#22745 and ollama#15758 — cache tokens dropped or never reported
https://github.com/mlflow/mlflow/issues/22745
https://github.com/ollama/ollama/issues/15758

The same Anthropic call exported twice: once with `gen_ai.usage.cache_read.input_tokens`, once by
an exporter that drops cache attributes. Without them every input token is billed at the full
rate, and nothing says so. tracecost prices cache reads at the cache rate and warns when a
model that bills cache has generations with no cache attributes at all.

    uv run python examples/mlflow_22745_cache_tokens.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import chat, hex_id, load, price_and_rollup, show, write_otlp

from tracecost.report import collect_warnings


def main() -> int:
    with_cache = chat(
        hex_id(1, 32),
        hex_id(1),
        "claude-sonnet-4-5",
        "anthropic",
        input_tokens=60_000,
        output_tokens=800,
        cache_read=55_000,
        cache_write=2_000,
    )
    cache_dropped = chat(
        hex_id(2, 32),
        hex_id(2),
        "claude-sonnet-4-5",
        "anthropic",
        input_tokens=60_000,
        output_tokens=800,
    )
    with tempfile.TemporaryDirectory() as tmp:
        export = load(write_otlp(Path(tmp) / "traces.json", [with_cache, cache_dropped]))

    show(export, "trace")
    table, priced, _ = price_and_rollup(export)
    full, dropped = priced
    assert full.generation.usage.cache_reported is True
    assert dropped.generation.usage.cache_reported is False
    assert full.cost.amount < dropped.cost.amount, "cache reads must be cheaper than full input"
    assert full.cost.breakdown["cache_read"] > 0
    assert full.cost.breakdown["cache_write"] > 0
    warnings = collect_warnings(priced, table).lines()
    assert any("no cache attributes" in w for w in warnings), warnings
    saving = dropped.cost.amount - full.cost.amount
    print(
        f"with cache attributes ${full.cost.amount:.4f}, without ${dropped.cost.amount:.4f} "
        f"(overstated by ${saving:.4f})"
    )
    print("OK: cache tokens are priced at the cache rate and their absence is flagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
