"""Run the existing inbox HTTP-boundary E2E through pytest and PR CI."""

import subprocess
import sys
from pathlib import Path


def test_inbox_http_boundaries() -> None:
    """Require the full E2E runner and its persisted verdict artifact to pass."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tests" / "e2e_inbox_sync.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
