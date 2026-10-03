"""Exercise the offline DM report with synthetic provider-shaped evidence."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from linkedin_mdp_mcp.dm_actions import classify_dm_evidence, plan_dm_actions

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
ACCOUNT = "https://www.linkedin.com/in/owner"
PEER = "https://www.linkedin.com/in/person-a"
OWNER_URN = "urn:li:person:owner"
PEER_URN = "urn:li:person:person-a"
THREAD = "urn:li:messagingThread:synthetic-thread"
ROOT = Path(__file__).resolve().parents[1]


def inbox_row(*, inbound: bool = False, hours: int = 1, content: str = "Synthetic hello") -> dict[str, Any]:
    """Build one two-person INBOX observation."""
    sender, recipient = (PEER, ACCOUNT) if inbound else (ACCOUNT, PEER)
    return {
        "CONVERSATION ID": "synthetic-thread",
        "SENDER PROFILE URL": sender,
        "RECIPIENT PROFILE URLS": [recipient],
        "DATE": (NOW - timedelta(hours=hours)).isoformat(),
        "CONTENT": content,
        "ATTACHMENTS": [],
    }


def activity(row: dict[str, Any], *, inbound: bool = False, activity_id: str = "activity-1") -> dict[str, Any]:
    """Build a successful synthetic CREATE with matching owner and author roles."""
    author = PEER_URN if inbound else OWNER_URN
    occurred = datetime.fromisoformat(row["DATE"])
    return {
        "owner": OWNER_URN,
        "actor": author,
        "resourceName": "messages",
        "method": "CREATE",
        "activityStatus": "SUCCESS",
        "resourceId": activity_id,
        "activityId": "call-" + activity_id,
        "activity": {
            "id": activity_id,
            "owner": author,
            "author": author,
            "thread": THREAD,
            "createdAt": int(occurred.timestamp() * 1000),
            "content": {"format": "TEXT", "formatVersion": 1, "fallback": row["CONTENT"]},
            "attachments": [],
        },
    }


def snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep one complete collector snapshot with matching raw rows."""
    return {
        "source_result": "success",
        "truncated": False,
        "page_count": 1,
        "completed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "provider_generated_at": None,
        "raw_elements": [{"snapshotDomain": "INBOX", "snapshotData": deepcopy(rows)}],
        "rows": deepcopy(rows),
    }


def changelog(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a complete bounded consent-history envelope."""
    return {"events": deepcopy(events), "page_count": 1, "truncated": False, "next_start_time": 1 if events else None, "collected_at": (NOW - timedelta(minutes=5)).isoformat()}


def sources(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the two classifier input envelopes."""
    return snapshot(rows), changelog(events)


def policy() -> dict[str, Any]:
    """State explicit current qualification and recent acceptance for one profile."""
    return {"profiles": {PEER: {
        "accepted_at": (NOW - timedelta(days=2)).isoformat(),
        "country": "US",
        "pvf_employer": True,
        "qualification_citations": ["https://evidence.example/qualification"],
        "opt_out": False,
    }}}


def research() -> dict[str, Any]:
    """Return cited exact-profile saved research and an owner-reviewed opener."""
    return {"rows": [{
        "profile_url": PEER,
        "identity_match": {"canonical_profile_url_exact": True},
        "proposed_personalized_opener": "I noticed your synthetic PVF work.",
        "supporting_facts": [{"fact": "Synthetic PVF work", "source_url": "https://evidence.example/fact"}],
        "uncertainties": [],
    }]}


def report(rows: list[dict[str, Any]], events: list[dict[str, Any]], *, policy_data: dict[str, Any] | None = None, research_data: dict[str, Any] | None = None, inbox: dict[str, Any] | None = None, log: dict[str, Any] | None = None, connection_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the complete pure classification and planning boundary."""
    evidence = classify_dm_evidence(inbox or snapshot(rows), log or changelog(events), account_profile_url=ACCOUNT, account_member_urn=OWNER_URN, now=NOW)
    prospects = {"rows": [{"id": "prospect-a", "linkedin_url": PEER, "created_at": "2026-09-01T01:02:03+00:00", "attributes": {}}], "source_result": "success", "truncated": False, "page_count": 1, "row_count": 1, "consistency": "stable_count_and_unique_ids", "completed_at": (NOW - timedelta(minutes=5)).isoformat()}
    connected = connection_rows if connection_rows is not None else [{"URL": PEER, "Connected On": (NOW - timedelta(days=2)).date().isoformat()}]
    connections = {"rows": deepcopy(connected), "raw_elements": [{"snapshotDomain": "CONNECTIONS", "snapshotData": deepcopy(connected)}], "source_result": "success", "truncated": False, "page_count": 1, "completed_at": (NOW - timedelta(minutes=5)).isoformat()}
    return plan_dm_actions(evidence, prospects, connections, research_data if research_data is not None else research(), policy=policy_data if policy_data is not None else policy(), now=NOW)


def run_matrix() -> dict[str, str]:
    """Return fixed verdicts for the refusal and action scenarios."""
    verdicts: dict[str, str] = {}

    def check(name: str, condition: bool) -> None:
        """Record one synthetic assertion without person data."""
        if not condition:
            raise AssertionError(name)
        verdicts[name] = "passed"

    outbound = inbox_row(hours=73)
    incoming = inbox_row(inbound=True, hours=1)
    check("verified_outbound_followup", len(report([outbound], [activity(outbound)])["follow_up"]) == 1)
    check("verified_inbound_reply", len(report([outbound, incoming], [activity(outbound), activity(incoming, inbound=True, activity_id="activity-2")])["reply"]) == 1)
    note = activity(outbound)
    note["activity"]["extensionContent"] = {"contentRecordMap": {"InvitationMessageContent": {"text": outbound["CONTENT"]}}}
    check("invitation_note_not_dm", not report([outbound], [note])["follow_up"])
    check("valid_note_allows_first_dm", len(report([outbound], [note])["first_dm"]) == 1)
    malformed_note = deepcopy(note)
    malformed_note["activity"]["content"] = []
    malformed_result = report([outbound], [malformed_note])
    check("malformed_invitation_note_withheld", not any(malformed_result[key] for key in ("reply", "follow_up", "first_dm")) and malformed_result["withheld"])
    distinct_dm = activity(outbound, activity_id="activity-2")
    check("note_dm_same_row_note_first", not report([outbound], [note, distinct_dm])["follow_up"])
    check("note_dm_same_row_dm_first", not report([outbound], [distinct_dm, note])["follow_up"])
    forged_note = deepcopy(note)
    forged_note["resourceId"] = "other"
    check("unproved_note_blocks_first_dm", not report([outbound], [forged_note])["first_dm"])
    media_row = inbox_row(hours=73, content="Synthetic media")
    media_row["ATTACHMENTS"] = [{"id": "synthetic-media-1"}]
    media_event = activity(media_row)
    media_event["activity"]["content"]["format"] = "MEDIA"
    media_event["activity"]["attachments"] = [{"id": "synthetic-media-1"}]
    check("exact_media_attachment", len(report([media_row], [media_event])["follow_up"]) == 1)
    empty_media_row = deepcopy(media_row)
    empty_media_row["CONTENT"] = ""
    empty_media_event = deepcopy(media_event)
    empty_media_event["activity"]["content"]["fallback"] = ""
    check("attachment_only_empty_content_verified", len(report([empty_media_row], [empty_media_event])["follow_up"]) == 1)
    omitted_media_row = deepcopy(empty_media_row)
    omitted_media_row.pop("CONTENT")
    check("attachment_only_omitted_content_verified", len(report([omitted_media_row], [empty_media_event])["follow_up"]) == 1)
    empty_text_row = inbox_row(hours=73, content="")
    check("empty_text_without_attachment_unverified", not report([empty_text_row], [activity(empty_text_row)])["follow_up"])
    wrong_media = deepcopy(media_event)
    wrong_media["activity"]["attachments"] = [{"id": "synthetic-media-2"}]
    check("media_attachment_mismatch", not report([media_row], [wrong_media])["follow_up"])
    missing = deepcopy(outbound)
    missing.pop("CONTENT")
    missing["ATTACHMENTS"] = ["synthetic-attachment"]
    check("missing_content_attachment_unknown", not report([missing], [activity(outbound)])["follow_up"])
    unknown = activity(outbound)
    unknown["activity"]["extensionContent"] = {"contentRecordMap": {"UnsupportedContent": {}}}
    check("unknown_extension", not report([outbound], [unknown])["follow_up"])
    mismatch = activity(outbound)
    mismatch["resourceId"] = "different"
    check("resource_mismatch", not report([outbound], [mismatch])["follow_up"])
    wrong_owner = activity(outbound)
    wrong_owner["owner"] = PEER_URN
    check("owner_mismatch", not report([outbound], [wrong_owner])["follow_up"])
    missing_id = activity(outbound)
    missing_id.pop("activityId")
    check("missing_activity_id", not report([outbound], [missing_id])["follow_up"])
    wrong_thread = activity(outbound)
    wrong_thread["activity"]["thread"] = "urn:li:other:synthetic-thread"
    check("thread_type_mismatch", not report([outbound], [wrong_thread])["follow_up"])
    future = activity(outbound)
    future["activity"]["createdAt"] = int((NOW + timedelta(hours=1)).timestamp() * 1000)
    check("future_activity", not report([outbound], [future])["follow_up"])
    repeated = activity(outbound)
    repeated["activityStatus"] = "SUCCESSFUL_REPLAY"
    check("consistent_replay", len(report([outbound], [activity(outbound), repeated])["follow_up"]) == 1)
    conflicting = deepcopy(repeated)
    conflicting["activity"]["content"]["fallback"] = "conflict"
    check("conflicting_replay", not report([outbound], [activity(outbound), conflicting])["follow_up"])
    duplicated_row = deepcopy(outbound)
    duplicated_row["CONVERSATION ID"] = "urn:li:messagingThread:synthetic-thread"
    check("ambiguous_correlation", not report([outbound, duplicated_row], [activity(outbound)])["follow_up"])
    bad_time = activity(outbound)
    bad_time["activity"]["createdAt"] = True
    check("boolean_epoch", not report([outbound], [bad_time])["follow_up"])
    bad_time = activity(outbound)
    bad_time["activity"]["createdAt"] = -1
    check("negative_epoch", not report([outbound], [bad_time])["follow_up"])
    group = deepcopy(outbound)
    group["RECIPIENT PROFILE URLS"] = [PEER, "https://www.linkedin.com/in/person-b"]
    check("whole_thread_group_taint", not report([outbound, group], [activity(outbound)])["follow_up"])
    later_unknown = inbox_row(inbound=True, hours=1)
    check("later_unknown_inbound_blocks_followup", not report([outbound, later_unknown], [activity(outbound)])["follow_up"])
    check("later_unknown_outbound_blocks_reply", not report([incoming, inbox_row(hours=0)], [activity(incoming, inbound=True)])["reply"])
    same_time = inbox_row(inbound=True, hours=73)
    check("equal_second_no_reply", not report([outbound, same_time], [activity(outbound), activity(same_time, inbound=True, activity_id="activity-2")])["reply"])
    check("seventy_two_hour_boundary", len(report([inbox_row(hours=72)], [activity(inbox_row(hours=72))])["follow_up"]) == 1)
    stale = snapshot([outbound])
    stale["completed_at"] = (NOW - timedelta(days=8)).isoformat()
    check("stale_snapshot_withheld", not report([outbound], [activity(outbound)], inbox=stale)["follow_up"])
    incomplete = changelog([activity(outbound)])
    incomplete["truncated"] = True
    check("incomplete_changelog_withheld", not report([outbound], [activity(outbound)], log=incomplete)["follow_up"])
    missing_snapshot = snapshot([outbound])
    missing_snapshot["source_result"] = "404"
    check("missing_snapshot_withheld", not report([outbound], [activity(outbound)], inbox=missing_snapshot)["follow_up"])
    check("unknown_read_state", report([outbound], [activity(outbound)])["follow_up"][0]["read_state"] == "unknown")
    first = report([], [])
    check("first_dm_cited_draft", len(first["first_dm"]) == 1 and first["first_dm"][0]["date_added"] == "2026-09-01T01:02:03+00:00")
    check("complete_empty_changelog_without_watermark", len(first["first_dm"]) == 1)
    nonempty_missing_watermark = changelog([activity(outbound)])
    nonempty_missing_watermark["next_start_time"] = None
    check("nonempty_changelog_without_watermark_withheld", not report([outbound], [], log=nonempty_missing_watermark)["follow_up"])
    check("first_dm_connection_date_provenance", first["first_dm"][0]["connected_on"] == "2026-09-30" and first["first_dm"][0]["connection_source"] == "connections.rows[0].Connected On" and first["first_dm"][0]["connection_date_precision"] == "calendar_day")
    old_connection = [{"URL": PEER, "Connected On": (NOW - timedelta(days=31)).date().isoformat()}]
    check("old_provider_connection_blocks_first_dm", not report([], [], connection_rows=old_connection)["first_dm"])
    check("old_connection_preserves_manual_followup", len(report([outbound], [activity(outbound)], connection_rows=old_connection)["follow_up"]) == 1)
    check("missing_provider_connection_date_blocks_first_dm", not report([], [], connection_rows=[{"URL": PEER}])["first_dm"])
    check("malformed_provider_connection_date_blocks_first_dm", not report([], [], connection_rows=[{"URL": PEER, "Connected On": "yesterday"}])["first_dm"])
    conflicting_date = [{"URL": PEER, "Connected On": NOW.date().isoformat()}]
    check("policy_provider_date_mismatch_blocks_first_dm", not report([], [], connection_rows=conflicting_date)["first_dm"])
    duplicate_alias = [{"URL": PEER, "Connected On": "2026-09-30"}, {"URL": "https://linkedin.com/in/person-a", "Connected On": "2026-10-01"}]
    check("connection_alias_date_conflict_blocks_first_dm", not report([], [], connection_rows=duplicate_alias)["first_dm"])
    check("first_dm_snapshot_phrase", "no observed prior DM in supplied snapshot" in first["first_dm"][0]["reason"])
    no_research = {"rows": []}
    check("missing_research_withheld", not report([], [], research_data=no_research)["first_dm"])
    wrong_research = research()
    wrong_research["rows"][0]["profile_url"] = "https://www.linkedin.com/in/other"
    check("research_identity_mismatch", not report([], [], research_data=wrong_research)["first_dm"])
    opted = policy()
    opted["profiles"][PEER]["opt_out"] = True
    check("optout_blocks_all", not report([outbound], [activity(outbound)], policy_data=opted)["follow_up"])
    check("manual_followup_without_first_dm_policy", len(report([outbound], [activity(outbound)], policy_data={"profiles": {}})["follow_up"]) == 1)
    unqualified = policy()
    unqualified["profiles"][PEER]["country"] = "CA"
    check("qualification_required", not report([], [], policy_data=unqualified)["first_dm"])
    malformed_research_url = research()
    malformed_research_url["rows"][0]["supporting_facts"][0]["source_url"] = "https://user:pass@evidence.example/fact"
    check("research_url_userinfo_rejected", not report([], [], research_data=malformed_research_url)["first_dm"])
    malformed_research_url["rows"][0]["supporting_facts"][0]["source_url"] = "https:///missing-host"
    check("research_url_missing_host_rejected", not report([], [], research_data=malformed_research_url)["first_dm"])
    malformed_policy_url = policy()
    malformed_policy_url["profiles"][PEER]["qualification_citations"] = ["https://user:pass@evidence.example/qualification"]
    check("qualification_url_userinfo_rejected", not report([], [], policy_data=malformed_policy_url)["first_dm"])
    malformed_event = activity(outbound)
    malformed_event["activity"]["content"]["format"] = []
    malformed_result = report([outbound], [malformed_event])
    check("malformed_content_withheld", not any(malformed_result[key] for key in ("reply", "follow_up", "first_dm")) and malformed_result["withheld"])
    malformed_replay = activity(outbound)
    malformed_replay["activity"]["unexpected"] = {1: "first", "second": "second"}
    malformed_result = report([outbound], [malformed_replay])
    check("malformed_replay_withheld", not any(malformed_result[key] for key in ("reply", "follow_up", "first_dm")) and malformed_result["withheld"])
    malformed_log = changelog([activity(outbound)])
    malformed_log["events"] = {"unexpected": "mapping"}
    malformed_result = report([outbound], [], log=malformed_log)
    check("malformed_changelog_withheld", "changelog_incomplete" in malformed_result["coverage"]["reasons"] and not malformed_result["follow_up"])
    reversed_report = report([incoming, outbound], [activity(incoming, inbound=True, activity_id="activity-2"), activity(outbound)])
    ordered_report = report([outbound, incoming], [activity(outbound), activity(incoming, inbound=True, activity_id="activity-2")])
    check("input_order_determinism", [(item["profile_url"], item["message_at"]) for item in reversed_report["reply"]] == [(item["profile_url"], item["message_at"]) for item in ordered_report["reply"]])
    unrelated_group = inbox_row(hours=90, content="Synthetic unrelated group")
    unrelated_group["CONVERSATION ID"] = "unrelated-group"
    unrelated_group["RECIPIENT PROFILE URLS"] = ["https://www.linkedin.com/in/person-b", "https://www.linkedin.com/in/person-c"]
    group_event = activity(unrelated_group, activity_id="group-activity")
    group_event["activity"]["thread"] = "urn:li:messagingThread:unrelated-group"
    for name, extra_events in (("identified_group_row_is_local", []), ("identified_group_activity_is_local", [group_event])):
        result = report([outbound, unrelated_group], [activity(outbound), *extra_events])
        check(name, len(result["follow_up"]) == 1)
    for name, value in (("missing", None), ("null", None), ("nonboolean", "unknown"), ("nonmapping", [])):
        incomplete_policy = policy()
        if name == "missing":
            del incomplete_policy["profiles"][PEER]["opt_out"]
        elif name == "nonmapping":
            incomplete_policy["profiles"][PEER] = value
        else:
            incomplete_policy["profiles"][PEER]["opt_out"] = value
        result = report([outbound], [activity(outbound)], policy_data=incomplete_policy)
        check("policy_" + name + "_is_unverified", result["withheld"][0]["reason"] == "policy_opt_out_state_unverified")
    return verdicts


def cli_scenario() -> None:
    """Prove the private CLI emits fixed logs and fresh private report files."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = {"schema_version": 1, "account_member_urn": OWNER_URN, "collected_at": (NOW - timedelta(minutes=5)).isoformat(), "snapshots": {"INBOX": snapshot([]), "CONNECTIONS": {"rows": [{"URL": PEER, "Connected On": "2026-09-30"}], "raw_elements": [{"snapshotDomain": "CONNECTIONS", "snapshotData": [{"URL": PEER, "Connected On": "2026-09-30"}]}], "source_result": "success", "truncated": False, "page_count": 1, "completed_at": (NOW - timedelta(minutes=5)).isoformat()}}, "prospects": {"rows": [{"id": "prospect-a", "linkedin_url": PEER, "created_at": "2026-09-01T01:02:03+00:00", "attributes": {}}], "source_result": "success", "truncated": False, "page_count": 1, "row_count": 1, "consistency": "stable_count_and_unique_ids", "completed_at": (NOW - timedelta(minutes=5)).isoformat()}}
        source["changelog"] = changelog([])
        inputs = {"source": source, "policy": policy(), "research": research()}
        for name, data in inputs.items():
            path = base / f"{name}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            path.chmod(0o600)
        cmd = [sys.executable, str(ROOT / "scripts" / "build_private_dm_actions.py")]
        for name in inputs:
            cmd.extend([f"--{name}", str(base / f"{name}.json")])
        cmd.extend(["--output-dir", str(base / "report"), "--account-profile-url", ACCOUNT, "--now", NOW.isoformat()])
        result = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False)
        assert result.returncode == 0 and result.stdout == "private DM actions: report written\n" and result.stderr == ""
        output = base / "report"
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert {p.name for p in output.iterdir()} == {"private-dm-actions.json", "private-dm-actions.md"}
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir())
        first = json.loads((output / "private-dm-actions.json").read_text(encoding="utf-8"))["first_dm"]
        markdown = (output / "private-dm-actions.md").read_text(encoding="utf-8")
        assert len(first) == 1 and first[0]["connected_on"] == "2026-09-30"
        assert "Connected on: 2026-09-30 (calendar day)" in markdown
        assert "Connection source: connections.rows[0].Connected On" in markdown
        # A separate changelog acquisition must not override the bundled source.
        override = base / "override.json"
        override.write_text(json.dumps(changelog([activity(inbox_row(hours=90, content="Synthetic override"))])), encoding="utf-8")
        override.chmod(0o600)
        override_cmd = cmd.copy()
        override_cmd.extend(["--changelog", str(override)])
        override_cmd[override_cmd.index("--output-dir") + 1] = str(base / "override-report")
        failed = subprocess.run(override_cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False)
        assert failed.returncode == 1 and failed.stdout == "private DM actions: failed\n" and failed.stderr == ""
        assert not (base / "override-report").exists()
        for nested in (None, {"events": []}):
            source["changelog"] = nested
            (base / "source.json").write_text(json.dumps(source), encoding="utf-8")
            invalid_cmd = cmd.copy()
            invalid_cmd[invalid_cmd.index("--output-dir") + 1] = str(base / f"bad-changelog-{nested is None}")
            failed = subprocess.run(invalid_cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False)
            assert failed.returncode == 1 and failed.stdout == "private DM actions: failed\n" and failed.stderr == ""
            assert not Path(invalid_cmd[invalid_cmd.index("--output-dir") + 1]).exists()
        source["changelog"] = changelog([])
        for invalid_urn in (None, "urn:li:organization:123", "urn:li:person:"):
            source.pop("account_member_urn", None) if invalid_urn is None else source.__setitem__("account_member_urn", invalid_urn)
            (base / "source.json").write_text(json.dumps(source), encoding="utf-8")
            invalid_cmd = cmd.copy()
            invalid_cmd[invalid_cmd.index("--output-dir") + 1] = str(base / f"invalid-{invalid_urn is None}")
            failed = subprocess.run(invalid_cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False)
            assert failed.returncode == 1 and failed.stdout == "private DM actions: failed\n" and failed.stderr == ""
            assert not Path(invalid_cmd[invalid_cmd.index("--output-dir") + 1]).exists()
        source["account_member_urn"] = OWNER_URN
        source["snapshots"]["INBOX"] = None
        (base / "source.json").write_text(json.dumps(source), encoding="utf-8")
        bad_cmd = cmd.copy()
        bad_cmd[bad_cmd.index("--output-dir") + 1] = str(base / "bad-report")
        failed = subprocess.run(bad_cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False)
        assert failed.returncode == 0 and failed.stdout == "private DM actions: report written\n" and failed.stderr == ""
        withheld = json.loads((base / "bad-report" / "private-dm-actions.json").read_text(encoding="utf-8"))
        assert "inbox_incomplete" in withheld["coverage"]["reasons"]
        assert not any(withheld[key] for key in ("reply", "follow_up", "first_dm"))


def failure_artifact_scenario() -> None:
    """A failed current run must replace an old passing verdict before it fails."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.json"
        path.write_text('{"run_status":"passed"}\n', encoding="utf-8")
        original = globals()["run_matrix"]
        def fail() -> dict[str, str]:
            """Force a current-run failure after evidence initialization."""
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


def main(evidence_path: Path | None = None) -> None:
    """Run synthetic scenarios and retain a repeatable public verdict artifact."""
    path = evidence_path or ROOT / "artifacts" / "private-dm-actions-e2e-evidence.json"
    path.write_text(json.dumps({"run_status": "failed", "scenario_count": 0, "verdicts": {}}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdicts = run_matrix()
    cli_scenario()
    verdicts["private_cli"] = "passed"
    path.write_text(json.dumps({"run_status": "passed", "scenario_count": len(verdicts), "verdicts": verdicts}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"private DM actions: {len(verdicts)} synthetic scenarios passed")


if __name__ == "__main__":
    failure_artifact_scenario()
    main()
