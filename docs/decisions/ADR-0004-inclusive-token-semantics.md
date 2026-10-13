# ADR-0004: One token model: inclusive input and output counts

## Context
Dialects disagree. The OpenTelemetry GenAI registry says `gen_ai.usage.input_tokens` *includes*
cache reads and writes and `gen_ai.usage.output_tokens` *includes* reasoning tokens. Langfuse
stores *exclusive* buckets (`input` excludes `input_cached_tokens`; `output` excludes
`output_reasoning_tokens`). Anthropic's raw API `input_tokens` excludes cache tokens; OpenAI's
`prompt_tokens` includes them. Getting this wrong double-counts or under-counts cache tokens,
which is exactly the kind of error mlflow#22745 and ollama#15758 are about.

## Decision
`Usage` follows the OpenTelemetry definition: `input_tokens` and `output_tokens` are totals, and
`cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` are the detailed subsets. Readers
convert on the way in (the Langfuse reader adds the detail buckets back). Pricing derives
`uncached_input_tokens = input - cache_read - cache_write` and `plain_output = output -
reasoning`. `cache_reported` records whether the exporter wrote any cache attribute, so zero
cache can be flagged as "possibly not reported".

## Consequences
Exporters that write OTel attributes with Anthropic's exclusive semantics (the registry notes
instrumentations must add them up) will be under-priced on cache; the FAQ documents how to
check with the JSON output. Reasoning tokens bill at the output rate unless a model lists
`output_reasoning_mtok`.
