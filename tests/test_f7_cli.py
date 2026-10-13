"""F7: the CLI is a thin wrapper: text and JSON output, --by, --fail-over, --prices, errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_f3_pricing import SMALL_TABLE
from tests.test_f4_rollup import gen_usage, obs
from tests.test_f6_diff import run_a, run_b
from tracecost import __version__
from tracecost.cli import app

runner = CliRunner()


@pytest.fixture
def prices(tmp_path: Path) -> Path:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(SMALL_TABLE))
    return p


@pytest.fixture
def export(tmp_path: Path) -> Path:
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
            None,
            "GENERATION",
            model="mystery-9000",
            usage=gen_usage(10, 10),
            name="chat",
        ),
    ]
    traces = [
        {"id": "t1", "sessionId": "s1", "userId": "alice"},
        {"id": "t2", "sessionId": "s1", "userId": "bob"},
    ]
    p = tmp_path / "obs.json"
    p.write_text(json.dumps({"data": data, "traces": traces}))
    return p


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"tracecost {__version__}"


def test_cost_text_and_json(export: Path, prices: Path) -> None:
    text = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--by", "root"])
    assert text.exit_code == 0, text.output
    assert f"tracecost {__version__} · 4 generations · 3 traces · prices " in text.output
    assert "(bundled+prices.json)" in text.output
    assert "by root" in text.output
    assert "plan-and-write" in text.output
    assert "$10.0000" in text.output  # root span total from two children, one under a tool span
    assert "total" in text.output
    assert "$11.0000" in text.output
    assert "mystery-9000" in text.output
    assert "no price" in text.output

    as_json = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--format", "json"])
    assert as_json.exit_code == 0, as_json.output
    doc = json.loads(as_json.output)
    assert doc["tracecost"] == __version__
    assert doc["by"] == "trace"
    assert doc["total"]["cost"] == pytest.approx(11.0)
    assert doc["total"]["llm_calls"] == 4
    assert doc["total"]["tool_calls"] == 1
    assert doc["total"]["unpriced_calls"] == 1
    assert {r["key"] for r in doc["rows"]} == {"t1", "t2", "t3"}
    assert len(doc["generations"]) == 4
    assert doc["generations"][0]["cost_source"] == "table"
    assert any("mystery-9000" in w for w in doc["warnings"])
    assert doc["prices"]["source"] == "bundled+prices.json"


@pytest.mark.parametrize("dimension", ["root", "trace", "session", "user", "model"])
def test_cost_every_dimension(export: Path, prices: Path, dimension: str) -> None:
    result = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--by", dimension])
    assert result.exit_code == 0, result.output
    assert f"by {dimension}" in result.output


def test_cost_bad_dimension_is_a_usage_error(export: Path) -> None:
    result = runner.invoke(app, ["cost", str(export), "--by", "colour"])
    assert result.exit_code == 2


def test_fail_over_exit_code(export: Path, prices: Path) -> None:
    ok = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--fail-over", "20"])
    assert ok.exit_code == 0
    over = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--fail-over", "5"])
    assert over.exit_code == 3
    assert "exceeds --fail-over" in over.output


def test_top_truncates_text_rows(export: Path, prices: Path) -> None:
    result = runner.invoke(app, ["cost", str(export), "--prices", str(prices), "--top", "1"])
    assert result.exit_code == 0
    assert "2 more rows" in result.output


def test_unrecognized_file_exits_1_with_the_path(tmp_path: Path) -> None:
    p = tmp_path / "notes.json"
    p.write_text(json.dumps({"hello": "world"}))
    result = runner.invoke(app, ["cost", str(p)])
    assert result.exit_code == 1
    assert "notes.json" in result.output
    assert "unrecognized format" in result.output


def test_diff_text_and_json(tmp_path: Path, prices: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"data": run_a()}))
    b.write_text(json.dumps({"data": run_b()}))

    text = runner.invoke(app, ["diff", str(a), str(b), "--prices", str(prices)])
    assert text.exit_code == 0, text.output
    assert "implement" in text.output
    assert "review" in text.output
    assert "$10.0000 → $11.3000" in text.output
    assert "acme-tiered" in text.output

    as_json = runner.invoke(app, ["diff", str(a), str(b), "--prices", str(prices), "-f", "json"])
    doc = json.loads(as_json.output)
    assert doc["total"]["cost_delta"] == pytest.approx(1.3)
    assert {r["key"]: r["status"] for r in doc["rows"]} == {
        "implement": "changed",
        "review": "added",
        "plan": "same",
    }

    budget = runner.invoke(
        app, ["diff", str(a), str(b), "--prices", str(prices), "--fail-over", "11"]
    )
    assert budget.exit_code == 3


def test_prices_command(prices: Path) -> None:
    summary = runner.invoke(app, ["prices", "--prices", str(prices)])
    assert summary.exit_code == 0
    assert "snapshot " in summary.output
    assert "bundled+prices.json" in summary.output

    lookup = runner.invoke(
        app, ["prices", "acme-flat-x", "--prices", str(prices), "--service-tier", "priority"]
    )
    assert lookup.exit_code == 0, lookup.output
    assert "acme/acme-flat" in lookup.output
    assert "input_mtok" in lookup.output
    assert "4.0" in lookup.output

    missing = runner.invoke(app, ["prices", "nope-1"])
    assert missing.exit_code == 1


def test_bundled_prices_are_the_default(tmp_path: Path) -> None:
    data = [
        obs("g", "t", None, "GENERATION", model="gpt-4o", usage=gen_usage(1_000_000, 0), name="c")
    ]
    p = tmp_path / "obs.json"
    p.write_text(json.dumps(data))
    result = runner.invoke(app, ["cost", str(p), "--at", "2026-01-01", "-f", "json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["total"]["cost"] == pytest.approx(2.5)
    assert doc["prices"]["source"] == "bundled"
