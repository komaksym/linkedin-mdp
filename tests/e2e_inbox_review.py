"""Exercise review regressions through the provider and database HTTP boundaries."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.inbox_sync import reconcile_inbox
from linkedin_mdp_mcp.supabase_client import SupabaseClient

_verifier_path = Path(__file__).resolve().parents[1] / "diagnostics" / "verify_inbox_live.py"
_verifier_spec = importlib.util.spec_from_file_location("verify_inbox_live", _verifier_path)
assert _verifier_spec is not None and _verifier_spec.loader is not None
verify_inbox_live = importlib.util.module_from_spec(_verifier_spec)
_verifier_spec.loader.exec_module(verify_inbox_live)

ACCOUNT = "https://www.linkedin.com/in/account"
PEER = "https://www.linkedin.com/in/prospect"


def row(**changes: Any) -> dict[str, Any]:
    """Build one attachment-only provider message with overridable fields."""
    value: dict[str, Any] = {
        "CONVERSATION ID": "review-conversation",
        "DATE": "2026-09-30T10:00:00Z",
        "SENDER PROFILE URL": PEER,
        "RECIPIENT PROFILE URLS": [ACCOUNT],
        "ATTACHMENTS": "attachment.pdf",
    }
    value.update(changes)
    return value


async def reconcile(rows: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    """Run real clients against synthetic LinkedIn and Supabase responses."""
    writes: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        """Serve one complete INBOX page and its ordinary exhaustion response."""
        if "start=" in str(request.url):
            return httpx.Response(404, json={"message": "No data found for this domain and memberId"})
        return httpx.Response(200, json={"elements": [{"snapshotDomain": "INBOX", "snapshotData": rows}]})

    def database(request: httpx.Request) -> httpx.Response:
        """Capture the events sent by the production writer."""
        if request.url.path.endswith("/prospects"):
            return httpx.Response(200, json=[{"id": "p1", "linkedin_url_key": PEER}])
        if request.method == "POST":
            writes.extend(json.loads(request.content))
            return httpx.Response(201, json=writes)
        return httpx.Response(200, json=[])

    linkedin = LinkedInMDPClient("synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    store = SupabaseClient("https://db.example.test", "synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(database)))
    try:
        result = await reconcile_inbox(linkedin, store, account_profile_url=ACCOUNT, dry_run=False)
    finally:
        await linkedin._http.aclose()
        await store._http.aclose()
    return result, writes


async def verify_live_plan(*, empty: bool = True, corrupt: bool = False, inviter_urls: list[Any] | None = None) -> tuple[str, list[dict[str, Any]], int]:
    """Run real verifier HTTP boundaries with empty, persisted, or corrupt evidence."""
    writes: list[dict[str, Any]] = []
    persisted: dict[str, dict[str, Any]] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        """Serve one complete invitation and inbox page."""
        domain = request.url.params.get("domain")
        if domain == "INVITATIONS":
            data: list[dict[str, Any]] = [{"Direction": "OUTGOING", "inviterProfileUrl": url} for url in (inviter_urls if inviter_urls is not None else [ACCOUNT])]
        else:
            data = [row(CONTENT="unmatched")]
        return httpx.Response(200, json={"elements": [{"snapshotDomain": domain, "snapshotData": data}]})

    def database(request: httpx.Request) -> httpx.Response:
        """Serve exact prospect mappings and duplicate-safe, optionally corrupt events."""
        if request.url.path.endswith("/prospects"):
            return httpx.Response(200, json=[] if empty else [{"id": "p1", "linkedin_url_key": PEER}])
        if request.method == "POST":
            inserted = []
            for event in json.loads(request.content):
                if event["external_key"] not in persisted:
                    writes.append(event)
                    saved = deepcopy(event)
                    if corrupt:
                        saved["payload"]["content"] = "corrupt synthetic evidence"
                    persisted[event["external_key"]] = saved
                    inserted.append(saved)
            return httpx.Response(201, json=inserted)
        return httpx.Response(200, json=list(persisted.values()))

    linkedin = LinkedInMDPClient("synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    configured = SupabaseClient("https://db.example.test", "synthetic")
    original = verify_inbox_live.VerifiedStore

    def make_store(url: str, key: str) -> verify_inbox_live.VerifiedStore:
        """Supply the real verifying store with a synthetic HTTP transport."""
        store = original(url, key)
        store._http = httpx.AsyncClient(transport=httpx.MockTransport(database))
        return store

    output = StringIO()
    with (
        patch.object(verify_inbox_live.LinkedInMDPClient, "from_env", return_value=linkedin),
        patch.object(verify_inbox_live.SupabaseClient, "from_env", return_value=configured),
        patch.object(verify_inbox_live, "VerifiedStore", make_store),
        redirect_stdout(output),
    ):
        code = await asyncio.to_thread(verify_inbox_live.main)
    await linkedin._http.aclose()
    return output.getvalue().strip(), writes, code


async def main() -> None:
    """Require review fixes and persist only fixed, private-data-free verdicts."""
    result, writes = await reconcile([row()])
    verdicts = {
        "missing_content_with_attachment_persists": result.planned_events == 1 and len(writes) == 1 and writes[0]["payload"]["content"] == "",
    }
    result, writes = await reconcile([row(ATTACHMENTS="")])
    verdicts["missing_content_without_attachment_skips"] = result.planned_events == 0 and not writes
    result, writes = await reconcile([row(CONTENT=None)])
    verdicts["explicit_null_content_rejected"] = result.planned_events == 0 and not writes
    result, writes = await reconcile([row(CONTENT=123)])
    verdicts["nonstr_content_rejected"] = result.planned_events == 0 and not writes
    result, writes = await reconcile([row(CONTENT="")])
    verdicts["empty_string_attachment_persists"] = result.planned_events == 1 and len(writes) == 1
    line, writes, code = await verify_live_plan()
    verdicts["empty_plan_live_verifier_succeeds"] = code == 0
    verdicts["empty_plan_reports_empty_result"] = line == "live inbox evidence: empty result and duplicate-free replay verified"
    verdicts["empty_plan_makes_no_writes"] = not writes
    line, writes, code = await verify_live_plan(empty=False)
    verdicts["nonempty_plan_verifies_readback_and_replay"] = (
        code == 0 and len(writes) == 1
        and line == "live inbox evidence: persistence and duplicate-free replay verified"
    )
    line, writes, code = await verify_live_plan(empty=False, inviter_urls=[ACCOUNT, "https://linkedin.com/in/account/", "https://pl.linkedin.com/in/account?trk=test"])
    verdicts["equivalent_inviter_profiles_verify"] = code == 0 and len(writes) == 1
    for label, urls in (
        ("distinct_inviter_profiles_rejected", [ACCOUNT, PEER]),
        ("invalid_inviter_profile_rejected", [ACCOUNT, "https://linkedin.com.evil.test/in/account"]),
        ("nonstring_inviter_profile_rejected", [ACCOUNT, None]),
    ):
        line, writes, code = await verify_live_plan(empty=False, inviter_urls=urls)
        verdicts[label] = code == 1 and not writes and line == "live inbox evidence: verification failed"
    line, _, code = await verify_live_plan(empty=False, corrupt=True)
    verdicts["corrupt_readback_is_rejected"] = code == 1 and line == "live inbox evidence: verification failed"
    optimized = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-O", str(Path(__file__).resolve()), "--corrupt-verifier"],
        capture_output=True, text=True, check=False,
    )
    verdicts["optimized_python_rejects_corrupt_readback"] = (
        optimized.returncode == 1 and optimized.stdout.strip() == "live inbox evidence: verification failed"
    )
    target = Path(__file__).resolve().parents[1] / "artifacts" / "inbox-review-e2e-evidence.json"
    target.write_text(json.dumps({"scenarios": verdicts}, indent=2) + "\n")
    assert all(verdicts.values()), verdicts


if __name__ == "__main__":
    if "--corrupt-verifier" in sys.argv:
        line, _, code = asyncio.run(verify_live_plan(empty=False, corrupt=True))
        print(line)
        raise SystemExit(code)
    asyncio.run(main())
