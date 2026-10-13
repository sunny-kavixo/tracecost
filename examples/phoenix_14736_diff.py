"""phoenix discussion 14736 — "whatbroke: diff what your agent did between two runs, from OTLP span exports"
https://github.com/Arize-ai/phoenix/discussions/14736

Two runs of the same coding agent (plan → implement → review). Trace and span ids differ, so
tracecost matches root spans by name and reports what moved: calls, tokens, cost, model mix.

    uv run python examples/phoenix_14736_diff.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import T0, agent, chat, hex_id, load, price_and_rollup, tool, write_otlp

from tracecost.diff import diff
from tracecost.report import diff_table

NS = 1_000_000_000


def run(seed: int, *, implement_calls: int, model: str) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    n = seed * 100
    for i, step in enumerate(("plan", "implement", "review")):
        trace = hex_id(n + i, 32)
        root = hex_id(n + i * 10)
        start = T0 + i * 60 * NS
        spans.append(agent(trace, root, step, session=f"run-{seed}", user="ci", start=start))
        calls = implement_calls if step == "implement" else 1
        for c in range(calls):
            spans.append(
                chat(
                    trace,
                    hex_id(n + i * 10 + 1 + c),
                    model if step == "implement" else "claude-sonnet-4-5",
                    "openai" if model.startswith("gpt") and step == "implement" else "anthropic",
                    parent=root,
                    input_tokens=20_000 + 5_000 * c,
                    output_tokens=1_500,
                    cache_read=15_000,
                    reasoning=400 if model.startswith("gpt-5") else None,
                    start=start + c * NS,
                )
            )
            if step == "implement":
                spans.append(
                    tool(trace, hex_id(n + i * 10 + 50 + c), "edit_file", parent=root, start=start)
                )
    return spans


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        a = load(
            write_otlp(
                Path(tmp) / "run-a.json", run(1, implement_calls=3, model="claude-sonnet-4-5")
            )
        )
        b = load(write_otlp(Path(tmp) / "run-b.json", run(2, implement_calls=6, model="gpt-5")))
    _, _, ra = price_and_rollup(a)
    _, _, rb = price_and_rollup(b)
    result = diff(ra, rb)
    print(diff_table(result))
    print()

    rows = {r.key: r for r in result.rows}
    assert rows["plan"].status == "same"
    assert rows["implement"].status == "changed"
    assert rows["implement"].calls_b == 6
    assert rows["implement"].calls_a == 3
    assert rows["implement"].tool_calls_b == 6
    assert result.models["gpt-5"] == (0, 6)
    assert result.total.calls_b > result.total.calls_a
    print("OK: the diff pins the change to the 'implement' step and the model switch to gpt-5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
