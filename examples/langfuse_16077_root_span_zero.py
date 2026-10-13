"""langfuse#16077 — "v4 unified Tracing table cannot display trace total cost — root SPAN rows show $0.00"
https://github.com/langfuse/langfuse/issues/16077

The reporter's LangGraph agent creates one root SPAN per conversation turn with the LLM calls as
nested GENERATIONs. Cost lives only on the children, so a list view keyed by root observation
shows $0.00. tracecost rolls every generation up to its root, however deep.

    uv run python examples/langfuse_16077_root_span_zero.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import load, show


def observation(
    id_: str, trace: str, parent: str | None, type_: str, name: str, **kw: object
) -> dict[str, object]:
    return {
        "id": id_,
        "traceId": trace,
        "parentObservationId": parent,
        "type": type_,
        "name": name,
        "startTime": "2026-08-13T10:00:00.000Z",
        "endTime": "2026-08-13T10:00:04.000Z",
        "model": kw.get("model"),
        "usageDetails": kw.get("usage", {}),
        "costDetails": {},
    }


def main() -> int:
    observations = [
        # conversation turn 1: root SPAN, planner call, tool, writer call under the tool
        observation("turn-1", "trace-1", None, "SPAN", "conversation-turn"),
        observation(
            "plan-1",
            "trace-1",
            "turn-1",
            "GENERATION",
            "planner",
            model="gpt-4o",
            usage={"input": 2_000, "input_cached_tokens": 6_000, "output": 300, "total": 8_300},
        ),
        observation("search-1", "trace-1", "turn-1", "TOOL", "web_search"),
        observation(
            "write-1",
            "trace-1",
            "search-1",
            "GENERATION",
            "writer",
            model="claude-sonnet-4-5",
            usage={
                "input": 1_200,
                "cache_read_input_tokens": 9_000,
                "output": 900,
                "total": 11_100,
            },
        ),
        # conversation turn 2: one call
        observation("turn-2", "trace-2", None, "SPAN", "conversation-turn"),
        observation(
            "plan-2",
            "trace-2",
            "turn-2",
            "GENERATION",
            "planner",
            model="gpt-4o",
            usage={"input": 2_500, "output": 250, "total": 2_750},
        ),
    ]
    traces = [
        {"id": "trace-1", "sessionId": "session-42", "userId": "hector"},
        {"id": "trace-2", "sessionId": "session-42", "userId": "hector"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "langfuse-observations.json"
        path.write_text(json.dumps({"data": observations, "traces": traces}))
        export = load(path)
        result = show(export, "root")
        show(export, "session")

    turn_1 = next(r for r in result.by("root") if r.key == "trace-1/turn-1")
    children = [g for g in export.generations if g.trace_id == "trace-1"]
    assert turn_1.cost > 0, "root span must carry the cost of its generations"
    assert turn_1.llm_calls == 2
    assert turn_1.tool_calls == 1
    assert len(children) == 2
    session = result.by("session")[0]
    assert session.key == "session-42"
    assert session.llm_calls == 3
    print("OK: root spans and the session carry the summed cost of their generations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
