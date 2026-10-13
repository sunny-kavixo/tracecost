"""Every example named after an issue runs clean, and the demo export is reproducible."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted(p for p in (ROOT / "examples").glob("*.py") if not p.name.startswith("_"))


@pytest.mark.parametrize("script", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_example_runs(script: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK:" in proc.stdout


def test_demo_export_is_reproducible(tmp_path: Path) -> None:
    out = tmp_path / "traces.jsonl"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "demo" / "make_demo.py"), str(out)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_text() == (ROOT / "examples" / "demo" / "traces.jsonl").read_text()
