"""Run review regression E2E scenarios in PR CI."""

import subprocess
import sys
from pathlib import Path


def test_inbox_review_http_boundaries() -> None:
    """Require all synthetic HTTP boundary review verdicts to pass."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tests" / "e2e_inbox_review.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
