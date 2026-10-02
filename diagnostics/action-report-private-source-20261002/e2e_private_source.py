"""Verify synthetic source collection through real clients and CMS encryption."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
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
    **{case: (RuntimeError, "private source collection failed") for case in ("db_cap", "db_malformed", "db_missing_count", "db_changed_count", "db_duplicate")},
}

async def exercise(case: str, cert: Path, target: Path) -> tuple[bytes | None, list[str]]:
    """Run production HTTP clients with complete or deliberately defective pages."""
    methods: list[str] = []
    def provider(request: httpx.Request) -> httpx.Response:
        """Provide valid source pages and one specific completeness defect."""
        methods.append(request.method)
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
        encrypted, methods = await exercise("complete", cert, output)
        result = await asyncio.to_thread(subprocess.run, ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(output), "-inkey", str(key), "-recip", str(cert)], capture_output=True, check=False)
        bundle = json.loads(result.stdout) if result.returncode == 0 else {}
        prospects = bundle.get("prospects", {}).get("rows", [])
        verdicts["complete_sources_roundtrip"] = bool(encrypted) and len(prospects) == 2 and len(bundle.get("events", {}).get("rows", [])) == 2 and set(bundle.get("snapshots", {})) == {"CONNECTIONS", "INVITATIONS", "INBOX"}
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
        verdicts["successful_empty_sources_encrypt"] = empty is not None and set(methods) == {"GET"} and zero_bundle.get("prospects", {}).get("row_count") == 0 and zero_bundle.get("events", {}).get("row_count") == 0 and all(not value["rows"] and value["page_count"] == 1 for value in zero_bundle.get("snapshots", {}).values())
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
