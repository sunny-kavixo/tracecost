# ADR-0003: A dated, bundled price snapshot instead of live prices

## Context
Prices change (langfuse#13780 was a new OpenAI service tier that a table missed), and users
re-price history after a change. Live lookups would need the network at runtime and make two
runs on the same file disagree.

## Decision
Bundle `src/tracecost/data/prices.json`, a trimmed snapshot of pydantic/genai-prices v2
(MIT), with its `snapshot_date` and upstream commit recorded. `scripts/update_prices.py` is the
only code that touches the network. Users extend or override the snapshot with `--prices`.
Every report prints the snapshot date. Dated prices in the snapshot are selected by each
span's start time, so old exports are priced at the rate in force when the call happened.

## Consequences
A release may ship stale prices; the mitigation is the visible date, the override flag, and a
dependabot-style habit of regenerating the snapshot before each release. The snapshot is about
300 KB, which is acceptable in a wheel. genai-prices' own Python package is not a dependency:
its API is still moving and we need only the data.
