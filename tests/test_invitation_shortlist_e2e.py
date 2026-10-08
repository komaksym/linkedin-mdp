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

    collection = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/e2e_invitation_shortlist.py"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert collection.returncode == 0, collection.stdout + collection.stderr
    assert "test_candidate_citations_must_attest_exact_profile" in collection.stdout
    assert "test_legacy_baseline_private_cli_happy_case" in collection.stdout
    assert "test_legacy_baseline_malformed_same_name_row_still_collides" in collection.stdout
    assert "test_legacy_baseline_target_collisions_withhold_both" in collection.stdout
    required_present_employer_regressions = {
        "test_present_set_charges_each_company_once_and_keeps_flat_audit",
        "test_present_set_membership_does_not_invent_primary_employer",
        "test_conflicting_canonical_present_sets_are_not_unioned",
        "test_present_set_selection_requires_and_consumes_every_company_slot",
        "test_legacy_employer_rows_reject_completeness_metadata",
    }
    missing_regressions = sorted(
        test_name for test_name in required_present_employer_regressions if test_name not in collection.stdout
    )
    assert not missing_regressions, f"missing red-first regression coverage: {missing_regressions}"

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
            "unpaired Unicode employer keys failed before unresolved queue recording",
            "escaped surrogate snapshot metadata blocked complete matching raw and exported rows",
            "candidate qualification citation attested a different profile",
            "unique legacy baseline provider identity left unsupported",
            "Unicode-normalized legacy name left unsupported",
            "unrelated invitation event prevented the selected baseline binding",
            "legacy audit evidence remained unresolved despite a unique provider match",
            "resolved legacy identity retained unsupported rather than missing-employer reasons",
            "private legacy CLI happy case wrote a withheld report",
            "report omitted a valid exact-profile prospect creation timestamp",
            "invalid or future creation timestamps were not withheld as unknown",
            "creation timestamp or qualification spoof changed ranking or membership",
            "private Markdown omitted creation-time provenance",
            "UTC conversion overflow aborted timestamp enrichment",
            "private Markdown omitted the creation-time prospect ID",
            "escaped surrogate snapshot metadata blocked complete matching raw and exported rows",
            "explicit concurrent employers could not charge each historical company once",
            "complete employer sets could not admit qualified-company membership",
            "conflicting canonical sets lacked an explicit conflict reason",
            "secondary employer at capacity admitted an invitation",
            "legacy employer rows ignored incomplete coverage metadata",
        ],
    }
    body["artifact_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    artifact = root / "artifacts" / "invitation-shortlist-e2e-evidence.json"
    artifact.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
