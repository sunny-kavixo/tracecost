# Concepts

## Generation

One LLM (or embedding) call. Every reader produces the same `Generation`: trace, span and
parent ids, model, provider, a `Usage`, an optional `SuppliedCost`, session and user ids, and
the service tier. Everything downstream (pricing, rollups, diff, report) only sees this shape,
so a new input format is a new reader and nothing else.

## Usage: inclusive counts

`Usage.input_tokens` is the whole prompt including cache reads and writes; `output_tokens` is
the whole completion including reasoning tokens. That is how the OpenTelemetry GenAI registry
defines `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`. The detailed counts
(`cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`) are subsets.

Langfuse stores the opposite (`input` excludes `input_cached_tokens`), so its reader adds the
buckets back. See ADR-0004.

`cache_reported` is true when the exporter wrote *any* cache attribute. A generation with zero
cache tokens and `cache_reported=False` on a model that bills cache gets a warning: the cache
may have been used and not reported (ollama#15758, mlflow#22745).

## Span and root

Every span in the export is kept, generation or not, so cost can be rolled up to the root of
each trace. A root is a span whose parent is absent from the export (no parent, or a parent id
the file doesn't contain). Langfuse root `SPAN`s and OTel `invoke_agent` spans carry no usage
themselves; their cost is the sum over the generations beneath them (langfuse#16077).

## Price table

A JSON file with providers and models (the pydantic/genai-prices v2 shape). A model entry has
a `match` clause (`equals`, `starts_with`, `contains`, `regex`, `or`, `and`) and `prices`:

| key | meaning |
|---|---|
| `input_mtok` | USD per million uncached input tokens |
| `cache_read_mtok` | per million tokens read from the cache |
| `cache_write_mtok` | per million tokens written to the cache |
| `output_mtok` | per million output tokens (reasoning included unless `output_reasoning_mtok` exists) |

A rate can be a number or `{"base": …, "tiers": [{"start": N, "price": …}]}`: once the
generation's total input tokens exceed `start`, the tier price applies to *all* tokens of that
type (the "cliff" model the big providers use). `prices` can also be a list of
`{"constraint": {"start_date": …}, "prices": …}` entries, and the last one in force at the
span's start time wins. `price_variants` overlay rates for a `service_tier`.

Model lookup tries the provider hint first (`gen_ai.provider.name`, `llm.provider`), then
providers whose `model_match` fits the name, then everything else. Lookup is case-insensitive.

## Cost

For each generation: `uncached_input × input_mtok + cache_read × cache_read_mtok + cache_write ×
cache_write_mtok + plain_output × output_mtok + reasoning × (output_reasoning_mtok or
output_mtok)`, all per million. The breakdown is kept per generation and appears in the JSON
output.

Three situations are never silent:

- **unknown model** → `$0`, counted, and a warning naming the model;
- **cache tokens on a model with no cache rate** → billed at the input rate, with a note;
- **supplied cost** → used as-is with its source label, and the table figure is kept alongside
  (`recomputed_cost` in JSON, the "token table says" warning in text). `--recompute` flips the
  default.

## Rollup

Sums over generations keyed by root span, trace, session, user or model. Tool calls (OTel
`execute_tool`, OpenInference `TOOL`, Langfuse `TOOL`) are counted per root and per trace so a
diff can show fan-out. No top-N cutoff; `--top` only limits what the text table prints.

## Diff

Two rollups compared by root-span *name*, because ids differ between runs. Rows are
`added`, `removed`, `changed` or `same`, sorted by absolute cost delta. The model section shows
how many calls each model made on each side.
