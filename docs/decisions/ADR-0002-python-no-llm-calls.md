# ADR-0002: Python, standard library plus typer, and no LLM calls

## Context
The tool reads exported spans and multiplies token counts by prices. The maintainer's brief
prefers Python, asks for boring pinned dependencies, and says the core path must work without
paid API keys.

## Decision
Python ≥ 3.11 with `typer` as the only runtime dependency. Parsing (JSON, JSON Lines), the
price table, rollups and the text table use the standard library. There is no provider
abstraction because tracecost never calls a model: it prices what the exporter already recorded.

## Consequences
No `pydantic`, `rich`, `pandas` or protobuf at runtime. Protobuf-binary OTLP is out of scope
for v0.1 (JSON encoding only), and the table output is plain text. `uvx tracecost` installs in
seconds and runs offline.
