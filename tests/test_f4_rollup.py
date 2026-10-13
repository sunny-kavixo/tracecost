"""F4: roll cost up to root span, trace, session, user and model.

Motivation: langfuse#16077 (root SPAN rows show $0.00 because only child GENERATIONs carry
usage), #18210 (per-user total only sums the top 20 users), #15109 (session total $0.00),
#14889 (trace-list token aggregation null).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_f3_pricing import SMALL_TABLE
from tracecost import load
from tracecost.prices import PriceTable, price_export
from tracecost.rollup import Rollup, rollup

# Three traces. t1: root agent span with two child chats (one nested under a tool span).
# t2: a single chat span that is its own root, in the same session as t1 but another user.
# t3: a generation without parent_id present in the file (orphan) -> treated as its own root.


def obs(id_: str, trace: str, parent: str | None, type_: str, **extra: object) -> dict[str, object]:
    return {
        "id": id_,
        "traceId": trace,
        "parentObservationId": parent,
        "type": type_,
        "name": extra.pop("name", type_.lower()),
        "model": extra.pop("model", None),
        "usageDetails": extra.pop("usage", {}),
        "costDetails": {},
        **extra,
    }


def gen_usage(inp: int, out: int) -> dict[str, int]:
    return {"input": inp, "output": out, "total": inp + out}


@pytest.fixture
def export_path(tmp_path: Path) -> Path:
    data = [
        obs("r1", "t1", None, "AGENT", name="plan-and-write"),
        obs("g1", "t1", "r1", "GENERATION", model="acme-flat", usage=gen_usage(1_000_000, 0)),
        obs("tool1", "t1", "r1", "TOOL", name="search"),
        obs("g2", "t1", "tool1", "GENERATION", model="acme-flat", usage=gen_usage(0, 1_000_000)),
        obs(
            "g3",
            "t2",
            None,
            "GENERATION",
            model="acme-flat",
            usage=gen_usage(500_000, 0),
            name="chat",
        ),
        obs(
            "g4",
            "t3",
            "missing",
            "GENERATION",
            model="acme-flat",
            usage=gen_usage(0, 500_000),
            name="chat",
        ),
    ]
    traces = [
        {"id": "t1", "sessionId": "s1", "userId": "alice"},
        {"id": "t2", "sessionId": "s1", "userId": "bob"},
        {"id": "t3", "sessionId": None, "userId": "alice"},
    ]
    p = tmp_path / "obs.json"
    p.write_text(json.dumps({"data": data, "traces": traces}))
    return p


@pytest.fixture
def result(export_path: Path, tmp_path: Path) -> Rollup:
    table_path = tmp_path / "prices.json"
    table_path.write_text(json.dumps(SMALL_TABLE))
    export = load(export_path)
    priced = price_export(export, PriceTable.load(table_path))
    return rollup(export, priced)


def test_root_span_gets_children_cost(result: Rollup) -> None:
    roots = {r.key: r for r in result.by("root")}
    # acme-flat: input 2.0/M, output 8.0/M -> g1 = $2.00, g2 = $8.00 (nested under a tool span)
    assert roots["t1/r1"].cost == pytest.approx(10.0)
    assert roots["t1/r1"].name == "plan-and-write"
    assert roots["t1/r1"].llm_calls == 2
    assert roots["t1/r1"].tool_calls == 1
    assert roots["t1/r1"].usage.input_tokens == 1_000_000
    assert roots["t1/r1"].usage.output_tokens == 1_000_000


def test_generation_that_is_its_own_root_and_orphan(result: Rollup) -> None:
    roots = {r.key: r for r in result.by("root")}
    assert roots["t2/g3"].cost == pytest.approx(1.0)
    assert roots["t3/g4"].cost == pytest.approx(4.0)  # parent id missing from the file
    assert roots["t3/g4"].name == "chat"


def test_trace_totals(result: Rollup) -> None:
    traces = {r.key: r for r in result.by("trace")}
    assert traces["t1"].cost == pytest.approx(10.0)
    assert traces["t2"].cost == pytest.approx(1.0)
    assert traces["t3"].cost == pytest.approx(4.0)
    assert traces["t1"].name == "plan-and-write"  # named after its root span


def test_session_and_user_totals_count_every_row(result: Rollup) -> None:
    sessions = {r.key: r for r in result.by("session")}
    assert sessions["s1"].cost == pytest.approx(11.0)
    assert sessions["(none)"].cost == pytest.approx(4.0)
    users = {r.key: r for r in result.by("user")}
    assert users["alice"].cost == pytest.approx(14.0)
    assert users["bob"].cost == pytest.approx(1.0)
    assert users["alice"].traces == 2


def test_model_totals_and_grand_total(result: Rollup) -> None:
    models = result.by("model")
    assert [m.key for m in models] == ["acme-flat"]
    assert models[0].cost == pytest.approx(15.0)
    assert models[0].llm_calls == 4
    assert result.total.cost == pytest.approx(15.0)
    assert result.total.llm_calls == 4
    assert result.total.traces == 3


def test_rows_sorted_by_cost_descending_then_key(result: Rollup) -> None:
    costs = [r.cost for r in result.by("root")]
    assert costs == sorted(costs, reverse=True)


def test_unknown_dimension_raises(result: Rollup) -> None:
    with pytest.raises(ValueError, match="dimension"):
        result.by("colour")
