"""Regenerate examples/demo/traces.jsonl, the OTLP/JSON Lines export the README demo runs on.

    uv run python examples/demo/make_demo.py            # writes examples/demo/traces.jsonl
    uv run python examples/demo/make_demo.py OUT.jsonl  # writes elsewhere (used by the tests)

The data is synthetic but shaped like a real support agent: six conversation turns over three
sessions and two users, each turn an `invoke_agent` root with a planner call (gpt-5 with
reasoning, one on the priority tier), a tool, and an answer call (claude-sonnet-4-5 with prompt
caching, one turn with cache attributes missing), plus one call on a self-hosted model the price
table doesn't know and one with a caller-supplied cost. Deterministic: same ids every run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import T0, agent, chat, hex_id, otlp_request, tool, write_jsonl

NS = 1_000_000_000
OUT = Path(__file__).with_name("traces.jsonl")

TURNS = [
    # (session, user, planner_tier, answer_cache_reported, extra)
    ("sess-a1", "alice", "default", True, None),
    ("sess-a1", "alice", "default", True, None),
    ("sess-a1", "alice", "priority", True, None),
    ("sess-b7", "bob", "default", False, None),
    ("sess-b7", "bob", "default", True, "unknown-model"),
    ("sess-c3", "carol", "default", True, "supplied-cost"),
]


def turn(
    i: int, session: str, user: str, tier: str, cache_reported: bool, extra: str | None
) -> dict:  # type: ignore[type-arg]
    trace = f"{(0x8C4E2FA71D93B650 << 64) | (0x3B19E7C2D0F4A600 + i * 0x2B7):032x}"
    root = hex_id(0x5A0C3E1F00 + 0x10 * (i + 1))
    start = T0 + i * 90 * NS
    spans = [
        agent(trace, root, "support-turn", session=session, user=user, start=start, duration_s=12)
    ]
    spans.append(
        chat(
            trace,
            hex_id(0x10 * (i + 1) + 1),
            "gpt-5",
            "openai",
            parent=root,
            input_tokens=9_000 + 400 * i,
            output_tokens=700,
            cache_read=6_000,
            reasoning=350,
            session=session,
            user=user,
            start=start + 1 * NS,
            extra={"openai.response.service_tier": tier},
        )
    )
    spans.append(
        tool(trace, hex_id(0x10 * (i + 1) + 2), "lookup_order", parent=root, start=start + 3 * NS)
    )
    answer_model, provider = "claude-sonnet-4-5", "anthropic"
    if extra == "unknown-model":
        answer_model, provider = "acme-support-7b", "acme"
    spans.append(
        chat(
            trace,
            hex_id(0x10 * (i + 1) + 3),
            answer_model,
            provider,
            parent=root,
            input_tokens=22_000 + 1_000 * i,
            output_tokens=1_200,
            cache_read=18_000 if cache_reported else None,
            cache_write=1_500 if cache_reported else None,
            session=session,
            user=user,
            start=start + 5 * NS,
            extra={"cost.amount": 0.0125, "cost.source": "vendor_invoice"}
            if extra == "supplied-cost"
            else None,
        )
    )
    return otlp_request(spans, service="support-agent")


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else OUT
    requests = [turn(i, *spec) for i, spec in enumerate(TURNS)]
    write_jsonl(out, requests)
    print(f"wrote {out} ({len(requests)} traces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
