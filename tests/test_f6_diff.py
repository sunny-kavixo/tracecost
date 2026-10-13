"""F6: diff two exports by root-span name.

phoenix discussion 14736 ("whatbroke: diff what your agent did between two runs, from OTLP span
exports"). Trace and span ids differ between runs, so the stable key is the root span's name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_f3_pricing import SMALL_TABLE
from tests.test_f4_rollup import gen_usage, obs
from tracecost import load
from tracecost.diff import Diff, diff
from tracecost.prices import PriceTable, price_export
from tracecost.rollup import rollup


def run_a() -> list[dict[str, object]]:
    return [
        obs("r1", "t1", None, "AGENT", name="plan"),
        obs("g1", "t1", "r1", "GENERATION", model="acme-flat", usage=gen_usage(1_000_000, 0)),
        obs("r2", "t2", None, "AGENT", name="implement"),
        obs("g2", "t2", "r2", "GENERATION", model="acme-flat", usage=gen_usage(0, 1_000_000)),
        obs("tl", "t2", "r2", "TOOL", name="edit"),
    ]


def run_b() -> list[dict[str, object]]:
    return [
        obs("r1", "t1", None, "AGENT", name="plan"),
        obs("g1", "t1", "r1", "GENERATION", model="acme-flat", usage=gen_usage(1_000_000, 0)),
        obs("r2", "t2", None, "AGENT", name="implement"),
        obs("g2", "t2", "r2", "GENERATION", model="acme-flat", usage=gen_usage(0, 1_000_000)),
        obs("g3", "t2", "r2", "GENERATION", model="acme-tiered", usage=gen_usage(100_000, 0)),
        obs("tl", "t2", "r2", "TOOL", name="edit"),
        obs("tl2", "t2", "r2", "TOOL", name="test"),
        obs("r3", "t3", None, "AGENT", name="review"),
        obs("g4", "t3", "r3", "GENERATION", model="acme-flat", usage=gen_usage(500_000, 0)),
    ]


@pytest.fixture
def result(tmp_path: Path) -> Diff:
    table_path = tmp_path / "prices.json"
    table_path.write_text(json.dumps(SMALL_TABLE))
    table = PriceTable.load(table_path)
    rollups = []
    for name, rows in (("a", run_a()), ("b", run_b())):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps({"data": rows}))
        export = load(p)
        rollups.append(rollup(export, price_export(export, table)))
    return diff(rollups[0], rollups[1])


def test_diff_per_root_name(result: Diff) -> None:
    rows = {r.key: r for r in result.rows}
    assert set(rows) == {"plan", "implement", "review"}

    plan = rows["plan"]
    assert plan.cost_a == pytest.approx(2.0)
    assert plan.cost_b == pytest.approx(2.0)
    assert plan.cost_delta == pytest.approx(0.0)

    impl = rows["implement"]
    assert impl.calls_a == 1
    assert impl.calls_b == 2
    assert impl.tool_calls_a == 1
    assert impl.tool_calls_b == 2
    assert impl.tokens_a == 1_000_000
    assert impl.tokens_b == 1_100_000
    assert impl.cost_delta == pytest.approx(0.3)  # 100k * 3.0 / 1e6

    review = rows["review"]
    assert review.cost_a == 0.0
    assert review.calls_a == 0
    assert review.cost_b == pytest.approx(1.0)
    assert review.status == "added"
    assert impl.status == "changed"
    assert plan.status == "same"


def test_diff_totals_and_model_mix(result: Diff) -> None:
    assert result.total.cost_a == pytest.approx(10.0)
    assert result.total.cost_b == pytest.approx(11.3)
    assert result.total.calls_a == 2
    assert result.total.calls_b == 4
    assert result.models == {"acme-flat": (2, 3), "acme-tiered": (0, 1)}


def test_rows_sorted_by_absolute_cost_delta(result: Diff) -> None:
    deltas = [abs(r.cost_delta) for r in result.rows]
    assert deltas == sorted(deltas, reverse=True)


def test_removed_root(tmp_path: Path) -> None:
    table_path = tmp_path / "prices.json"
    table_path.write_text(json.dumps(SMALL_TABLE))
    table = PriceTable.load(table_path)
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"data": run_b()}))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"data": run_a()}))
    ea, eb = load(a), load(b)
    result = diff(rollup(ea, price_export(ea, table)), rollup(eb, price_export(eb, table)))
    review = next(r for r in result.rows if r.key == "review")
    assert review.status == "removed"
    assert review.cost_delta == pytest.approx(-1.0)
