"""Exercise private composition and Google publication at their real boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from e2e_doc_report import ENV, TEXT, Services
from e2e_invitation_shortlist import qualification, source_export
from e2e_invitation_shortlist import run as shortlist_run
from e2e_private_dm_actions import activity, inbox_row
from e2e_private_dm_actions import report as dm_report

from linkedin_mdp_mcp.google_doc_report import (
    GoogleDocConfig,
    GoogleDocPublisher,
    GoogleReportError,
)
from scripts import build_combined_action_report as build_cli
from scripts import publish_combined_action_report as publish_cli

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "https://www.linkedin.com/in/synthetic-person"


def invitation() -> dict[str, object]:
    """Return the selected fields in the current shortlist output shape."""
    return {
        "schema_version": 1, "status": "ready", "cap_scope": "all_history",
        "score_version": "invitation-priority-v1", "source_freshness": {"collected_at": "2026-10-02T10:00:00+00:00", "upstream_provider_generation": "unknown"},
        "research_queue": [], "withheld_counts": {}, "excluded_counts": {}, "invitations": [{
            "profile_url": PROFILE, "name": "Synthetic Person", "role": "Investor", "company_name": "Synthetic PVF",
            "score": 5.0, "score_version": "invitation-priority-v1", "reason": "US PVF fit",
            "action": "Review and send a LinkedIn invitation manually", "date_added": "2026-09-01T01:02:03+00:00",
            "date_added_source": {"source_ref": "prospects.rows[0].created_at", "prospect_id": "prospect-1", "reason": None},
            "score_factors": {"activity": {"value": True, "points": 4.0, "observed_at": "2026-10-01T00:00:00+00:00", "citations": [{"url": "https://evidence.example/activity", "source_date": "2026-10-01", "retrieved_at": "2026-10-02T00:00:00+00:00"}]}},
            "unknown_factors": {"mutual_connections": "not_observed_or_stale"},
            "evidence": {"role": [{"url": "https://evidence.example/role", "source_date": None, "retrieved_at": "2026-10-02T00:00:00+00:00"}]},
        }],
    }


def dm() -> dict[str, object]:
    """Return the direct queue and coverage shape emitted by the current DM planner."""
    return {"reply": [{
        "profile_url": "https://www.linkedin.com/in/synthetic-reply", "prospect_id": "prospect-2",
        "date_added": None, "date_added_source": "prospects.rows[1].created_at",
        "reason": "latest verified inbound DM after outbound", "message_at": "2026-10-02T09:00:00+00:00",
        "message": "Synthetic hello", "source_refs": ["inbox.rows[0]", "changelog.events[0]"],
        "action": "Owner writes reply", "read_state": "unknown",
    }], "follow_up": [], "first_dm": [{
        "profile_url": "https://www.linkedin.com/in/synthetic-first", "prospect_id": "prospect-3",
        "date_added": None, "date_added_source": "prospects.rows[2].created_at",
        "reason": "no observed prior DM in supplied snapshot; consent changelog is bounded",
        "connected_on": "2026-10-01", "connection_source": "connections.rows[0].Connected On",
        "draft": "I noticed your synthetic PVF work.",
        "citations": [{"fact": "Synthetic PVF work", "source_url": "https://evidence.example/fact"}],
        "action": "Owner reviews and sends first DM", "read_state": "unknown",
    }], "withheld": [{"profile_url": "https://www.linkedin.com/in/synthetic-unknown", "reason": "exact_cited_research_missing"}],
        "coverage": {"reasons": [], "upstream_freshness": "unknown", "changelog_scope": "bounded_28_days"}}


def private_file(path: Path, payload: object) -> None:
    """Write a synthetic owner-only JSON input."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def cli_case(inv: object, actions: object, *, expect_ok: bool) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Run the real offline command against owner-only files in a temporary directory."""
    base = Path(tempfile.mkdtemp(prefix="combined-report-e2e-"))
    base.chmod(0o700)
    private_file(base / "invitations.json", inv)
    private_file(base / "dm.json", actions)
    output = base / "output"
    process = subprocess.run([sys.executable, str(ROOT / "scripts/build_combined_action_report.py"),
        "--invitations", str(base / "invitations.json"), "--dm-actions", str(base / "dm.json"),
        "--output-dir", str(output)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert process.returncode == (0 if expect_ok else 1), process.stdout + process.stderr
    assert process.stdout.strip() == ("combined action report: written" if expect_ok else "combined action report: failed")
    assert process.stderr == ""
    return output, process


async def google_case(text: str, scenario: str = "complete") -> Services:
    """Publish prepared text through the synthetic Google OAuth, Drive, and Docs HTTP service."""
    service = Services(scenario=scenario)
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle)) as http:
        with patch.dict(os.environ, ENV):
            publisher = GoogleDocPublisher(GoogleDocConfig.from_env(), http=http)
            await publisher.publish_text(text)
    return service


def run_matrix() -> dict[str, str]:
    """Return fixed verdicts for the offline and HTTP behavior matrix."""
    from linkedin_mdp_mcp.combined_action_report import combine_action_reports

    verdicts: dict[str, str] = {}

    def check(name: str, condition: bool) -> None:
        """Record one assertion without retaining person-level data in evidence."""
        assert condition, name
        verdicts[name] = "passed"

    output, _ = cli_case(invitation(), dm(), expect_ok=True)
    body = (output / "combined-action-report.txt").read_text(encoding="utf-8")
    data = json.loads((output / "combined-action-report.json").read_text(encoding="utf-8"))
    check("private_cli_modes", stat.S_IMODE(output.stat().st_mode) == 0o700 and all(stat.S_IMODE((output / name).stat().st_mode) == 0o600 for name in ("combined-action-report.txt", "combined-action-report.json")))
    check("complete_sections_and_provenance", all(part in body for part in ("Synthetic Person", "Priority score", "activity", "mutual_connections", "prospects.rows[0].created_at", "https://evidence.example/role", "Owner writes reply", "Owner reviews and sends first DM", "https://evidence.example/fact", "exact_cited_research_missing", "unknown")))
    check("actual_dm_shape_accepted", data["status"] == "partial" and data["dm_actions"]["coverage"]["reasons"] == [])
    complete_dm = dm()
    complete_dm["withheld"] = []
    check("all_sections_complete", combine_action_reports(invitation(), complete_dm)["status"] == "complete")
    check("exact_trailing_newline", body.endswith("\n") and not body.endswith("\n\n"))
    producer_result = combine_action_reports(shortlist_run(source_export(), qualification()), dm_report([], []))
    check("actual_upstream_first_dm", len(producer_result["invitation_shortlist"]["invitations"]) == 1 and len(producer_result["dm_actions"]["first_dm"]) == 1 and "Researched first-DM drafts" in producer_result["text"])
    incoming = inbox_row(inbound=True)
    reply = dm_report([incoming], [activity(incoming, inbound=True)])
    reply_result = combine_action_reports(shortlist_run(source_export(), qualification()), reply)
    check("actual_upstream_reply", len(reply_result["dm_actions"]["reply"]) == 1 and "Owner writes reply" in reply_result["text"])
    media = inbox_row(hours=73, content="Synthetic attachment")
    media["ATTACHMENTS"] = [{"id": "synthetic-media-1"}]
    media_activity = activity(media)
    media_activity["activity"]["content"]["format"] = "MEDIA"
    media_activity["activity"]["attachments"] = [{"id": "synthetic-media-1"}]
    media_report = dm_report([media], [media_activity])
    media_result = combine_action_reports(shortlist_run(source_export(), qualification()), media_report)
    check("actual_upstream_media", len(media_result["dm_actions"]["follow_up"]) == 1 and "Synthetic attachment" in media_result["text"])
    blank_message = deepcopy(dm())
    blank_message["reply"][0]["message"] = ""
    check("empty_message_text_preserves_sources", "[attachment-only message; no text; inspect cited sources]" in combine_action_reports(invitation(), blank_message)["text"])
    with_counts = deepcopy(invitation())
    with_counts["withheld_counts"] = {"qualification_unknown": 2}
    with_counts["excluded_counts"] = {"existing_connection": 3}
    counted = combine_action_reports(with_counts, dm())
    check("withheld_counts_are_partial_and_visible", counted["status"] == "partial" and "qualification_unknown: 2" in counted["text"] and "existing_connection: 3" in counted["text"])
    for bad_value in (float("nan"), float("inf"), float("-inf")):
        malformed = deepcopy(invitation())
        malformed["source_freshness"]["age_seconds"] = bad_value
        cli_case(malformed, dm(), expect_ok=False)
    verdicts["nonfinite_json_refused"] = "passed"
    malformed_dm = deepcopy(dm())
    malformed_dm["first_dm"][0]["citations"] = []
    cli_case(invitation(), malformed_dm, expect_ok=False)
    verdicts["uncited_first_dm_refused"] = "passed"
    malformed_inv = deepcopy(invitation())
    malformed_inv["invitations"][0]["score_factors"]["activity"]["citations"] = []
    cli_case(malformed_inv, dm(), expect_ok=False)
    verdicts["uncited_known_factor_refused"] = "passed"
    hidden_surrogate = deepcopy(invitation())
    hidden_surrogate["not_rendered"] = "\ud800"
    out, _ = cli_case(hidden_surrogate, dm(), expect_ok=False)
    check("invalid_unicode_has_no_partial_artifacts", not out.exists())
    again = subprocess.run([sys.executable, str(ROOT / "scripts/build_combined_action_report.py"), "--invitations", str(output.parent / "invitations.json"), "--dm-actions", str(output.parent / "dm.json"), "--output-dir", str(output)], cwd=ROOT, capture_output=True, text=True, check=False)
    check("no_overwrite", again.returncode == 1 and again.stdout.strip() == "combined action report: failed" and (output / "combined-action-report.txt").read_text(encoding="utf-8") == body)

    bad = deepcopy(invitation())
    bad["schema_version"] = 2
    cli_case(bad, dm(), expect_ok=False)
    verdicts["unsupported_invitation_version"] = "passed"
    bad = deepcopy(invitation())
    bad["status"] = "withheld_current_employer_assignments"
    bad["research_queue"] = [{"profile_url": PROFILE, "reason": "employer_unknown", "source_ref": "invitations.rows[0]"}]
    bad["invitations"] = []
    partial = combine_action_reports(bad, dm())
    check("withheld_section_partial", partial["status"] == "partial" and "employer_unknown" in partial["text"] and "No invitation recommendations" in partial["text"])
    bad["invitations"] = invitation()["invitations"]
    cli_case(bad, dm(), expect_ok=False)
    verdicts["withheld_invitation_action_refused"] = "passed"
    bad = deepcopy(dm())
    bad["coverage"]["reasons"] = ["inbox_incomplete"]
    bad["reply"] = []
    bad["first_dm"] = []
    check("dm_coverage_partial", combine_action_reports(invitation(), bad)["status"] == "partial")
    bad["reply"] = dm()["reply"]
    cli_case(invitation(), bad, expect_ok=False)
    verdicts["dm_action_with_coverage_refused"] = "passed"
    bad = deepcopy(dm())
    bad["follow_up"] = [dict(bad["reply"][0])]
    cli_case(invitation(), bad, expect_ok=False)
    verdicts["cross_queue_conflict_refused"] = "passed"
    bad = deepcopy(invitation())
    bad["invitations"] = [deepcopy(bad["invitations"][0]) for _ in range(26)]
    cli_case(bad, dm(), expect_ok=False)
    verdicts["over_capacity_and_duplicate_refused"] = "passed"
    bad = deepcopy(invitation())
    bad["invitations"].append(deepcopy(bad["invitations"][0]))
    cli_case(bad, dm(), expect_ok=False)
    verdicts["duplicate_invitation_refused"] = "passed"
    bad = deepcopy(dm())
    bad["withheld"].append({"profile_url": bad["reply"][0]["profile_url"], "reason": "source_coverage_incomplete"})
    cli_case(invitation(), bad, expect_ok=False)
    verdicts["action_withheld_conflict_refused"] = "passed"
    bad = deepcopy(invitation())
    bad["invitations"][0]["name"] = "Unsafe [BEGIN AUTOMATED REPORT] value"
    cli_case(bad, dm(), expect_ok=False)
    verdicts["source_marker_injection_refused"] = "passed"
    research = deepcopy(invitation())
    research["status"] = "withheld_current_employer_assignments"
    research["invitations"] = []
    research["research_queue"] = [{"profile_url": None, "reason": "identity_unknown", "source_ref": "invitations.rows[0]"}]
    out, _ = cli_case(research, dm(), expect_ok=True)
    check("research_null_identity_unknown", "unknown | evidence:" in (out / "combined-action-report.txt").read_text(encoding="utf-8"))
    for url in ("https://attacker.example/person", "https://www.linkedin.com/in/synthetic-person?tracking=1", "https://www.linkedin.com/company/synthetic"):
        research["research_queue"][0]["profile_url"] = url
        out, _ = cli_case(research, dm(), expect_ok=False)
        assert not out.exists()
    verdicts["research_invalid_profile_refused"] = "passed"
    research["research_queue"][0].pop("profile_url")
    cli_case(research, dm(), expect_ok=False)
    verdicts["research_missing_profile_refused"] = "passed"
    verdicts.update(write_failure_scenarios())

    service = asyncio.run(google_case("Prepared 🧭 report\n"))
    check("google_utf16_marker_preservation", service.writes == 1 and service.text == TEXT.replace("old\n", "Prepared 🧭 report\n"))
    service = asyncio.run(google_case("Prepared report\n\n\n"))
    check("google_exact_one_trailing_newline", service.text == TEXT.replace("old\n", "Prepared report\n"))
    service = asyncio.run(google_case("Prepared report\n", "conflict"))
    check("google_revision_retry", service.attempts == 2 and service.permissions == 4)
    with tempfile.TemporaryDirectory() as tmp:
        report_file = Path(tmp) / "prepared.txt"
        report_file.write_text("Prepared CLI report\n", encoding="utf-8")
        report_file.chmod(0o600)
        service = Services()
        http = httpx.AsyncClient(transport=httpx.MockTransport(service.handle))
        stdout = io.StringIO()
        with patch.dict(os.environ, ENV), patch.object(publish_cli, "GoogleDocPublisher", side_effect=lambda config: GoogleDocPublisher(config, http=http)), contextlib.redirect_stdout(stdout):
            code = publish_cli.main(["--report", str(report_file)])
        check("google_only_publication_cli", code == 0 and service.writes == 1 and stdout.getvalue() == "combined action report publication: written\n")
        report_file.chmod(0o644)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = publish_cli.main(["--report", str(report_file)])
        check("publication_cli_refuses_public_file", code == 1 and stdout.getvalue() == "combined action report publication: failed\n")
    for name, text in (("empty", ""), ("spaces_only", "   "), ("newlines_only", "\n\n"), ("mixed_whitespace", " \t\n\u2003\u00a0"), ("begin_marker", "unsafe [BEGIN AUTOMATED REPORT] text"), ("end_marker", "[END AUTOMATED REPORT]"), ("nul", "a\x00b"), ("cr", "a\rb"), ("c1", "a\x85b"), ("bidi_override", "a\u202eb"), ("bidi_isolate", "a\u2066b"), ("surrogate", "a\ud800b")):
        service = Services()
        requests = 0

        def track(request: httpx.Request, service: Services = service) -> httpx.Response:
            """Count every synthetic HTTP request, including OAuth."""
            nonlocal requests
            requests += 1
            return service.handle(request)

        async def refused(text: str = text, name: str = name) -> None:
            """Assert unsafe prepared text fails before the first OAuth request."""
            async with httpx.AsyncClient(transport=httpx.MockTransport(track)) as http:
                publisher = GoogleDocPublisher(GoogleDocConfig("id", "secret", "refresh", "doc"), http=http)
                try:
                    await publisher.publish_text(text)
                except GoogleReportError:
                    return
                raise AssertionError(name)
        asyncio.run(refused())
        check("text_refuses_" + name, requests == 0)
    return verdicts


def write_failure_scenarios(scenarios: tuple[str, ...] = ("first_write_partial", "second_write_partial", "second_write_collision")) -> dict[str, str]:
    """Fail each real output write and preserve files not created by this invocation."""
    verdicts: dict[str, str] = {}
    original = build_cli._write
    for scenario in scenarios:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            private_file(base / "invitations.json", invitation())
            private_file(base / "dm.json", dm())
            output = base / "output"
            sibling = base / "owner-notes.txt"
            sibling.write_text("owner notes", encoding="utf-8")
            calls = 0

            def fail(path: Path, content: str, scenario: str = scenario) -> tuple[int, int]:
                """Exercise real partial-file Unicode failure or an exclusive-create collision."""
                nonlocal calls
                calls += 1
                if calls == (1 if scenario == "first_write_partial" else 2):
                    if scenario == "second_write_collision":
                        path.write_text("foreign output", encoding="utf-8")
                        path.chmod(0o600)
                        return original(path, content)
                    else:
                        return original(path, "\ud800")
                else:
                    return original(path, content)

            stdout = io.StringIO()
            with patch.object(build_cli, "_write", side_effect=fail), contextlib.redirect_stdout(stdout):
                code = build_cli.main(["--invitations", str(base / "invitations.json"), "--dm-actions", str(base / "dm.json"), "--output-dir", str(output)])
            assert code == 1 and stdout.getvalue() == "combined action report: failed\n"
            assert sibling.read_text(encoding="utf-8") == "owner notes"
            if scenario == "second_write_collision":
                assert (output / "combined-action-report.txt").read_text(encoding="utf-8") == "foreign output"
                assert not (output / "combined-action-report.json").exists()
            else:
                assert not output.exists(), scenario
            verdicts[scenario] = "passed"
    return verdicts


def main(path: Path | None = None) -> None:
    """Reset then write a fixed-verdict artifact for each synthetic run."""
    target = path or ROOT / "artifacts/combined-action-report-e2e-evidence.json"
    target.write_text('{"run_status":"running","scenarios":{}}\n', encoding="utf-8")
    try:
        verdicts = run_matrix()
    except BaseException:
        target.write_text('{"run_status":"failed","scenarios":{}}\n', encoding="utf-8")
        raise
    target.write_text(json.dumps({"run_status": "passed", "scenarios": verdicts}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"combined action report E2E: {len(verdicts)} passed")


def failure_artifact_scenario() -> None:
    """Prove a failed rerun replaces a stale passing artifact with failure evidence."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.json"
        path.write_text('{"run_status":"passed"}\n', encoding="utf-8")
        original = globals()["run_matrix"]

        def fail() -> dict[str, str]:
            """Force a synthetic failure after evidence reset."""
            raise AssertionError("synthetic failure")

        globals()["run_matrix"] = fail
        try:
            try:
                main(path)
            except AssertionError:
                pass
            else:
                raise AssertionError("forced failure did not fail")
        finally:
            globals()["run_matrix"] = original
        assert json.loads(path.read_text(encoding="utf-8"))["run_status"] == "failed"


if __name__ == "__main__":
    main()
