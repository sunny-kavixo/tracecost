# ADR-0001: Name the project `tracecost`

## Context
The tool recomputes LLM spend from exported traces. The maintainer's other tools use short,
lowercase compound names that say what they do (verdrift, layerdelta, lastgreen, upfront).

## Decision
`tracecost`. Candidates checked on 2026-10-13 (GitHub `thanatchon36/<name>`, `pip index versions`,
`npm view`, GitHub repo search, web search with "github"/"cli"):

| Name | GitHub (own) | PyPI | npm | Notable clash |
|---|---|---|---|---|
| **tracecost** | free | free | free | none (web search finds no project by that name) |
| spancost | free | free | free | none, but "span" means less to Langfuse users |
| tracetab | free | free | free | none, but "tab" is ambiguous (bill vs. table vs. key) |
| costledger | free | free | free | a Shopify app "CostLedger" and `pi-token-cost-ledger` |
| spendtrace | free | **taken** | free | — |
| tokentab | free | **taken** | **taken** | — |

## Consequences
"tracecost" reads as "cost of traces" as well as "cost from traces". The README's first line
removes the ambiguity, and the PyPI keywords pair it with "opentelemetry" and "langfuse".
