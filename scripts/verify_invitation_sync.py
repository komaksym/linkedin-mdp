"""Produce repeatable, privacy-safe JSON evidence for invitation reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


def main() -> int:
    """Run synthetic HTTP-boundary tests and write only aggregate verification data."""
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / "invitation_sync_verification.json"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_invitation_sync_e2e.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    match = (
        re.search(r"(\d+) passed", result.stdout) if result.returncode == 0 else None
    )
    report = {
        "suite": "tests/test_invitation_sync_e2e.py",
        "surface": "LinkedInMDPClient and SupabaseClient over httpx.MockTransport",
        "status": "passed" if result.returncode == 0 else "failed",
        "passed": int(match.group(1)) if match else 0,
        "exit_code": result.returncode,
        "contains_provider_data": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(output)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
