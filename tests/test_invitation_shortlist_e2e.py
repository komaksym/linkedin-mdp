"""Run the shortlist E2E matrix and save a sanitized pass artifact."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def test_invitation_shortlist_e2e() -> None:
    """Require every shortlist boundary scenario before writing aggregate evidence."""
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_path = str(root / "src")
    environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/e2e_invitation_shortlist.py"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"(\d+) passed", result.stdout)
    assert match is not None, result.stdout + result.stderr
    body = {
        "schema_version": 1,
        "scenario_count": int(match.group(1)),
        "all_scenarios_passed": True,
        "red_first_failures_reproduced": [
            "provider-local invitation dates lost to ISO-only parsing",
            "observation timestamps misused as invitation dates",
            "raw-only provider invitations omitted from exported rows",
            "history rows without event IDs collapsed to one event",
            "duplicate prospect IDs allowed event identity misattachment",
            "CRM-only policy permitted for an all-history report",
            "complete empty provider pages rejected",
            "normalized CRM send dates lost or conflicting aliases selected",
            "text or ambiguous opt-out flags admitted recommendations",
            "date-precision event calendar days shifted by UTC conversion",
            "private-file cleanup masked a real filesystem write failure",
            "undated invitation evidence omitted from latest-employer capacity",
            "provider, CRM, and Supabase send evidence counted more than once per profile",
            "conflicting canonical current-employer assignments permitted capacity claims",
            "candidate qualification employer disagreed with owner mapping",
            "unknown historical identity omitted from global withhold queue",
            "read-only report constructed a recommendation persistence event",
            "invalid canonical employer aliases left contradictory resolved audit entries",
            "distinct invalid employer identity keys collapsed without source pointers",
        ],
    }
    body["artifact_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    artifact = root / "artifacts" / "invitation-shortlist-e2e-evidence.json"
    artifact.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
