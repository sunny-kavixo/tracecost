"""Command-line interface: a thin wrapper over the library."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Annotated

import typer

from tracecost import __version__, load
from tracecost.diff import diff as diff_rollups
from tracecost.model import Export
from tracecost.prices import PriceTable, price_export, resolve_rates
from tracecost.report import (
    collect_warnings,
    cost_json,
    cost_table,
    diff_json,
    diff_table,
    header,
)
from tracecost.rollup import DIMENSIONS, rollup

EXIT_BUDGET = 3

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Recompute what your LLM traces cost, offline, from OTLP, Langfuse or Phoenix exports.\n\n"
        "Exit codes: 0 ok · 1 unreadable input · 2 usage · 3 --fail-over exceeded."
    ),
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"tracecost {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """tracecost."""


def _table(prices: Path | None) -> PriceTable:
    table = PriceTable.load()
    return table.with_overrides(prices) if prices else table


def _parse_at(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("expected YYYY-MM-DD or an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _load(files: list[Path]) -> Export:
    try:
        return load(*files)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


FilesArg = Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, help="Export files.")]
PricesOpt = Annotated[
    Path | None,
    typer.Option(
        "--prices",
        exists=True,
        dir_okay=False,
        help="JSON price table that overrides/extends the snapshot.",
    ),
]
FormatOpt = Annotated[str, typer.Option("--format", "-f", help="text or json.")]
FailOverOpt = Annotated[
    float | None,
    typer.Option("--fail-over", help="Exit 3 if the total cost exceeds this many USD."),
]


def _check_format(value: str) -> str:
    if value not in ("text", "json"):
        raise typer.BadParameter("expected 'text' or 'json'")
    return value


@app.command()
def cost(
    files: FilesArg,
    by: Annotated[str, typer.Option("--by", help="root, trace, session, user or model.")] = "trace",
    fmt: FormatOpt = "text",
    prices: PricesOpt = None,
    recompute: Annotated[
        bool, typer.Option("--recompute", help="Ignore supplied costs; price from tokens.")
    ] = False,
    at: Annotated[
        str | None,
        typer.Option("--at", help="Price as of this date (default: each span's start time)."),
    ] = None,
    top: Annotated[int, typer.Option("--top", help="Rows to print in text mode.")] = 20,
    fail_over: FailOverOpt = None,
) -> None:
    """Price every LLM call in the exports and roll the cost up."""
    _check_format(fmt)
    if by not in DIMENSIONS:
        raise typer.BadParameter(f"--by must be one of {', '.join(DIMENSIONS)}")
    export = _load(files)
    table = _table(prices)
    priced = price_export(export, table, recompute=recompute, at=_parse_at(at))
    result = rollup(export, priced)
    warnings = collect_warnings(priced, table)
    if fmt == "json":
        typer.echo(cost_json(export, table, result, priced, by, warnings))
    else:
        typer.echo(header(export, table, result))
        typer.echo()
        typer.echo(cost_table(result, by, top=top))
        lines = warnings.lines()
        if lines:
            typer.echo()
            for line in lines:
                typer.echo(f"⚠ {line}")
    if fail_over is not None and result.total.cost > fail_over:
        typer.echo(
            f"total ${result.total.cost:,.4f} exceeds --fail-over ${fail_over:,.4f}", err=True
        )
        raise typer.Exit(EXIT_BUDGET)


@app.command()
def diff(
    a: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Baseline export.")],
    b: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="New export.")],
    fmt: FormatOpt = "text",
    prices: PricesOpt = None,
    top: Annotated[int, typer.Option("--top", help="Rows to print in text mode.")] = 20,
    fail_over: FailOverOpt = None,
) -> None:
    """Compare two exports by root-span name: cost, calls, tokens, model mix."""
    _check_format(fmt)
    export_a, export_b = _load([a]), _load([b])
    table = _table(prices)
    ra = rollup(export_a, price_export(export_a, table))
    rb = rollup(export_b, price_export(export_b, table))
    result = diff_rollups(ra, rb)
    if fmt == "json":
        typer.echo(diff_json(result, table))
    else:
        typer.echo(f"tracecost {__version__} · {a} → {b} · prices {table.snapshot_date}")
        typer.echo()
        typer.echo(diff_table(result, top=top))
    if fail_over is not None and result.total.cost_b > fail_over:
        typer.echo(
            f"total ${result.total.cost_b:,.4f} exceeds --fail-over ${fail_over:,.4f}", err=True
        )
        raise typer.Exit(EXIT_BUDGET)


@app.command()
def prices(
    model: Annotated[str | None, typer.Argument(help="Model name to look up.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Provider hint.")] = None,
    prices: PricesOpt = None,
    at: Annotated[str | None, typer.Option("--at", help="Date for dated prices.")] = None,
    service_tier: Annotated[str | None, typer.Option("--service-tier")] = None,
) -> None:
    """Show the price snapshot, or the rates that would apply to one model."""
    table = _table(prices)
    typer.echo(f"snapshot {table.snapshot_date} · {table.source} · {table.model_count} models")
    if model is None:
        return
    match = table.find(model, provider)
    if match is None:
        typer.echo(f"no price for model {model!r}", err=True)
        raise typer.Exit(1)
    when = _parse_at(at) or dt.datetime.now(dt.UTC)
    rates, notes = resolve_rates(match.model, when, service_tier)
    typer.echo(f"{model} → {match.provider_id}/{match.model_id}")
    for key, value in rates.items():
        typer.echo(f"  {key:<28} {value}")
    for note in notes:
        typer.echo(f"  ⚠ {note}")


def run() -> None:  # pragma: no cover - console entry
    sys.exit(app())
