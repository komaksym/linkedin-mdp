"""Verify the real reporting pipeline against synthetic HTTP services."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp import google_doc_report as report
from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.supabase_client import SupabaseClient
from scripts import report_connections_doc as cli

TEXT = "🧭 Operator note\n[BEGIN AUTOMATED REPORT]\nold\n[END AUTOMATED REPORT]\nKeep this note.\n"
FIXED = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "private-client-id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "private-client-secret",
    "GOOGLE_OAUTH_REFRESH_TOKEN": "private-refresh-token",
    "GOOGLE_REPORT_DOCUMENT_ID": "private-doc-id",
}


def body(text: str) -> list[dict[str, Any]]:
    """Build the real Docs paragraph shape, including its initial section break."""
    items: list[dict[str, Any]] = [{"endIndex": 1, "sectionBreak": {}}]
    index = 1
    for line in text.splitlines(keepends=True):
        end = index + len(line.encode("utf-16-le")) // 2
        items.append({"startIndex": index, "endIndex": end, "paragraph": {
            "elements": [{"textRun": {"content": line}}],
        }})
        index = end
    return items


@dataclass
class Services:
    """Emulate Google, LinkedIn and Supabase HTTP boundaries without real data."""

    scenario: str = "complete"
    text: str = TEXT
    revision: int = 1
    attempts: int = 0
    writes: int = 0
    provider_calls: int = 0
    database_calls: int = 0
    permissions: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def handle(self, req: httpx.Request) -> httpx.Response:
        """Route a request and inject the scenario's failure at the HTTP boundary."""
        host = req.url.host
        if host == "oauth2.googleapis.com":
            if self.scenario == "oauth":
                return httpx.Response(400, json={"error": "private-refresh-token"})
            return httpx.Response(200, json={"access_token": "private-access-token"})
        if host == "www.googleapis.com":
            return self.drive(req)
        if host == "docs.googleapis.com":
            return self.docs(req)
        if host == "api.linkedin.com":
            return self.linkedin(req)
        if host == "supabase.invalid":
            return self.supabase(req)
        raise AssertionError("unexpected HTTP destination")

    def drive(self, req: httpx.Request) -> httpx.Response:
        """Return privacy metadata, including paginated or published permissions."""
        if req.url.path.endswith("/permissions"):
            self.permissions += 1
            assert req.url.params["includePermissionsForView"] == "published"
            owner = {"type": "user", "role": "owner"}
            if self.scenario == "sharing":
                if "pageToken" not in req.url.params:
                    return httpx.Response(200, json={"permissions": [owner], "nextPageToken": "page2"})
                return httpx.Response(200, json={"permissions": [{"type": "user", "role": "reader"}]})
            if self.scenario == "published":
                return httpx.Response(200, json={"permissions": [owner, {"type": "anyone", "role": "reader", "view": "published"}]})
            return httpx.Response(200, json={"permissions": [owner]})
        if self.scenario == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.invalid/"})
        return httpx.Response(200, json={"id": ENV["GOOGLE_REPORT_DOCUMENT_ID"],
            "mimeType": report.GOOGLE_DOCS_MIME, "trashed": False, "capabilities": {"canEdit": True}})

    def docs(self, req: httpx.Request) -> httpx.Response:
        """Apply atomic UTF-16 replacements only after the revision precondition passes."""
        if req.method == "GET":
            text = self.text
            if self.scenario == "markers":
                text = text.replace("old\n", "[BEGIN AUTOMATED REPORT]\n")
            if self.scenario == "empty_region":
                text = text.replace("old\n", "")
            content = body(text)
            if self.scenario == "rich":
                content[3]["paragraph"]["elements"].append({"inlineObjectElement": {"inlineObjectId": "fixture"}})
            tab: dict[str, Any] = {"tabProperties": {"tabId": "tab-a"}, "documentTab": {"body": {"content": content}}}
            if self.scenario == "nested":
                tab["childTabs"] = [tab.copy()]
            return httpx.Response(200, json={"revisionId": f"rev-{self.revision}", "tabs": [tab]})
        self.attempts += 1
        payload = json.loads(req.content)
        if self.scenario in {"conflict", "conflict_twice"} and (self.attempts == 1 or self.scenario == "conflict_twice"):
            self.revision += 1
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "The revision ID does not match the current revision."}})
        if self.scenario == "docs_bad_request":
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "private document content"}})
        assert payload["writeControl"]["requiredRevisionId"] == f"rev-{self.revision}"
        insert = payload["requests"][-1]
        location = insert["insertText"]["location"]
        if len(payload["requests"]) == 2:
            bounds = payload["requests"][0]["deleteContentRange"]["range"]
            assert bounds["endIndex"] > bounds["startIndex"]
        else:
            assert self.scenario == "empty_region"
            bounds = {"startIndex": location["index"], "endIndex": location["index"], "tabId": location["tabId"]}
            self.text = self.text.replace("old\n", "")
        assert bounds["tabId"] == location["tabId"] == "tab-a"
        assert bounds["startIndex"] == location["index"]
        encoded = self.text.encode("utf-16-le")
        start, end = (bounds[key] - 1 for key in ("startIndex", "endIndex"))
        self.text = (encoded[:start * 2] + insert["insertText"]["text"].encode("utf-16-le") + encoded[end * 2:]).decode("utf-16-le")
        self.writes += 1
        self.revision += 1
        return httpx.Response(200, json={"replies": [{}, {}]})

    def linkedin(self, req: httpx.Request) -> httpx.Response:
        """Serve full, empty, later-page missing, or truncated CONNECTIONS snapshots."""
        self.provider_calls += 1
        exhaustion = {
            "exhaustion_domain": "No data found for this domain and memberId.",
            "exhaustion_member": "No data found for this memberId.",
            "exhaustion_domain_no_period": "No data found for this domain and memberId",
            "exhaustion_member_no_period": "No data found for this memberId",
        }
        if self.scenario in exhaustion and self.provider_calls == 3:
            return httpx.Response(404, json={"message": exhaustion[self.scenario]})
        if self.scenario == "first_page_exhaustion":
            return httpx.Response(404, json={"message": exhaustion["exhaustion_domain"]})
        if self.provider_calls == 2 and self.scenario == "late_404_extra_text":
            return httpx.Response(404, json={"message": exhaustion["exhaustion_domain"] + " More details"})
        if self.provider_calls == 2 and self.scenario == "late_500_known_message":
            return httpx.Response(500, json={"message": exhaustion["exhaustion_domain"]})
        if self.scenario == "provider_404" or (self.scenario == "later_404" and self.provider_calls == 2):
            return httpx.Response(404, json={"message": "private provider response"})
        rows = [] if self.scenario == "empty" else [
            {"URL": "https://www.linkedin.com/in/private-one", "Name": "private-person"},
            {"URL": "https://www.linkedin.com/in/private-one"},
            {"URL": "https://www.linkedin.com/in/private-two"},
            {"URL": "https://www.linkedin.com/in/private-unmatched"},
        ]
        payload: dict[str, Any] = {"elements": [{"snapshotData": rows}]}
        if self.scenario in exhaustion and self.provider_calls == 2:
            payload["elements"] = [{"snapshotData": [
                {"URL": "https://www.linkedin.com/in/private-one"},
                {"URL": "https://www.linkedin.com/in/private-two", "Name": "private-person-two"},
            ]}]
        if self.scenario in {"later_404", "truncated", "late_404_extra_text", "late_500_known_message", *exhaustion}:
            payload["paging"] = {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=next"}]}
        return httpx.Response(200, json=payload)

    def supabase(self, req: httpx.Request) -> httpx.Response:
        """Read prospects and emulate duplicate-safe writes or uncertain write failures."""
        self.database_calls += 1
        if req.method == "GET":
            return httpx.Response(200, json=[
                {"id": "private-prospect-one", "linkedin_url_key": "https://www.linkedin.com/in/private-one"},
                {"id": "private-prospect-two", "linkedin_url_key": "https://www.linkedin.com/in/private-two"},
            ])
        assert req.url.params["on_conflict"] == "source,external_key"
        self.events = json.loads(req.content)
        if self.scenario == "supabase_timeout":
            raise httpx.ReadTimeout("private database failure", request=req)
        inserted = 0 if self.scenario == "duplicates" else 3 if self.scenario == "invalid_count" else 1
        return httpx.Response(200, json=[{"id": "private-event"}] * inserted)


async def pipeline(service: Services) -> tuple[int, str]:
    """Run the real CLI and clients with only HTTP transports and environment replaced."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
        google = report.GoogleDocPublisher(report.GoogleDocConfig.from_env(), http=http)
        linkedin = LinkedInMDPClient("private-linkedin-token", http=http, max_retries=0)
        supabase = SupabaseClient("https://supabase.invalid", "private-supabase-key", http=http)
        stdout, stderr = io.StringIO(), io.StringIO()
        provider_error = RuntimeError("private provider configuration") if service.scenario == "provider_config" else None
        database_error = RuntimeError("private database configuration") if service.scenario == "database_config" else None
        close_error = RuntimeError("private cleanup failure") if service.scenario == "close_failure" else None
        linkedin_close_error = RuntimeError("private provider cleanup") if service.scenario == "provider_close" else None
        database_close_error = RuntimeError("private database cleanup") if service.scenario == "database_close" else None
        with patch.object(cli, "GoogleDocPublisher", return_value=google), patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin, side_effect=provider_error), patch.object(cli.SupabaseClient, "from_env", return_value=supabase, side_effect=database_error), patch.object(google, "aclose", side_effect=close_error), patch.object(linkedin, "aclose", side_effect=linkedin_close_error), patch.object(supabase, "aclose", side_effect=database_close_error), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = await cli.run()
        return code, stdout.getvalue() + stderr.getvalue()


async def run_scenarios() -> dict[str, str]:
    """Assert pipeline outcomes, no leaked output, preserved notes, and fail-closed preflight."""
    results: dict[str, str] = {}
    cases = {
        "complete": (0, "COMPLETE"), "empty": (0, "COMPLETE"), "duplicates": (0, "COMPLETE"),
        "provider_404": (1, "BLOCKED"), "later_404": (1, "BLOCKED"), "truncated": (1, "BLOCKED"),
        "first_page_exhaustion": (1, "BLOCKED"),
        "late_404_extra_text": (1, "BLOCKED"), "late_500_known_message": (1, "BLOCKED"),
        "exhaustion_domain": (0, "COMPLETE"), "exhaustion_member": (0, "COMPLETE"),
        "exhaustion_domain_no_period": (0, "COMPLETE"), "exhaustion_member_no_period": (0, "COMPLETE"),
        "supabase_timeout": (1, "FAILED"), "invalid_count": (1, "FAILED"), "conflict": (0, "COMPLETE"),
        "conflict_twice": (2, None), "docs_bad_request": (2, None), "oauth": (2, None),
        "sharing": (2, None), "published": (2, None), "redirect": (2, None),
        "markers": (2, None), "rich": (2, None), "nested": (2, None),
        "provider_config": (1, "BLOCKED"), "database_config": (1, "FAILED"), "close_failure": (2, "COMPLETE"),
        "empty_region": (0, "COMPLETE"),
        "provider_close": (1, "COMPLETE"), "database_close": (1, "COMPLETE"),
    }
    with patch.dict(os.environ, ENV):
        for name, (expected_code, status) in cases.items():
            service = Services(name)
            code, output = await pipeline(service)
            assert code == expected_code, f"{name}: expected exit {expected_code}, got {code}; output={output.strip()!r}"
            assert "private" not in output and "Traceback" not in output, name
            assert "Fetched" not in output and "Inserted" not in output, name
            if name in {"provider_close", "database_close", "close_failure"}:
                assert "morning report: cleanup unavailable\n" in output, name
            if status is None:
                assert service.writes == 0 and service.text == TEXT, name
            else:
                assert service.writes == 1 and f"Status: {status}" in service.text, name
                before, after = TEXT.split("old\n")
                assert service.text.startswith(before) and service.text.endswith(after), name
                assert "Next action:" in service.text and "Run time:" in service.text, name
                if status != "COMPLETE":
                    assert service.text.count(": unknown") == 6, name
                if status == "BLOCKED":
                    assert service.database_calls == 0, name
                if status == "FAILED":
                    assert "may have happened" in service.text, name
            if name == "complete":
                assert len(service.events) == 2
                for line in ("Fetched rows: 4", "Matched rows: 3", "Unique matched connections: 2", "Unmatched rows: 1", "Newly inserted events: 1", "Already-present events: 1"):
                    assert line in service.text
            if name.startswith("exhaustion_"):
                assert service.provider_calls == 3 and len(service.events) == 2, name
                for line in ("Fetched rows: 5", "Matched rows: 4", "Unique matched connections: 2", "Unmatched rows: 1", "Newly inserted events: 1", "Already-present events: 1"):
                    assert line in service.text, name
            if name == "empty":
                assert service.text.count(": 0") == 6
            if name == "duplicates":
                assert "Newly inserted events: 0" in service.text and "Already-present events: 2" in service.text
            if name == "conflict":
                assert service.attempts == 2 and service.permissions == 5
            if name == "conflict_twice":
                assert service.attempts == 2
            if name == "docs_bad_request":
                assert service.attempts == 1
            if name in {"oauth", "sharing", "published", "redirect", "markers", "rich", "nested"}:
                assert service.provider_calls == service.database_calls == 0
            results[name] = "passed"
        with patch.dict(os.environ, {"GOOGLE_OAUTH_REFRESH_TOKEN": ""}):
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                assert await cli.run() == 2
            assert out.getvalue() == "morning report: configuration unavailable\n"
            results["missing_configuration"] = "passed"
    results.update(await run_generic_text_scenarios())
    return results


async def run_generic_text_scenarios() -> dict[str, str]:
    """Prove the shared managed-region boundary preserves every Google safety guard."""
    results: dict[str, str] = {}
    with patch.dict(os.environ, ENV):
        for name in ("complete", "conflict", "conflict_twice", "sharing", "published", "markers", "rich", "nested"):
            service = Services(name)
            async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
                publisher = report.GoogleDocPublisher(report.GoogleDocConfig.from_env(), http=http)
                try:
                    await publisher.publish_text("Synthetic complete-text report\nSecond line\n")
                except report.GoogleReportError:
                    assert name in {"conflict_twice", "sharing", "published", "markers", "rich", "nested"}, name
                else:
                    assert name in {"complete", "conflict"}, name
            if name in {"complete", "conflict"}:
                assert service.writes == 1, name
                assert service.text.startswith("🧭 Operator note\n[BEGIN AUTOMATED REPORT]\n"), name
                assert service.text.endswith("[END AUTOMATED REPORT]\nKeep this note.\n"), name
                assert "Synthetic complete-text report\nSecond line\n" in service.text, name
                assert "LinkedIn CONNECTIONS reconciliation" not in service.text, name
            else:
                assert service.writes == 0 and service.text == TEXT, name
            if name == "conflict":
                assert service.attempts == 2 and service.permissions == 4
            if name == "conflict_twice":
                assert service.attempts == 2
            results[f"publish_text_{name}"] = "passed"

        service = Services("complete")
        async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
            publisher = report.GoogleDocPublisher(report.GoogleDocConfig.from_env(), http=http)
            await publisher.publish_text("Stable body\n")
            first = service.text
            await publisher.publish_text("Stable body\n")
            assert service.text == first
            assert service.writes == 2
        results["publish_text_rerun_converges"] = "passed"

        service = Services("complete")
        async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
            publisher = report.GoogleDocPublisher(report.GoogleDocConfig.from_env(), http=http)
            try:
                await publisher.publish_text("safe\n[BEGIN AUTOMATED REPORT]\nunsafe\n")
            except report.GoogleReportError:
                pass
            else:
                raise AssertionError("managed marker injection was accepted")
        assert service.writes == 0 and service.text == TEXT
        results["publish_text_rejects_managed_markers"] = "passed"
    return results


def main() -> None:
    """Save only repeatable synthetic verdicts; never save request or report data."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    verdicts = asyncio.run(run_scenarios())
    evidence = json.dumps({"kind": "synthetic HTTP boundary E2E", "scenarios": verdicts}, indent=2, sort_keys=True) + "\n"
    if args.evidence:
        args.evidence.write_text(evidence)
    print(evidence, end="")


if __name__ == "__main__":
    main()
