#!/usr/bin/env python3
"""Regenerate src/tracecost/data/prices.json from pydantic/genai-prices (MIT).

    uv run python scripts/update_prices.py                 # download the current upstream file
    uv run python scripts/update_prices.py --from FILE     # use an already-downloaded copy
    uv run python scripts/update_prices.py --commit SHA    # record the upstream commit you used

The snapshot keeps, per provider: id, name, model_match, provider_match; per model: id, match,
prices, price_variants, deprecated. Extractors, API patterns and free-text are dropped. Runtime
code never touches the network; this script is the only thing that does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

UPSTREAM = (
    "https://raw.githubusercontent.com/pydantic/genai-prices/main/prices/new_data/v2/data_slim.json"
)
OUT = Path(__file__).resolve().parents[1] / "src" / "tracecost" / "data" / "prices.json"
PROVIDER_KEYS = ("id", "name", "model_match", "provider_match")
MODEL_KEYS = ("id", "match", "prices", "price_variants", "deprecated")


def trim(providers: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for provider in providers:
        slim = {k: provider[k] for k in PROVIDER_KEYS if k in provider}
        models = provider.get("models")
        slim["models"] = [
            {k: m[k] for k in MODEL_KEYS if k in m}
            for m in (models if isinstance(models, list) else [])
        ]
        out.append(slim)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--from", dest="source", type=Path, help="local copy of data_slim.json")
    parser.add_argument("--commit", default=None, help="upstream commit sha to record")
    parser.add_argument(
        "--date", default=dt.date.today().isoformat(), help="snapshot date (YYYY-MM-DD)"
    )
    args = parser.parse_args()

    if args.source:
        raw = args.source.read_bytes()
    else:
        with urllib.request.urlopen(UPSTREAM, timeout=60) as response:
            raw = response.read()
    providers = json.loads(raw)
    if not isinstance(providers, list):
        print("unexpected upstream shape: expected a list of providers", file=sys.stderr)
        return 1
    snapshot = {
        "snapshot_date": args.date,
        "source": UPSTREAM,
        "upstream_commit": args.commit,
        "license": "MIT (c) Pydantic Services Inc.; see https://github.com/pydantic/genai-prices",
        "providers": trim(providers),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, separators=(",", ":"), sort_keys=False) + "\n")
    models = sum(len(p["models"]) for p in snapshot["providers"])  # type: ignore[index]
    size_kb = OUT.stat().st_size // 1024
    print(f"wrote {OUT} ({size_kb} KB, {len(providers)} providers, {models} models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
