from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "prospect_report" / "input.json"
GOLDEN = Path(__file__).parents[1] / "docs" / "assets" / "prospect-report-example.json"
AS_OF = "2026-09-30T12:00:00Z"


def _run(input_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the public file-to-file CLI exactly as an operator would."""
    env = os.environ.copy()
    src = str(Path(__file__).parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "linkedin_mdp_mcp.prospect_report",
            str(input_path),
            "--as-of",
            AS_OF,
            "--output",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def _load(path: Path) -> dict:
    """Read one JSON artifact for behavior assertions."""
    return json.loads(path.read_text())


def test_cli_resolves_atomic_concurrent_roles_and_channel_facts(tmp_path: Path) -> None:
    """Resolve cited roles without mixing fields and keep email/LinkedIn state separate."""
    output = tmp_path / "report.json"
    result = _run(FIXTURE, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    assert report["employment"]["status"] == "resolved"
    assert {
        (role["company"], role["title"])
        for role in report["employment"]["current_roles"]
    } == {("Beta Labs", "Senior ML Engineer"), ("Open Lab", "Advisor")}
    assert report["employment"]["supporting_validation_ids"] == ["v-active"]
    assert ("Acme", "Senior ML Engineer") not in {
        (role["company"], role["title"])
        for role in report["employment"]["alternative_roles"]
    }

    channels = {item["channel"]: item for item in report["channels"]}
    assert channels["email"]["sent"]["status"] == "known"
    assert channels["email"]["sent"]["claim_ids"] == ["email-sent"]
    assert channels["email"]["received"]["status"] == "unknown"
    assert channels["linkedin"]["sent"]["status"] == "known"
    assert channels["linkedin"]["sent"]["claim_ids"] == ["linkedin-invite-sent"]
    assert channels["linkedin"]["received"]["status"] == "unknown"


def test_cli_is_permutation_stable_and_matches_repeatable_artifact(tmp_path: Path) -> None:
    """Permuting evidence, claims and validations must produce the same canonical bytes."""
    original = _load(FIXTURE)
    permuted = dict(original)
    permuted["evidence"] = list(reversed(original["evidence"]))
    permuted["claims"] = list(reversed(original["claims"]))
    permuted["validations"] = list(reversed(original["validations"]))
    permuted_path = tmp_path / "permuted.json"
    permuted_path.write_text(json.dumps(permuted))

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_result = _run(FIXTURE, first)
    second_result = _run(permuted_path, second)

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == GOLDEN.read_bytes()


def test_unresolved_conflict_preserves_alternatives_and_requests_research(tmp_path: Path) -> None:
    """Without applicable cited resolution, conflicting current roles require review."""
    payload = _load(FIXTURE)
    payload["validations"] = [payload["validations"][0]]
    input_path = tmp_path / "unresolved.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    assert report["employment"]["status"] == "needs_review"
    assert report["employment"]["current_roles"] == []
    assert {
        (role["company"], role["title"])
        for role in report["employment"]["alternative_roles"]
    } == {
        ("Acme", "ML Engineer"),
        ("Beta Labs", "Senior ML Engineer"),
        ("Open Lab", "Advisor"),
    }
    assert report["research_requests"][0]["scope"] == "current_employment"
    assert set(report["research_requests"][0]["claim_ids"]) == {
        "job-mdp",
        "job-profile",
        "job-advisor",
    }


def test_supported_validation_does_not_adopt_uncited_equal_fact_provenance(tmp_path: Path) -> None:
    """Keep equal-looking uncited observations out of the resolved role provenance."""
    payload = _load(FIXTURE)
    copied_evidence = dict(payload["evidence"][1])
    copied_evidence.update(
        {
            "id": "e-job-copy",
            "delivery": "apollo",
            "record_id": "person:copy",
            "citation": "apollo://person/copy",
        }
    )
    payload["evidence"].append(copied_evidence)
    payload["claims"].append(
        {
            "kind": "employment",
            "id": "job-copy",
            "subject_url": payload["target"]["linkedin_url"],
            "role_key": "apollo:copy",
            "company": "Beta Labs",
            "title": "Senior ML Engineer",
            "status": "current",
            "effective_at": copied_evidence["effective_at"],
            "ended_at": None,
            "evidence_ids": ["e-job-copy"],
        }
    )
    input_path = tmp_path / "equal-fact.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    beta = next(
        role
        for role in report["employment"]["current_roles"]
        if role["company"] == "Beta Labs"
    )
    assert beta["claim_ids"] == ["job-profile"]
    assert beta["evidence_ids"] == ["e-job-profile"]
    assert any(claim["id"] == "job-copy" for claim in report["claims"])


def test_supported_validation_must_cite_each_selected_claim(tmp_path: Path) -> None:
    """Reject a supported role set when its validation omits a selected claim's evidence."""
    payload = _load(FIXTURE)
    active = next(item for item in payload["validations"] if item["id"] == "v-active")
    active["evidence_ids"] = ["e-job-advisor"]
    input_path = tmp_path / "missing-claim-citation.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    assert report["employment"]["status"] == "needs_review"
    assert report["employment"]["current_roles"] == []
    assert {
        (item["code"], tuple(item["ids"]))
        for item in report["review"]
    } >= {("validation_missing_claim_evidence", ("v-active",))}


def test_invalid_identity_or_duplicate_id_fails_without_leaking_or_partial_write(
    tmp_path: Path,
) -> None:
    """Reject unsafe identity/ID input while preserving an existing output atomically."""
    payload = _load(FIXTURE)
    payload["evidence"][0]["raw"]["secret"] = "DO-NOT-LEAK-THIS"
    payload["claims"][0]["subject_url"] = "https://www.linkedin.com/in/different-person"
    payload["evidence"].append({**payload["evidence"][0], "raw": {"Company": "Other"}})
    input_path = tmp_path / "invalid.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"
    output.write_text("existing-safe-artifact\n")

    result = _run(input_path, output)

    assert result.returncode != 0
    assert "DO-NOT-LEAK-THIS" not in result.stderr
    assert output.read_text() == "existing-safe-artifact\n"


def test_plans_absence_and_overlapping_times_do_not_create_false_state(tmp_path: Path) -> None:
    """Planned actions stay non-events, absence stays unknown, and overlap has no fake latest."""
    payload = _load(FIXTURE)
    payload["claims"] = [
        {
            "kind": "message",
            "id": "email-plan",
            "subject_url": payload["target"]["linkedin_url"],
            "channel": "email",
            "event": "planned",
            "occurred_at": None,
            "evidence_ids": ["e-email-draft"],
        },
        {
            "kind": "message",
            "id": "linkedin-send-a",
            "subject_url": payload["target"]["linkedin_url"],
            "channel": "linkedin",
            "event": "sent",
            "occurred_at": {
                "earliest": "2026-09-28T15:00:00Z",
                "latest": "2026-09-28T16:00:00Z",
                "precision": "instant",
            },
            "evidence_ids": ["e-linkedin-invite"],
        },
        {
            "kind": "message",
            "id": "linkedin-send-b",
            "subject_url": payload["target"]["linkedin_url"],
            "channel": "linkedin",
            "event": "sent",
            "occurred_at": {
                "earliest": "2026-09-28T15:30:00Z",
                "latest": "2026-09-28T16:30:00Z",
                "precision": "instant",
            },
            "evidence_ids": ["e-linkedin-invite"],
        },
    ]
    payload["validations"] = []
    input_path = tmp_path / "channels.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    channels = {item["channel"]: item for item in report["channels"]}
    assert channels["email"]["sent"]["status"] == "unknown"
    assert channels["email"]["received"]["status"] == "unknown"
    assert channels["linkedin"]["sent"]["status"] == "known"
    assert channels["linkedin"]["sent"]["latest"] is None

def test_time_windows_do_not_establish_facts_before_latest_bound(tmp_path: Path) -> None:
    """Keep uncertain events, roles and cited evidence unresolved until their latest bound."""
    payload = _load(FIXTURE)
    email = next(item for item in payload["claims"] if item["id"] == "email-sent")
    email["occurred_at"] = {
        "earliest": "2026-09-30T11:00:00Z",
        "latest": "2026-09-30T13:00:00Z",
        "precision": "instant",
    }
    profile = next(item for item in payload["claims"] if item["id"] == "job-profile")
    profile["effective_at"] = {
        "earliest": "2026-09-30T11:00:00Z",
        "latest": "2026-09-30T13:00:00Z",
        "precision": "instant",
    }
    input_path = tmp_path / "uncertain-claim.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "uncertain-claim-report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    channels = {item["channel"]: item for item in report["channels"]}
    assert channels["email"]["sent"]["status"] == "unknown"
    assert report["employment"]["status"] == "needs_review"

    payload = _load(FIXTURE)
    evidence = next(item for item in payload["evidence"] if item["id"] == "e-job-profile")
    evidence["effective_at"] = {
        "earliest": "2026-09-30T11:00:00Z",
        "latest": "2026-09-30T13:00:00Z",
        "precision": "instant",
    }
    input_path = tmp_path / "uncertain-evidence.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "uncertain-evidence-report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    assert report["employment"]["status"] == "needs_review"
    assert {
        (item["code"], tuple(item["ids"]))
        for item in report["review"]
    } >= {("validation_evidence_future", ("v-active",))}


def test_supported_partial_role_requires_research(tmp_path: Path) -> None:
    """Keep a cited but incomplete role unresolved instead of presenting it as current."""
    payload = _load(FIXTURE)
    profile = next(item for item in payload["claims"] if item["id"] == "job-profile")
    profile["title"] = None
    input_path = tmp_path / "partial-role.json"
    input_path.write_text(json.dumps(payload))
    output = tmp_path / "report.json"

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    report = _load(output)
    assert report["employment"]["status"] == "needs_review"
    assert {
        (item["code"], tuple(item["ids"]))
        for item in report["review"]
    } >= {("validation_incomplete_role", ("v-active",))}


def test_nonstandard_linkedin_profile_urls_are_rejected(tmp_path: Path) -> None:
    """Reject credentials and ports at the identity boundary."""
    for index, url in enumerate(
        (
            "https://linkedin.com:443/in/alice-example",
            "https://user@linkedin.com/in/alice-example",
        )
    ):
        payload = _load(FIXTURE)
        payload["target"]["linkedin_url"] = url
        input_path = tmp_path / f"invalid-url-{index}.json"
        input_path.write_text(json.dumps(payload))
        output = tmp_path / f"invalid-url-{index}-report.json"

        result = _run(input_path, output)

        assert result.returncode != 0
        assert not output.exists()

