# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Read gzip-compressed OTLP and Langfuse JSON/JSONL exports, including files detected by gzip magic bytes.

## [0.1.0] - 2026-10-13

### Added

- `tracecost cost FILES…`: prices every LLM call in OTLP JSON/JSONL, Phoenix spans JSONL or
  Langfuse observation exports and rolls the cost up `--by root|trace|session|user|model`,
  with `--format text|json`, `--prices` overrides, `--recompute`, `--at`, `--top` and
  `--fail-over USD` (exit 3).
- `tracecost diff A B`: compares two exports by root-span name (cost, LLM calls, tool calls,
  tokens, model mix) and marks rows added, removed, changed or same.
- `tracecost prices [MODEL]`: shows the price snapshot and the rates a model name resolves to,
  including tiers, dated changes and service-tier variants.
- Readers for OpenTelemetry GenAI attributes (`gen_ai.*`, with aliases for the deprecated
  `gen_ai.system`, the pre-2024 `prompt_tokens`/`completion_tokens` names and the
  `cache_creation` spelling), OpenInference `llm.*` attributes, Phoenix `get_spans_dataframe`
  exports and Langfuse `observations` / `traces` API responses.
- Pricing from a bundled, dated snapshot of pydantic/genai-prices v2 (MIT): per-million rates,
  threshold tiers, dated price changes selected by span start time, OpenAI service tiers,
  cache read/write and reasoning tokens. Unknown models are counted as $0 with a warning.
- Supplied costs (`llm.cost.total`, `cost.amount`/`cost.currency`/`cost.source`, Langfuse
  `costDetails`) are used first and labelled; the token-table figure is kept for auditing.
- Warnings for unknown models, generations with no cache attributes on a cache-billing model,
  supplied-vs-table disagreement and unknown or not-yet-in-force service tiers.
- Library API: `tracecost.load`, `tracecost.prices.price_export`, `tracecost.rollup.rollup`,
  `tracecost.diff.diff`, `tracecost.report.*`.
- Examples reproducing langfuse#16077, langfuse#13780, phoenix#13655, mlflow#22745 /
  ollama#15758 and phoenix discussion 14736, plus a reproducible demo export.

[Unreleased]: https://github.com/thanatchon36/tracecost/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thanatchon36/tracecost/releases/tag/v0.1.0
