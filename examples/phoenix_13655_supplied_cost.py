"""phoenix#13655 — "Support custom span costs independent of LLM token pricing"
https://github.com/Arize-ai/phoenix/issues/13655

A commenter runs a self-hosted vLLM model billed by GPU time and writes `cost_usd` on each span;
Phoenix "re-derives cost instead of trusting the value we already have". tracecost uses a
supplied cost first, labels where it came from, and shows what the token table would have said.

    uv run python examples/phoenix_13655_supplied_cost.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import hex_id, load, price_and_rollup, show, span, write_otlp

from tracecost.report import collect_warnings

TRACE = hex_id(1, 32)


def main() -> int:
    spans = [
        # self-hosted model: unknown to any price table, billed by GPU seconds by the caller
        span(
            TRACE,
            hex_id(1),
            "ChatCompletion",
            {
                "openinference.span.kind": "LLM",
                "llm.provider": "vllm",
                "llm.model_name": "internal/qwen3-32b-finetune",
                "llm.token_count.prompt": 4_000,
                "llm.token_count.completion": 600,
                "llm.cost.total": 0.0031,  # GPU seconds * hourly rate, computed by the caller
                "cost.source": "gpu_time",
            },
        ),
        # hosted model with a caller-supplied cost that disagrees with the token table
        span(
            TRACE,
            hex_id(2),
            "ChatCompletion",
            {
                "openinference.span.kind": "LLM",
                "llm.provider": "openai",
                "llm.model_name": "gpt-4o",
                "llm.token_count.prompt": 100_000,
                "llm.token_count.completion": 10_000,
                "llm.cost.total": 0.30,  # the caller's own (stale) table
            },
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        export = load(write_otlp(Path(tmp) / "phoenix-spans.json", spans))

    print("--- as supplied")
    show(export, "model")
    table, priced, _ = price_and_rollup(export)
    gpu, hosted = priced

    assert gpu.cost.amount == 0.0031
    assert gpu.cost.source == "supplied:gpu_time"
    assert gpu.recomputed is not None
    assert gpu.recomputed.source == "unknown-model"
    assert hosted.cost.amount == 0.30
    assert hosted.cost.source == "supplied:llm.cost.total"
    assert hosted.recomputed is not None
    assert hosted.recomputed.amount > 0
    warnings = collect_warnings(priced, table).lines()
    assert any("supplied cost" in w and "token table says" in w for w in warnings)

    print("--- with --recompute (token table only)")
    show(export, "model", recompute=True)
    _, recomputed, _ = price_and_rollup(export, recompute=True)
    assert recomputed[0].cost.source == "unknown-model"
    assert recomputed[1].cost.source == "table"
    delta = recomputed[1].cost.amount - hosted.cost.amount
    print(f"gpt-4o: supplied $0.3000, table ${recomputed[1].cost.amount:.4f} (delta {delta:+.4f})")
    print("OK: supplied costs are honoured and labelled; --recompute audits them against the table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
