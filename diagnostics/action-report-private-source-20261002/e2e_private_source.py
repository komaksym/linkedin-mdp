"""Verify synthetic source collection through real clients and CMS encryption."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient
from linkedin_mdp_mcp.supabase_client import SupabaseClient

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("private_source", HERE / "collect_private_source.py")
if spec is None or spec.loader is None:
    raise RuntimeError("collector unavailable")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

MARKER = "PRIVATE_SYNTHETIC_MEMBER_ONLY_493829"
EXPECTED_FAILURES: dict[str, tuple[type[Exception], str]] = {
    "unavailable": (RuntimeError, "private source collection failed"),
    "malformed": (LinkedInAPIError, "LinkedIn API error 0: malformed snapshot element"),
    "domain": (LinkedInAPIError, "LinkedIn API error 0: snapshot element domain does not match request"),
    "truncated": (RuntimeError, "private source collection failed"),
    "escaped_domain": (LinkedInAPIError, "LinkedIn API error 0: snapshot next link changed request scope"),
    "change_bad_envelope": (LinkedInAPIError, "LinkedIn API error 0: malformed changelog elements list"),
    "change_wrong_endpoint": (LinkedInAPIError, "LinkedIn API error 0: changelog next link changed endpoint"),
    "change_wrong_q": (LinkedInAPIError, "LinkedIn API error 0: changelog next link changed request scope"),
    "change_added_start_time": (LinkedInAPIError, "LinkedIn API error 0: changelog next link changed request scope"),
    "change_bad_next": (LinkedInAPIError, "LinkedIn API error 0: malformed changelog paging link"),
    **{case: (RuntimeError, "private source collection failed") for case in ("db_cap", "db_malformed", "db_missing_count", "db_changed_count", "db_duplicate", "change_bad_event", "change_no_watermark", "change_zero_watermark", "change_negative_watermark", "change_boolean_watermark", "change_truncated", "auth_missing", "auth_multiple", "auth_malformed", "auth_wrong_prefix", "auth_suffix_whitespace", "auth_suffix_internal_space", "auth_suffix_bad_chars")},
}

async def exercise(case: str, cert: Path, target: Path) -> tuple[bytes | None, list[str]]:
    """Run production HTTP clients with complete or deliberately defective pages."""
    methods: list[str] = []
    def provider(request: httpx.Request) -> httpx.Response:
        """Provide valid source pages and one specific completeness defect."""
        methods.append(request.method)
        if request.url.path == "/rest/memberAuthorizations":
            member = "urn:li:person:synthetic-member-493829"
            elements: list[Any] = [{"memberComplianceAuthorizationKey": {"member": member}}]
            if case == "auth_missing":
                elements = []
            if case == "auth_multiple":
                elements.append({"memberComplianceAuthorizationKey": {"member": "urn:li:person:second-synthetic"}})
            if case == "auth_malformed":
                elements = [{"memberComplianceAuthorizationKey": {"member": 42}}]
            if case == "auth_wrong_prefix":
                elements = [{"memberComplianceAuthorizationKey": {"member": "urn:li:organization:synthetic"}}]
            if case == "auth_suffix_whitespace":
                elements = [{"memberComplianceAuthorizationKey": {"member": "urn:li:person: synthetic-member"}}]
            if case == "auth_suffix_internal_space":
                elements = [{"memberComplianceAuthorizationKey": {"member": "urn:li:person:synthetic member"}}]
            if case == "auth_suffix_bad_chars":
                elements = [{"memberComplianceAuthorizationKey": {"member": "urn:li:person:synthetic/member"}}]
            return httpx.Response(200, json={"elements": elements})
        if request.url.path == "/rest/memberChangeLogs":
            if case == "change_added_start_time":
                if request.url.params.get("start") == "1":
                    raise RuntimeError("second changelog request reached")
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000}], "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?start=1&startTime=1710000000"}]}})
            if case == "change_bad_envelope":
                return httpx.Response(200, json={"paging": {"links": []}})
            if case == "change_wrong_endpoint":
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000}], "paging": {"links": [{"rel": "next", "href": "/rest/memberAuthorizations?start=1"}]}})
            if case == "change_wrong_q":
                if request.url.params.get("start") == "1":
                    return httpx.Response(200, json={"elements": [{"processedAt": 1710000001}], "paging": {"links": []}})
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000}], "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?q=other&start=1"}]}})
            if case == "change_bad_next":
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000}], "paging": {"links": [None]}})
            if case == "change_paged":
                if request.url.params.get("start") == "1":
                    kind = "SECOND" if request.url.params.get("q") == "memberAndApplication" and request.url.params.get("count") == "50" else "SECOND_BAD_SCOPE"
                    return httpx.Response(200, json={"elements": [{"processedAt": 1710000001, "changeType": kind}], "paging": {"links": []}})
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000, "changeType": "FIRST"}], "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?start=1"}]}})
            if case == "change_truncated":
                return httpx.Response(200, json={"elements": [{"processedAt": 1710000000}], "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?start=1"}]}})
            events: list[Any] = [] if case == "empty" else [{"processedAt": 1710000000, "changeType": "SYNTHETIC", "opaque": {"keep": [1, "two"]}}]
            if case == "change_bad_event":
                events = [None]
            if case == "change_no_watermark":
                events = [{"changeType": "SYNTHETIC"}]
            if case == "change_zero_watermark":
                events[0]["processedAt"] = 0
            if case == "change_negative_watermark":
                events[0]["processedAt"] = -1
            if case == "change_boolean_watermark":
                events[0]["processedAt"] = True
            return httpx.Response(200, json={"elements": events, "paging": {"links": []}})
        if case == "change_wrong_endpoint" and request.url.path == "/rest/memberAuthorizations":
            return httpx.Response(200, json={"elements": [{"processedAt": 1710000001}], "paging": {"links": []}})
        domain = request.url.params.get("domain")
        if case == "unavailable":
            return httpx.Response(404, json={"message": "unavailable"})
        if case == "malformed":
            return httpx.Response(200, json={"elements": [None]})
        payload: dict[str, Any] = {"elements": [{"snapshotDomain": "WRONG" if case == "domain" else domain, "snapshotData": [] if case == "empty" else [{"synthetic": MARKER}]}]}
        if case in ("truncated", "escaped_domain"):
            scope = "&domain=INBOX" if case == "escaped_domain" else ""
            payload["paging"] = {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=1" + scope}]}
        if case == "empty":
            payload["elements"] = []
        return httpx.Response(200, json=payload)
    def database(request: httpx.Request) -> httpx.Response:
        """Serve counted pages and optional database completeness defects."""
        methods.append(request.method)
        offset = int(request.url.params["offset"])
        table = request.url.path.rsplit("/", 1)[-1]
        data = [{"id": f"{table}-1", "attributes": {"synthetic": MARKER}}, {"id": f"{table}-2", "attributes": {}}]
        if table == "prospects":
            data[0]["created_at"] = "2025-01-02T03:04:05.123456+00:00"
            if case != "missing_created_at":
                data[1]["created_at"] = "2025-06-07T08:09:10Z"
        selected = request.url.params["select"].split(",")
        data = [{key: value for key, value in row.items() if key in selected} for row in data]
        if case == "db_cap":
            data.append({"id": f"{table}-3", "attributes": {}})
        page = [] if case == "empty" else data[offset:offset + 1]
        if case == "db_malformed":
            return httpx.Response(200, json={})
        if case == "db_duplicate" and offset == 1:
            page = [data[0]]
        headers = {} if case == "db_missing_count" else {"Content-Range": f"{offset}-{offset}/2"}
        if case == "empty":
            headers = {"Content-Range": "*/0"}
        if case == "db_cap":
            headers = {"Content-Range": f"{offset}-{offset}/3"}
        elif case == "db_changed_count" and offset == 1:
            headers = {"Content-Range": "1-1/3"}
        return httpx.Response(200, json=page, headers=headers)
    linkedin = LinkedInMDPClient("synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    store = SupabaseClient("https://db.example.test", "synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(database)))
    try:
        try:
            bundle = await collector.collect_sources(linkedin, store, page_size=1, max_db_pages=2, snapshot_pages=1)
        except Exception as error:  # noqa: BLE001
            expected = EXPECTED_FAILURES.get(case)
            if expected is None or type(error) is not expected[0] or str(error) != expected[1]:
                raise RuntimeError("unexpected synthetic collection failure") from None
            return None, methods
        collector.encrypt_bundle(bundle, cert, target)
        return target.read_bytes(), methods
    finally:
        await linkedin._http.aclose()
        await store._http.aclose()

async def main(evidence: Path | None = None) -> None:
    """Require real encryption roundtrip, safe failures and an artifact-only result."""
    evidence = HERE / "e2e-evidence.json" if evidence is None else evidence
    evidence.write_text(json.dumps({"synthetic_only": True, "run_status": "failed", "scenarios": {}}, indent=2) + "\n")
    verdicts: dict[str, bool] = {}
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        key, cert = tmp / "test-key.pem", tmp / "test-cert.pem"
        generated = await asyncio.to_thread(subprocess.run, ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=synthetic-test"], capture_output=True, check=False)
        if generated.returncode:
            raise RuntimeError("synthetic certificate generation failed")
        output = tmp / "source.cms"
        run_started = datetime.now(UTC)
        encrypted, methods = await exercise("complete", cert, output)
        run_completed = datetime.now(UTC)
        result = await asyncio.to_thread(subprocess.run, ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(output), "-inkey", str(key), "-recip", str(cert)], capture_output=True, check=False)
        bundle = json.loads(result.stdout) if result.returncode == 0 else {}
        prospects = bundle.get("prospects", {}).get("rows", [])
        changelog = bundle.get("changelog", {})
        change_events = changelog.get("events")
        verdicts["complete_sources_roundtrip"] = bool(encrypted) and len(prospects) == 2 and len(bundle.get("events", {}).get("rows", [])) == 2 and set(bundle.get("snapshots", {})) == {"CONNECTIONS", "INVITATIONS", "INBOX"}
        verdicts["account_member_urn_roundtrip"] = bundle.get("account_member_urn") == "urn:li:person:synthetic-member-493829"
        times = [datetime.fromisoformat(changelog[key]) for key in ("attempted_at", "completed_at", "collected_at")] if all(isinstance(changelog.get(key), str) for key in ("attempted_at", "completed_at", "collected_at")) else []
        verdicts["changelog_raw_events_and_acquisition_timestamps"] = change_events == [{"processedAt": 1710000000, "changeType": "SYNTHETIC", "opaque": {"keep": [1, "two"]}}] and len(times) == 3 and run_started <= times[0] <= times[1] <= times[2] <= run_completed and changelog.get("provider_generated_at") is None and changelog.get("upstream_freshness") == "unknown"
        verdicts["changelog_complete_watermark"] = changelog.get("next_start_time") == 1710000000 and changelog.get("source_result") == "success" and changelog.get("truncated") is False and type(changelog.get("page_count")) is int and changelog["page_count"] > 0
        paged, _ = await exercise("change_paged", cert, tmp / "paged.cms")
        paged_result = await asyncio.to_thread(subprocess.run, ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(tmp / "paged.cms"), "-inkey", str(key), "-recip", str(cert)], capture_output=True, check=False)
        paged_changelog = json.loads(paged_result.stdout).get("changelog", {}) if paged_result.returncode == 0 else {}
        verdicts["changelog_valid_pagination_preserves_scope_and_events"] = paged is not None and paged_changelog.get("events") == [{"processedAt": 1710000000, "changeType": "FIRST"}, {"processedAt": 1710000001, "changeType": "SECOND"}] and paged_changelog.get("page_count") == 2 and paged_changelog.get("next_start_time") == 1710000001
        seeded_requests: list[httpx.Request] = []
        def seeded_provider(request: httpx.Request) -> httpx.Response:
            """Return two pages with the caller's unchanged start time."""
            seeded_requests.append(request)
            if request.url.params.get("start") == "1":
                return httpx.Response(200, json={"elements": [{"processedAt": 1002}], "paging": {"links": []}})
            return httpx.Response(200, json={"elements": [{"processedAt": 1001}], "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?start=1&startTime=1000"}]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(seeded_provider)) as http:
            seeded = await LinkedInMDPClient("synthetic", http=http).changelog(start_time=1000)
        verdicts["changelog_original_start_time_survives_pagination"] = seeded["page_count"] == 2 and seeded["next_start_time"] == 1002 and len(seeded_requests) == 2 and all(request.url.params.get("startTime") == "1000" for request in seeded_requests)
        verdicts["prospect_created_at_exact_across_pages"] = [row.get("created_at") for row in prospects] == ["2025-01-02T03:04:05.123456+00:00", "2025-06-07T08:09:10Z"]
        missing, _ = await exercise("missing_created_at", cert, tmp / "missing.cms")
        missing_result = await asyncio.to_thread(subprocess.run, ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(tmp / "missing.cms"), "-inkey", str(key), "-recip", str(cert)], capture_output=True, check=False)
        missing_rows = json.loads(missing_result.stdout).get("prospects", {}).get("rows", []) if missing_result.returncode == 0 else []
        verdicts["missing_created_at_not_fabricated"] = bool(missing) and len(missing_rows) == 2 and "created_at" not in missing_rows[1]
        verdicts["private_plaintext_absent_encrypted_artifact"] = encrypted is not None and MARKER.encode() not in encrypted
        verdicts["read_only_http"] = set(methods) == {"GET"}
        empty, methods = await exercise("empty", cert, tmp / "empty.cms")
        zero = await asyncio.to_thread(subprocess.run, ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(tmp / "empty.cms"), "-inkey", str(key), "-recip", str(cert)], capture_output=True, check=False)
        zero_bundle = json.loads(zero.stdout) if zero.returncode == 0 else {}
        empty_changelog = zero_bundle.get("changelog", {})
        verdicts["successful_empty_sources_encrypt"] = empty is not None and set(methods) == {"GET"} and zero_bundle.get("prospects", {}).get("row_count") == 0 and zero_bundle.get("events", {}).get("row_count") == 0 and all(not value["rows"] and value["page_count"] == 1 for value in zero_bundle.get("snapshots", {}).values()) and empty_changelog.get("events") == [] and empty_changelog.get("next_start_time") is None and empty_changelog.get("source_result") == "success"
        for case in EXPECTED_FAILURES:
            target = tmp / f"{case}.cms"
            encrypted, methods = await exercise(case, cert, target)
            verdicts[f"{case}_rejected_without_artifact"] = encrypted is None and not target.exists() and set(methods) == {"GET"}
        with patch.object(collector, "collect_sources", side_effect=RuntimeError("unrelated synthetic fault")):
            try:
                await exercise("db_missing_count", cert, tmp / "unrelated.cms")
            except RuntimeError as error:
                verdicts["unexpected_failure_not_counted_as_rejection"] = str(error) == "unexpected synthetic collection failure"
            else:
                verdicts["unexpected_failure_not_counted_as_rejection"] = False
        with patch.object(collector, "collect_sources", return_value={}), patch.object(collector, "encrypt_bundle", side_effect=RuntimeError("private source collection failed")):
            try:
                await exercise("db_missing_count", cert, tmp / "encrypt-fault.cms")
            except RuntimeError as error:
                verdicts["encryption_failure_not_counted_as_rejection"] = str(error) == "private source collection failed"
            else:
                verdicts["encryption_failure_not_counted_as_rejection"] = False
        async def synthetic_export(certificate: Path, target: Path) -> None:
            """Exercise the actual collector/encryptor under the CLI verdict boundary."""
            value, _ = await exercise("complete", certificate, target)
            if value is None:
                raise RuntimeError(MARKER)
        logged = StringIO()
        cli_target = tmp / "cli.cms"
        with patch.object(collector, "export", synthetic_export), patch.object(sys, "argv", ["collect", "--certificate", str(cert), "--output", str(cli_target)]), redirect_stdout(logged):
            code = await asyncio.to_thread(collector.main)
        verdicts["cli_fixed_success_log_without_plaintext"] = code == 0 and logged.getvalue() == "private source export: encrypted\n" and MARKER not in logged.getvalue()
        async def failing_export(certificate: Path, target: Path) -> None:
            """Raise a deliberately private error to verify CLI sanitization."""
            raise RuntimeError(MARKER)
        logged = StringIO()
        with patch.object(collector, "export", failing_export), patch.object(sys, "argv", ["collect", "--certificate", str(cert), "--output", str(tmp / "failed.cms")]), redirect_stdout(logged):
            code = await asyncio.to_thread(collector.main)
        verdicts["cli_fixed_failure_log_without_plaintext"] = code == 1 and logged.getvalue() == "private source export: failed\n" and not (tmp / "failed.cms").exists()
        verdicts["no_plaintext_source_file"] = all(path.suffix != ".json" for path in tmp.iterdir())
        stale = tmp / "stale-evidence.json"
        stale.write_text(json.dumps({"synthetic_only": True, "run_status": "passed", "scenarios": {"stale": True}}))
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"")):
            try:
                await main(stale)
            except RuntimeError:
                pass
        verdicts["early_failure_invalidates_stale_evidence"] = json.loads(stale.read_text()) == {"synthetic_only": True, "run_status": "failed", "scenarios": {}}
    evidence.write_text(json.dumps({"synthetic_only": True, "run_status": "passed" if all(verdicts.values()) else "failed", "scenarios": verdicts}, indent=2) + "\n")
    if not all(verdicts.values()):
        raise RuntimeError("private source E2E failed")
    print("private source synthetic E2E: verified")

if __name__ == "__main__":
    asyncio.run(main())
