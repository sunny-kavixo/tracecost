# tracecost — Mission

> Recompute what your LLM traces cost, offline, from an OTLP, Langfuse or Phoenix export, with cache and reasoning tokens priced, and see where your observability tool's totals disagree.

Status: v0.1.0 shipped 2026-10-13 ([release](https://github.com/thanatchon36/tracecost/releases/tag/v0.1.0), [PyPI](https://pypi.org/project/tracecost/))

## Problem

Teams instrument their agents with OpenTelemetry and then can't trust the dollar figures their observability tool shows. Root spans carry no usage, so list views show $0.00. Per-user and per-session rollups miss rows. Price tables lag new service tiers and ignore cache reads, cache writes and reasoning tokens. Costs that aren't token-based (OCR, GPU time, tools) can't be added at all. And every platform speaks a different attribute dialect (OTel `gen_ai.*`, OpenInference `llm.*`, Langfuse `usageDetails`), so the same spans produce different totals in different tools, and nothing lets you re-price history after a table changes.

- [langfuse#16077 — v4 unified Tracing table cannot display trace total cost; root SPAN rows show $0.00](https://github.com/langfuse/langfuse/issues/16077) — 👍9 💬19, 2026-08-13 — cost lives only on child GENERATIONs; a second user confirmed it on self-hosted v4.11 via the public API
- [langfuse#18210 — User consumption "Total cost" only sums the top 20 users](https://github.com/langfuse/langfuse/issues/18210) — 👍6, 2026-10-05
- [langfuse#15109 — Session "Total cost" shows $0.00 when cost is model-calculated](https://github.com/langfuse/langfuse/issues/15109) — 👍4, 2026-07-15 — correct at every other layer, wrong at the session rollup
- [langfuse#13780 — incorrect OpenAI pricing for the "priority" service tier](https://github.com/langfuse/langfuse/issues/13780) — 👍4, 2026-05-21
- [ollama#15758 — Ollama's Cloud doesn't report number of cached tokens](https://github.com/ollama/ollama/issues/15758) — 👍21 💬8, 2026-04-23 — a generation with zero cache tokens may mean "no cache" or "not reported"
- [phoenix#13655 — Support custom span costs independent of LLM token pricing](https://github.com/Arize-ai/phoenix/issues/13655) — 👍2 💬6, 2026-06-08 — "Phoenix re-derives cost instead of trusting the value we already have"
- [mlflow#22745 — OTel GenAI ingest drops gen_ai.usage.cache_read / cache_creation input tokens](https://github.com/mlflow/mlflow/issues/22745) — closed/completed, 2026-04-21 — the cache blind spot is cross-vendor
- [phoenix discussion — whatbroke: diff what your agent did between two runs, from OTLP span exports](https://github.com/Arize-ai/phoenix/discussions/14736) — 💬3, 2026-07-25

The full evidence and gap check are in the pain-point report and `decision.md` in the research workspace.

## Target user

An engineer who runs LLM agents in production with OpenTelemetry instrumentation (OpenLLMetry, OpenInference, the Langfuse SDK, or hand-written `gen_ai.*` spans) and has to answer "what did this cost, per trace, per session, per user, per model?" for a budget review, an incident, or a vendor comparison. They hit it monthly at least, and every time the number on the dashboard doesn't match the invoice.

A second user is the **platform maintainer** (Langfuse, Phoenix, MLflow, a home-grown pipeline) who wants an independent reference implementation to check their own rollups and price tables against.

## Before / after

**Before:** export observations through the public API, load them into a notebook, hand-write the cache-token arithmetic for each provider, look up today's prices on three pricing pages, join to traces for session and user ids, and re-do it the next time a price changes. Or trust the dashboard and get surprised by the invoice.

**After** (illustrative; real output is in the README):
```console
$ tracecost cost traces.jsonl --by trace
tracecost 0.1.0 · 412 generations · 38 traces · prices 2026-10-12

trace                              llm calls   input    cache-read   output   cost
chat-turn 7c1a…                           6   48,210     41,900       2,311   $0.1738
chat-turn 9e02…                           4   12,004          0       1,105   $0.0526
…
total                                   412  …                                $4.9120
⚠ 3 generations on model "my-finetune-v2" have no price (0 USD counted). Add it with --prices.
⚠ 17 generations report zero cache tokens on a provider that bills cache; verify the exporter.

$ tracecost diff run-a.jsonl run-b.jsonl
root span            calls   tokens          cost
plan                 5 → 5   31,002 → 29,880 $0.19 → $0.18
implement            9 → 14  88,410 → 141,200 $0.61 → $1.02  ▲
```

## Non-goals (v0.1)

- **Fetching from a Langfuse or Phoenix server.** v0.1 reads files you export. Reading from the public APIs needs keys and pagination logic and goes on the roadmap.
- **Live pricing lookups.** Prices ship as a dated data file (sourced from pydantic/genai-prices) with a documented update command. No network at runtime.
- **A dashboard or UI.** Output is a terminal table and JSON; load the JSON into DuckDB, pandas or a spreadsheet.
- **Latency, quality or eval metrics.** This tool is about tokens and money.
- **Rewriting or re-exporting spans.** It reads; it never modifies or sends telemetry.
- **Protobuf-binary OTLP.** JSON-encoded OTLP (the SDK and Collector file exporters) only; the protobuf dependency isn't worth it for v0.1.

## Architecture

```text
export files ──► readers ──► Generation records (one per LLM call, one dialect-neutral shape)
  OTLP JSON/JSONL        readers/otlp.py      gen_ai.* and OpenInference llm.* attributes
  Phoenix spans JSONL    readers/phoenix.py   flattened attributes.* columns
  Langfuse observations  readers/langfuse.py  usageDetails / costDetails
                                   │
                                   ▼
                         prices.py  (bundled dated table, tiered + service-tier + cache/reasoning)
                                   │  supplied cost wins unless --recompute
                                   ▼
                         rollup.py  (span tree → root span, trace, session, user, model)
                         diff.py    (two exports → per-root-name deltas)
                                   │
                                   ▼
                         report.py  → text table / JSON / exit code
```

- `src/tracecost/model.py` — `Generation`, `Usage`, `Cost` dataclasses; the only shape downstream code sees.
- `src/tracecost/readers/` — one module per input format; each returns `list[Generation]` plus the span tree. Attribute-name tables are data, not code paths, so a new dialect is a table entry.
- `src/tracecost/prices.py` — loads `src/tracecost/data/prices.json` (trimmed snapshot of genai-prices v2, MIT) and `--prices` overrides; model matching by the snapshot's match clauses; tiered "cliff" pricing; `start_date` constraints; `service_tier` variants.
- `src/tracecost/rollup.py`, `diff.py`, `report.py`, `cli.py` (typer, thin).
- `scripts/update_prices.py` — dev-time only; downloads the upstream data file and writes the trimmed snapshot with its date.
- External deps: `typer` (CLI). Everything else is the standard library. No provider SDKs: the tool never calls an LLM.

## MVP features → acceptance tests

| # | Feature | Acceptance test | Closes use case in |
|---|---|---|---|
| F1 | Read OTLP JSON and JSONL exports carrying OTel GenAI attributes, including `gen_ai.usage.cache_read.input_tokens` and `gen_ai.usage.cache_creation.input_tokens` | `tests/test_f1_otlp.py::test_reads_generations_with_cache_tokens` — given a two-span OTLP file, when loaded, then one Generation has input/output/cache-read/cache-write counts and the trace/parent ids | [mlflow#22745](https://github.com/mlflow/mlflow/issues/22745) |
| F2 | Read OpenInference `llm.*` attributes (in OTLP or Phoenix spans JSONL) and Langfuse observation exports into the same `Generation` shape | `tests/test_f2_dialects.py::test_three_dialects_same_generation` — given the same call expressed in all three formats, when loaded, then the three Generations are equal | [phoenix discussion 13041](https://github.com/Arize-ai/phoenix/discussions/13041), [langfuse#16077](https://github.com/langfuse/langfuse/issues/16077) |
| F3 | Price every generation from the bundled dated table: input, output, cache read, cache write (5m/1h), reasoning at the output rate, tiered cliffs, `service_tier` variants; unknown models are flagged and counted, never priced $0 silently; `--prices` overrides and extends | `tests/test_f3_pricing.py::test_cache_and_tier_pricing`, `::test_unknown_model_is_flagged`, `::test_prices_override` | [langfuse#13780](https://github.com/langfuse/langfuse/issues/13780), [ollama#15758](https://github.com/ollama/ollama/issues/15758) |
| F4 | Roll up to root span, trace, session, user and model; totals come from children even when roots carry no usage; no top-N cutoff | `tests/test_f4_rollup.py::test_root_span_gets_children_cost`, `::test_session_and_user_totals_count_every_row` | [langfuse#16077](https://github.com/langfuse/langfuse/issues/16077), [#18210](https://github.com/langfuse/langfuse/issues/18210), [#15109](https://github.com/langfuse/langfuse/issues/15109), [#14889](https://github.com/langfuse/langfuse/issues/14889) |
| F5 | Honour caller-supplied cost (OpenInference `llm.cost.*`, generic `cost.amount`/`cost.currency`/`cost.source`, Langfuse `costDetails`) before the table, labelled by source; `--recompute` prices from tokens instead and reports the delta | `tests/test_f5_supplied_cost.py::test_supplied_cost_wins_and_is_labelled`, `::test_recompute_reports_delta` | [phoenix#13655](https://github.com/Arize-ai/phoenix/issues/13655) |
| F6 | `diff A B`: per-root-span-name and total deltas in cost, tokens, LLM calls, tool calls and model mix | `tests/test_f6_diff.py::test_diff_per_root_name` | [phoenix discussion 14736](https://github.com/Arize-ai/phoenix/discussions/14736) |
| F7 | CLI: `cost` and `diff`, `--by trace\|session\|user\|model\|root`, `--format text\|json`, `--fail-over USD` exits 3, warnings for unknown models and zero-cache generations; examples named after the issues run in CI | `tests/test_f7_cli.py::test_cost_text_and_json`, `::test_fail_over_exit_code`, `tests/test_examples.py` | [duckdb discussion 25408](https://github.com/duckdb/duckdb/discussions/25408), [litellm discussion 32172](https://github.com/BerriAI/litellm/discussions/32172) |

## Success metrics

- Each cited issue's use case reproduces as a passing example in `examples/`: langfuse#16077 (root span $0.00 → correct rollup), phoenix#13655 (supplied cost honoured), mlflow#22745 / ollama#15758 (cache tokens priced and zero-cache flagged), langfuse#13780 (service-tier price), phoenix 14736 (diff).
- CI green on ubuntu + macos × Python 3.11–3.13; coverage ≥ 85%.
- `uvx tracecost cost examples/demo/traces.jsonl` works in a clean environment with no network.

## Roadmap

- **v0.2** — read directly from the Langfuse public API (`/api/public/v2/observations`, paginated) and from a Phoenix server, so the audit needs no manual export ([langfuse#16077](https://github.com/langfuse/langfuse/issues/16077) users want list-level totals without clicking into each trace).
- **v0.2** — protobuf-binary OTLP and gzip inputs.
- **v0.3** — `reconcile`: compare tracecost's totals with a provider usage/billing CSV export (OpenAI, Anthropic) for the same period and show the gap per model ([litellm discussion 32172](https://github.com/BerriAI/litellm/discussions/32172)).
- **v0.3** — non-token costs from span attributes for tools and retrieval (GPU-time, external APIs) in the same rollups ([phoenix#13655](https://github.com/Arize-ai/phoenix/issues/13655)).
- **later** — a GitHub Action that fails a PR when a recorded eval run's cost rises beyond a budget (`--fail-over` already exists).
