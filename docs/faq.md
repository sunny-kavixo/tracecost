# FAQ

Each answer links the issue or thread that prompted it.

### My Langfuse trace list shows $0.00 for every root span. Is my data broken?

No. In Langfuse's v4 unified Tracing table the root observation of a trace is a `SPAN` with no
usage of its own; cost sits on the child `GENERATION`s and only appears in the trace detail view
([langfuse#16077](https://github.com/langfuse/langfuse/issues/16077)). Export the observations
and run `tracecost cost observations.json --by root` (or `--by trace`): each row is the sum of
the generations beneath it. `examples/langfuse_16077_root_span_zero.py` reproduces the case.

### The per-user total in Langfuse doesn't add up to my invoice.

The user-consumption widget sums only the top 20 users
([langfuse#18210](https://github.com/langfuse/langfuse/issues/18210)), and the session header
can show $0.00 when cost was model-derived
([langfuse#15109](https://github.com/langfuse/langfuse/issues/15109)). tracecost's `--by user`
and `--by session` count every row. Load the traces export alongside the observations so user
and session ids are present: `tracecost cost observations.json traces.json --by user`.

### OpenAI "priority" (or "flex") requests are priced like normal ones.

A table keyed on model alone can't know the tier
([langfuse#13780](https://github.com/langfuse/langfuse/issues/13780)). tracecost reads
`openai.response.service_tier` (or `openai.request.service_tier`, or Langfuse
`modelParameters.service_tier`) and applies the snapshot's `price_variants`. A tier the table
doesn't know, or one that wasn't in force on the span's date, is priced at the default rate and
noted. `examples/langfuse_13780_service_tier.py` shows all three outcomes.

### My cached tokens aren't making anything cheaper.

Two possibilities. (1) The exporter dropped the cache attributes, so every input token is billed
at the full rate: MLflow's OTel ingest did this
([mlflow#22745](https://github.com/mlflow/mlflow/issues/22745)) and Ollama's cloud reported 0
cached tokens for a while ([ollama#15758](https://github.com/ollama/ollama/issues/15758)).
tracecost warns when a model that bills cache has generations with no cache attributes at all.
(2) The attributes are there under a name tracecost doesn't read. Check the per-generation
`tokens` block in `--format json`; if `cache_read` is 0 while your raw span has a cache count,
open an issue with the attribute name. `examples/mlflow_22745_cache_tokens.py` shows the price
difference.

### I compute cost myself (GPU time, OCR, a vendor invoice). Phoenix ignores it.

Phoenix's `costSummary` re-derives cost from a registered model price and ignores a supplied
value ([phoenix#13655](https://github.com/Arize-ai/phoenix/issues/13655)). tracecost reads
OpenInference `llm.cost.total`, the generic `cost.amount` / `cost.currency` / `cost.source`
attributes and Langfuse `costDetails.total`, uses them first, labels the source, and keeps the
token-table figure alongside so you can see the gap. `--recompute` prices everything from tokens
instead. `examples/phoenix_13655_supplied_cost.py` covers both.

### Did my change make the agent more expensive?

`tracecost diff before.jsonl after.jsonl` matches root spans by name and shows calls, tokens,
cost and the model mix on both sides, the idea behind
[whatbroke](https://github.com/Arize-ai/phoenix/discussions/14736). Pair it with
`--fail-over` in CI to stop a regression at the pull request.
`examples/phoenix_14736_diff.py` is a complete run.

### How do I get cost per accepted task, including the failed attempts?

Use `--format json`; the `generations` array has one row per LLM call with trace, session,
user, model, tokens, cost and its source. Load it into DuckDB or pandas and join to your own
"accepted" table, as in the
[DuckDB discussion](https://github.com/duckdb/duckdb/discussions/25408):

```sql
SELECT t.task_id, sum(g.cost) AS cost
FROM read_json('cost.json')  -- then unnest(generations) …
```

### Which price did you use, and how old is it?

The report header prints the snapshot date and whether `--prices` was merged in. `tracecost
prices gpt-5` prints the exact rates (tiers, cache, dated changes) that a model name resolves
to. The snapshot comes from [pydantic/genai-prices](https://github.com/pydantic/genai-prices)
(MIT); regenerate it with `scripts/update_prices.py`.

### Do you support the OTel GenAI *events* or the old `gen_ai.usage.prompt_tokens` names?

Spans only; events carry message content, not usage. The pre-2024 names
`gen_ai.usage.prompt_tokens` / `completion_tokens` are read as aliases, as are the deprecated
`gen_ai.system` and the `gen_ai.usage.cache_creation.input_tokens` spelling.

### Does tracecost send anything anywhere?

No. It reads the files you name and the bundled price table, and writes to stdout. There is no
telemetry and no network call at runtime. See SECURITY.md.
