"""Verify the two private DM Google Doc destinations against synthetic HTTP services."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp import google_doc_report as report
from scripts import publish_private_dm_docs as cli

NOW = "2026-10-05T08:00:00+00:00"
RUN_TIME = "Run time: 2026-10-05 10:00:00 CEST"
FIRST_ID = "private-first-dm-doc-id"
FOLLOW_ID = "private-follow-up-doc-id"
FIRST_TEXT = "🧭 Operator note\n[BEGIN AUTOMATED REPORT]\nold first\n[END AUTOMATED REPORT]\nKeep this note.\n"
FOLLOW_TEXT = "🧭 Operator note\n[BEGIN AUTOMATED REPORT]\nold follow\n[END AUTOMATED REPORT]\nKeep this note.\n"
ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "private-client-id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "private-client-secret",
    "GOOGLE_OAUTH_REFRESH_TOKEN": "private-refresh-token",
    "GOOGLE_FIRST_DM_REPORT_DOCUMENT_ID": FIRST_ID,
    "GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID": FOLLOW_ID,
}


def actions() -> dict[str, Any]:
    """Build one complete synthetic planner artifact with every action kind."""
    return {
        "reply": [{
            "profile_url": "https://www.linkedin.com/in/synthetic-replier",
            "prospect_id": "reply-prospect",
            "date_added": "2026-09-20T00:00:00+00:00",
            "date_added_source": "prospects.rows[0].created_at",
            "reason": "latest verified inbound DM after outbound",
            "message_at": "2026-10-04T09:00:00+00:00",
            "message": "Synthetic inbound reply",
            "source_refs": ["inbox.rows[0]", "changelog.events[0]"],
            "action": "Owner writes reply",
            "read_state": "unknown",
        }],
        "follow_up": [{
            "profile_url": "https://www.linkedin.com/in/synthetic-follower",
            "prospect_id": "follow-prospect",
            "date_added": None,
            "date_added_source": "prospects.rows[1].created_at",
            "reason": "verified outbound DM at least 72 hours old with no later observed inbound",
            "message_at": "2026-10-01T10:00:00+00:00",
            "elapsed_hours": 94,
            "message": "Synthetic previous outbound",
            "source_refs": ["inbox.rows[1]", "changelog.events[1]"],
            "action": "Owner writes follow-up",
            "read_state": "unknown",
        }],
        "first_dm": [{
            "profile_url": "https://www.linkedin.com/in/synthetic-drafter",
            "prospect_id": "draft-prospect",
            "date_added": "2026-09-23T21:17:10+00:00",
            "date_added_source": "prospects.rows[2].created_at",
            "reason": "no observed prior DM in supplied snapshot; consent changelog is bounded",
            "connected_on": "2026-09-30",
            "connection_source": "connections.rows[0].Connected On",
            "connection_date_precision": "calendar_day",
            "draft": "Synthetic researched opener",
            "citations": [{"fact": "Synthetic employer fact", "source_url": "https://example.com/employer"}],
            "action": "Owner reviews and sends first DM",
            "read_state": "unknown",
        }],
        "withheld": [
            {"profile_url": "https://www.linkedin.com/in/synthetic-withheld", "reason": "message_history_ambiguous_or_action_not_due"},
            {"profile_url": "https://www.linkedin.com/in/synthetic-other", "reason": "opt_out"},
        ],
        "coverage": {"reasons": [], "upstream_freshness": "unknown", "changelog_scope": "consent_limited_28_days"},
    }


def retained(actions_value: dict[str, Any], *, count: int = 1) -> dict[str, Any]:
    """Build retained first-DM publication evidence that must survive republishing."""
    rows = [
        {
            "name": f"Synthetic Conditional {index}",
            "profile_url": f"https://www.linkedin.com/in/synthetic-conditional-{index}",
            "date_added": "2026-09-22T10:00:00+00:00",
            "connected_on": "2026-09-29",
            "draft": f"Synthetic conditional researched opener {index}",
            "status": "CONDITIONAL FIRST-DM DRAFT. NOT CLEARED TO SEND",
            "employer_source_url": f"https://example.com/employer/{index}",
        }
        for index in range(1, count + 1)
    ]
    return {
        "document_id": "retained-source-doc-id",
        "document_title": "Synthetic retained first-DM evidence",
        "document_url": "https://docs.google.com/document/d/retained-source-doc-id/edit",
        "permissions": "owner-only",
        "publication": {
            "cleared_first_dms": len(actions_value["first_dm"]),
            "conditional_researched_drafts": len(rows),
            "strict_planner_withheld_rows": len(actions_value["withheld"]),
            "status": "BLOCKED BY EVIDENCE" if actions_value["coverage"]["reasons"] else "COMPLETE",
        },
        "revision_id": "synthetic-retained-revision",
        "rows": rows,
        "verified_at": "2026-10-04T23:00:00+00:00",
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
    """Emulate the Google boundaries for both DM Docs without real data."""

    scenario: str = "complete"
    texts: dict[str, str] = field(default_factory=lambda: {FIRST_ID: FIRST_TEXT, FOLLOW_ID: FOLLOW_TEXT})
    revisions: dict[str, int] = field(default_factory=lambda: {FIRST_ID: 1, FOLLOW_ID: 1})
    attempts: dict[str, int] = field(default_factory=lambda: {FIRST_ID: 0, FOLLOW_ID: 0})
    writes: dict[str, int] = field(default_factory=lambda: {FIRST_ID: 0, FOLLOW_ID: 0})
    requests: int = 0

    def handle(self, req: httpx.Request) -> httpx.Response:
        """Route a request and inject the scenario's failure at the HTTP boundary."""
        self.requests += 1
        host = req.url.host
        if host == "oauth2.googleapis.com":
            if self.scenario == "oauth":
                return httpx.Response(400, json={"error": "private-refresh-token"})
            return httpx.Response(200, json={"access_token": "private-access-token"})
        parts = [part for part in req.url.path.split("/") if part]
        document = (parts[-2] if parts[-1] == "permissions" else parts[-1]).split(":")[0]
        if host == "www.googleapis.com":
            return self.drive(req, document)
        if host == "docs.googleapis.com":
            return self.docs(req, document)
        raise AssertionError("unexpected HTTP destination")

    def drive(self, req: httpx.Request, document: str) -> httpx.Response:
        """Return privacy metadata, including paginated or published permissions."""
        if req.url.path.endswith("/permissions"):
            owner = {"type": "user", "role": "owner"}
            if self.scenario == "sharing" and document == FIRST_ID:
                if "pageToken" not in req.url.params:
                    return httpx.Response(200, json={"permissions": [owner], "nextPageToken": "page2"})
                return httpx.Response(200, json={"permissions": [{"type": "user", "role": "reader"}]})
            if self.scenario == "published" and document == FOLLOW_ID:
                return httpx.Response(200, json={"permissions": [owner, {"type": "anyone", "role": "reader", "view": "published"}]})
            return httpx.Response(200, json={"permissions": [owner]})
        return httpx.Response(200, json={"id": document,
            "mimeType": report.GOOGLE_DOCS_MIME, "trashed": False, "capabilities": {"canEdit": True}})

    def docs(self, req: httpx.Request, document: str) -> httpx.Response:
        """Apply atomic UTF-16 replacements only after the revision precondition passes."""
        if req.method == "GET":
            text = self.texts[document]
            if self.scenario == "markers" and document == FOLLOW_ID:
                text = text.replace("old follow\n", "[BEGIN AUTOMATED REPORT]\n")
            content = body(text)
            if self.scenario == "rich" and document == FOLLOW_ID:
                content[3]["paragraph"]["elements"].append({"inlineObjectElement": {"inlineObjectId": "fixture"}})
            tab: dict[str, Any] = {"tabProperties": {"tabId": "tab-a"}, "documentTab": {"body": {"content": content}}}
            if self.scenario == "nested" and document == FOLLOW_ID:
                tab["childTabs"] = [tab.copy()]
            return httpx.Response(200, json={"revisionId": f"rev-{self.revisions[document]}", "tabs": [tab]})
        self.attempts[document] += 1
        payload = json.loads(req.content)
        if self.scenario in {"conflict", "conflict_twice"} and document == FIRST_ID and (
            self.attempts[document] == 1 or self.scenario == "conflict_twice"
        ):
            self.revisions[document] += 1
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "The revision ID does not match the current revision."}})
        assert payload["writeControl"]["requiredRevisionId"] == f"rev-{self.revisions[document]}"
        insert = payload["requests"][-1]
        location = insert["insertText"]["location"]
        if len(payload["requests"]) == 2:
            bounds = payload["requests"][0]["deleteContentRange"]["range"]
            assert bounds["endIndex"] > bounds["startIndex"]
        else:
            bounds = {"startIndex": location["index"], "endIndex": location["index"], "tabId": location["tabId"]}
        assert bounds["tabId"] == location["tabId"] == "tab-a"
        assert bounds["startIndex"] == location["index"]
        encoded = self.texts[document].encode("utf-16-le")
        start, end = (bounds[key] - 1 for key in ("startIndex", "endIndex"))
        self.texts[document] = (encoded[:start * 2] + insert["insertText"]["text"].encode("utf-16-le") + encoded[end * 2:]).decode("utf-16-le")
        self.writes[document] += 1
        self.revisions[document] += 1
        return httpx.Response(200, json={"replies": [{}, {}]})


def write_private_json(directory: str, value: Any, *, mode: int = 0o600, name: str) -> Path:
    """Write one synthetic private JSON input with the requested permission bits."""
    path = Path(directory) / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value if isinstance(value, str) else json.dumps(value))
    return path


async def publish(
    service: Services,
    actions_path: Path,
    retained_path: Path,
    argv: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run the real publisher CLI with only the HTTP transport replaced."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = await cli.run(
                argv or [
                    "--actions", str(actions_path),
                    "--retained-evidence", str(retained_path),
                    "--now", NOW,
                ],
                http=http,
            )
        return code, stdout.getvalue(), stderr.getvalue()


async def run_scenarios() -> dict[str, str]:
    """Assert retained evidence, dual preflight, isolation, and fail-closed publication."""
    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV):
        action_value = actions()
        action_path = write_private_json(tmp, action_value, name="actions.json")
        retained_path = write_private_json(tmp, retained(action_value), name="retained.json")

        service = Services()
        code, out, err = await publish(service, action_path, retained_path)
        assert code == 0 and out == "private DM docs: published\n" and err == "", (code, out, err)
        first, follow = service.texts[FIRST_ID], service.texts[FOLLOW_ID]
        assert first.startswith("🧭 Operator note\n[BEGIN AUTOMATED REPORT]\n") and first.endswith("[END AUTOMATED REPORT]\nKeep this note.\n")
        assert follow.startswith("🧭 Operator note\n[BEGIN AUTOMATED REPORT]\n") and follow.endswith("[END AUTOMATED REPORT]\nKeep this note.\n")
        for text in (first, follow):
            assert RUN_TIME in text, text
            assert "Status: COMPLETE" in text and "Withheld rows: 2" in text, text
            assert "Changelog scope: consent_limited_28_days" in text and "Upstream freshness: unknown" in text, text
            assert "synthetic-replier" not in text and "Owner writes reply" not in text and "Synthetic inbound reply" not in text, text
            assert "LinkedIn CONNECTIONS reconciliation" not in text, text
        assert "LinkedIn first-time DM report" in first and "First-DM drafts (owner review required): 1" in first, first
        assert "Conditional retained count: 1" in first and "Strict planner withheld count: 2" in first, first
        assert "CONDITIONAL FIRST-DM DRAFT. NOT CLEARED TO SEND" in first and "Cleared to send: no" in first, first
        assert "Synthetic conditional researched opener 1" in first and "https://example.com/employer/1" in first, first
        assert "https://www.linkedin.com/in/synthetic-drafter" in first and "Saved researched draft: Synthetic researched opener" in first, first
        assert "Connected on: 2026-09-30 (calendar day)" in first and "Research source: https://example.com/employer (Synthetic employer fact)" in first, first
        assert "Read state: unknown" in first and "consent changelog is bounded" in first, first
        assert "LinkedIn follow-up DM report" in follow and "Due follow-ups: 1" in follow, follow
        assert "No follow-up text was generated." in follow, follow
        assert "Previous outbound at: 2026-10-01 12:00:00 CEST (elapsed 94 hours)" in follow, follow
        assert "Verified previous message: Synthetic previous outbound" in follow, follow
        assert "Message sources: inbox.rows[1], changelog.events[1]" in follow, follow
        assert "Action: Owner writes follow-up" in follow and "Read state: unknown" in follow, follow
        assert "Synthetic previous outbound" not in first and "Synthetic conditional researched opener 1" not in follow, (first, follow)
        assert "Saved researched draft:" not in follow, follow
        results["both_docs_complete"] = "passed"

        before = (first, follow)
        code, out, err = await publish(service, action_path, retained_path)
        assert code == 0 and err == "" and (service.texts[FIRST_ID], service.texts[FOLLOW_ID]) == before, (code, err)
        assert service.writes == {FIRST_ID: 2, FOLLOW_ID: 2}
        results["rerun_converges"] = "passed"

        service = Services("conflict")
        code, out, err = await publish(service, action_path, retained_path)
        assert code == 0 and err == "", (code, err)
        assert service.attempts[FIRST_ID] == 2 and service.writes[FIRST_ID] == 1, service.attempts
        assert "Synthetic researched opener" in service.texts[FIRST_ID]
        results["first_doc_conflict_retries"] = "passed"

        service = Services("conflict_twice")
        code, out, err = await publish(service, action_path, retained_path)
        assert code == 2 and err == "private DM docs: Google publication unavailable\n", (code, err)
        assert service.attempts[FIRST_ID] == 2 and service.writes == {FIRST_ID: 0, FOLLOW_ID: 0}, service.writes
        results["second_conflict_fails_closed"] = "passed"

        for scenario in ("sharing", "published", "markers", "rich", "nested"):
            service = Services(scenario)
            code, out, err = await publish(service, action_path, retained_path)
            assert code == 2 and out == "" and err == "private DM docs: Google publication unavailable\n", (scenario, code, out, err)
            assert service.writes == {FIRST_ID: 0, FOLLOW_ID: 0}, (scenario, service.writes)
            assert service.texts == {FIRST_ID: FIRST_TEXT, FOLLOW_ID: FOLLOW_TEXT}, (scenario, service.texts)
            results[f"dual_preflight_{scenario}_refused"] = "passed"

        service = Services()
        markered_retained = retained(action_value)
        markered_retained["rows"][0]["draft"] = "safe\n[BEGIN AUTOMATED REPORT]\nunsafe"
        markered_path = write_private_json(tmp, markered_retained, name="markered-retained.json")
        code, out, err = await publish(service, action_path, markered_path)
        assert code == 1 and out == "" and err == "private DM docs: input invalid\n", (code, err)
        assert service.requests == 0 and service.writes == {FIRST_ID: 0, FOLLOW_ID: 0}
        results["marker_in_retained_draft_refused"] = "passed"

        service = Services()
        code, out, err = await publish(
            service,
            action_path,
            retained_path,
            argv=["--actions", str(action_path), "--now", NOW],
        )
        assert code == 1 and out == "" and err == "private DM docs: input invalid\n", (code, err)
        assert service.requests == 0
        results["retained_evidence_required"] = "passed"

        service = Services()
        lossy_retained = retained(action_value)
        lossy_retained["rows"] = []
        lossy_path = write_private_json(tmp, lossy_retained, name="lossy-retained.json")
        code, out, err = await publish(service, action_path, lossy_path)
        assert code == 1 and out == "" and err == "private DM docs: input invalid\n", (code, err)
        assert service.requests == 0
        results["missing_conditional_rows_refused"] = "passed"

        service = Services()
        stale_retained = retained(action_value)
        stale_retained["publication"]["strict_planner_withheld_rows"] += 1
        stale_path = write_private_json(tmp, stale_retained, name="stale-retained.json")
        code, out, err = await publish(service, action_path, stale_path)
        assert code == 1 and out == "" and err == "private DM docs: input invalid\n", (code, err)
        assert service.requests == 0
        results["stale_retained_counts_refused"] = "passed"

        service = Services()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {**ENV, "GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID": FIRST_ID}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = await cli.run([
                        "--actions", str(action_path),
                        "--retained-evidence", str(retained_path),
                        "--now", NOW,
                    ], http=http)
        assert code == 2 and stdout.getvalue() == "" and stderr.getvalue() == "private DM docs: configuration unavailable\n", (code, stderr.getvalue())
        assert service.requests == 0
        results["identical_destinations_refused"] = "passed"

        service = Services()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {**ENV, "GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID": ""}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(service.handle), follow_redirects=False) as http:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = await cli.run([
                        "--actions", str(action_path),
                        "--retained-evidence", str(retained_path),
                        "--now", NOW,
                    ], http=http)
        assert code == 2 and stdout.getvalue() == "" and stderr.getvalue() == "private DM docs: configuration unavailable\n", (code, stderr.getvalue())
        assert service.requests == 0
        results["missing_configuration"] = "passed"

        service = Services()
        broken = actions()
        del broken["coverage"]
        cases = {
            "shape.json": broken,
            "type.json": {**actions(), "first_dm": "not-a-list"},
            "missing.json": {**actions(), "withheld": [{"profile_url": "x"}]},
            "action.json": {**actions(), "first_dm": [{**actions()["first_dm"][0], "action": "Auto send"}]},
            "empty_read.json": {**actions(), "follow_up": [{**actions()["follow_up"][0], "read_state": ""}]},
            "naive_time.json": {**actions(), "follow_up": [{**actions()["follow_up"][0], "message_at": "2026-10-01T10:00:00"}]},
            "citation.json": {**actions(), "first_dm": [{**actions()["first_dm"][0], "citations": [{"fact": "f", "source_url": "ftp://example.com"}]}]},
        }
        for name, value in cases.items():
            bad = write_private_json(tmp, value, name=name)
            code, out, err = await publish(service, bad, retained_path)
            assert code == 1 and err == "private DM docs: input invalid\n", (name, code, err)
        permissive = write_private_json(tmp, actions(), mode=0o644, name="permissive.json")
        code, out, err = await publish(service, permissive, retained_path)
        assert code == 1 and err == "private DM docs: input invalid\n", (code, err)
        code, out, err = await publish(service, Path(tmp) / "absent.json", retained_path)
        assert code == 1 and err == "private DM docs: input invalid\n", (code, err)
        permissive_retained = write_private_json(tmp, retained(action_value), mode=0o644, name="permissive-retained.json")
        code, out, err = await publish(service, action_path, permissive_retained)
        assert code == 1 and err == "private DM docs: input invalid\n", (code, err)
        code, out, err = await publish(service, action_path, Path(tmp) / "absent-retained.json")
        assert code == 1 and err == "private DM docs: input invalid\n", (code, err)
        code, out, err = await publish(
            service,
            action_path,
            retained_path,
            argv=[
                "--actions", str(action_path),
                "--retained-evidence", str(retained_path),
                "--now", "2026-10-05T08:00:00",
            ],
        )
        assert code == 1 and err == "private DM docs: input invalid\n", (code, err)
        assert service.requests == 0 and service.writes == {FIRST_ID: 0, FOLLOW_ID: 0}
        results["invalid_inputs_refused"] = "passed"

        service = Services()
        supplied_state = actions()
        supplied_state["follow_up"][0]["read_state"] = "read"
        supplied_actions_path = write_private_json(tmp, supplied_state, name="supplied-state-actions.json")
        supplied_retained_path = write_private_json(tmp, retained(supplied_state), name="supplied-state-retained.json")
        code, out, err = await publish(service, supplied_actions_path, supplied_retained_path)
        assert code == 0 and err == "", (code, err)
        assert "Read state: read" in service.texts[FOLLOW_ID], service.texts[FOLLOW_ID]
        results["read_state_preserved"] = "passed"

        service = Services()
        empty = actions()
        empty["first_dm"] = []
        empty["follow_up"] = []
        empty["withheld"] = [{"profile_url": "https://www.linkedin.com/in/synthetic-withheld", "reason": "message_history_ambiguous_or_action_not_due"}] * 1097
        empty["coverage"]["reasons"] = ["inbox_stale_or_missing_acquisition"]
        empty_path = write_private_json(tmp, empty, name="empty.json")
        empty_retained_path = write_private_json(tmp, retained(empty, count=9), name="empty-retained.json")
        code, out, err = await publish(service, empty_path, empty_retained_path)
        assert code == 0 and err == "", (code, err)
        first, follow = service.texts[FIRST_ID], service.texts[FOLLOW_ID]
        assert "Status: BLOCKED" in first and "First-DM drafts (owner review required): 0" in first, first
        assert "Status: BLOCKED" in follow and "Due follow-ups: 0" in follow, follow
        assert "Withheld rows: 1097" in first and "Withheld rows: 1097" in follow, (first, follow)
        assert "Withholding reasons: inbox_stale_or_missing_acquisition" in first, first
        assert "Conditional retained count: 9" in first and "Publication status: BLOCKED BY EVIDENCE" in first, first
        assert "Synthetic conditional researched opener 1" in first and "Synthetic conditional researched opener 9" in first, first
        assert "No follow-up text was generated." in follow and "Synthetic conditional researched opener" not in follow, follow
        results["blocked_queues_preserve_conditional_rows"] = "passed"
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
