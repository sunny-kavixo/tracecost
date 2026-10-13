# Quickstart

tracecost reads trace exports you already have and prices every LLM call in them. Nothing
leaves your machine.

## 1. Install

```bash
uvx tracecost --version        # run without installing
pip install tracecost          # or install
```

Python 3.11 or newer.

## 2. Export your traces

Pick whichever you have.

**OpenTelemetry (any backend).** Point an OTLP file exporter at a file. With the Collector:

```yaml
exporters:
  file:
    path: /var/log/otel/traces.jsonl
service:
  pipelines:
    traces: {receivers: [otlp], exporters: [file]}
```

With the Python SDK, `ConsoleSpanExporter(out=open("traces.jsonl", "w"))` writes one span
per line in the same JSON encoding. Spans need the GenAI attributes (`gen_ai.usage.*`,
`gen_ai.request.model`) that OpenLLMetry, the OpenAI/Anthropic instrumentations and most
agent frameworks already emit.

**Langfuse.** Save the public API response to a file (paginate until `meta.totalPages`):

```bash
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "https://cloud.langfuse.com/api/public/observations?limit=100&page=1" > observations.json
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "https://cloud.langfuse.com/api/public/traces?limit=100&page=1" > traces.json
```

Load both so session and user ids come along: `tracecost cost observations.json traces.json`.

**Phoenix.** From the client:

```python
from phoenix.client import Client

df = Client().spans.get_spans_dataframe(project_identifier="my-app")
df.to_json("spans.jsonl", orient="records", lines=True)
```

## 3. Price it

```console
$ tracecost cost traces.jsonl --by trace
$ tracecost cost traces.jsonl --by session
$ tracecost cost traces.jsonl --by model --format json > cost.json
```

Dimensions: `root`, `trace`, `session`, `user`, `model`. The text report ends with warnings:
models the price table doesn't know, generations with no cache attributes on a model that
bills cache, and supplied costs that disagree with the token table.

## 4. Compare two runs

```console
$ tracecost diff before.jsonl after.jsonl
```

Root spans are matched by name; the table shows calls, tokens and cost on both sides and
which models moved.

## 5. Your own prices

Self-hosted or fine-tuned models aren't in any public table. Give tracecost a file:

```json
{
  "providers": [
    {
      "id": "internal",
      "provider_match": {"contains": "internal"},
      "models": [
        {
          "id": "qwen3-32b-finetune",
          "match": {"starts_with": "internal/qwen3"},
          "prices": {"input_mtok": 0.20, "output_mtok": 0.60}
        }
      ]
    }
  ]
}
```

```console
$ tracecost cost traces.jsonl --prices my-prices.json
$ tracecost prices internal/qwen3-32b-finetune --prices my-prices.json
```

Models in your file replace the bundled entry with the same `id`; new ones are added. The
format is the one pydantic/genai-prices uses, so its `prices/providers/*.yml` files are a
reference for tiers, dated changes and service-tier variants.

## 6. Budgets in CI

```console
$ tracecost cost eval-run.jsonl --fail-over 2.50   # exit 3 if the run cost more than $2.50
```
