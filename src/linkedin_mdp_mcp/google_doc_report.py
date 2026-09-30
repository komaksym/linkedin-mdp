"""Publish private aggregate reports into a marker-owned Google Doc region."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx

BEGIN_MARKER = "[BEGIN AUTOMATED REPORT]"
END_MARKER = "[END AUTOMATED REPORT]"
GOOGLE_DOCS_MIME = "application/vnd.google-apps.document"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/drive.metadata.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_URL = "https://www.googleapis.com/drive/v3"
DOCS_URL = "https://docs.googleapis.com/v1"


class GoogleReportError(RuntimeError):
    """Represent a sanitized Google configuration, privacy, or publication failure."""


@dataclass(frozen=True)
class GoogleDocConfig:
    """Hold the OAuth credentials and pre-existing target document identifier."""

    client_id: str
    client_secret: str
    refresh_token: str
    document_id: str

    @classmethod
    def from_env(cls) -> "GoogleDocConfig":
        """Load required OAuth values without including them in error messages."""
        names = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN", "GOOGLE_REPORT_DOCUMENT_ID")
        values = [os.getenv(name) for name in names]
        if any(value is None or not value.strip() for value in values):
            raise GoogleReportError("configuration unavailable")
        return cls(values[0] or "", values[1] or "", values[2] or "", values[3] or "")


@dataclass(frozen=True)
class ReportCounts:
    """Hold the six verified counts from a completed reconciliation."""

    fetched_rows: int
    matched_rows: int
    unique_matched: int
    unmatched_rows: int
    inserted: int
    already_present: int


@dataclass(frozen=True)
class ReportResult:
    """Describe run status, actual aware run time, and available aggregate counts."""

    status: Literal["COMPLETE", "BLOCKED", "FAILED"]
    run_at: datetime
    counts: ReportCounts | None

    def __post_init__(self) -> None:
        """Reject naive timestamps and status/count combinations that mislead readers."""
        if self.run_at.tzinfo is None or self.run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        if (self.status == "COMPLETE") != (self.counts is not None):
            raise ValueError("counts are present only for complete runs")


def render_report(result: ReportResult) -> str:
    """Render a fixed aggregate-only body suitable for the managed region."""
    from zoneinfo import ZoneInfo

    local_time = result.run_at.astimezone(ZoneInfo("Europe/Warsaw"))
    lines = [
        "LinkedIn CONNECTIONS reconciliation",
        f"Status: {result.status}",
        f"Run time: {local_time:%Y-%m-%d %H:%M:%S %Z}",
    ]
    if result.counts is None:
        lines.extend(f"{label}: unknown" for label in _COUNT_LABELS.values())
    else:
        values = as_counts(result.counts)
        lines.extend(f"{label}: {values[key]}" for key, label in _COUNT_LABELS.items())
    if result.status == "COMPLETE":
        lines.append("Next action: review the aggregate counts; investigate unmatched rows if needed.")
    elif result.status == "BLOCKED":
        lines.append("Next action: restore a complete LinkedIn CONNECTIONS snapshot, then rerun.")
    if result.status == "FAILED":
        lines.append("Database writes may have happened; their final state is unknown.")
        lines.append("Next action: verify Supabase state before retrying reconciliation.")
    return "\n".join(lines) + "\n"


_COUNT_LABELS = {
    "fetched_rows": "Fetched rows",
    "matched_rows": "Matched rows",
    "unique_matched": "Unique matched connections",
    "unmatched_rows": "Unmatched rows",
    "inserted": "Newly inserted events",
    "already_present": "Already-present events",
}


def as_counts(counts: ReportCounts) -> dict[str, int]:
    """Return count fields keyed by their stable internal names."""
    return {key: getattr(counts, key) for key in _COUNT_LABELS}


class GoogleDocPublisher:
    """Refresh OAuth and safely replace only the marked report paragraphs."""

    def __init__(self, config: GoogleDocConfig, *, http: httpx.AsyncClient | None = None) -> None:
        """Initialize an HTTPS-only publisher with redirects disabled."""
        self.config = config
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)

    async def aclose(self) -> None:
        """Close the publisher's internally owned HTTP client."""
        if self._owns_http:
            await self.http.aclose()

    async def prepare(self) -> None:
        """Authenticate and verify document privacy and marker structure before data access."""
        token = await self._access_token()
        await self._preflight_and_read(token)

    async def _preflight_and_read(self, token: str) -> dict[str, Any]:
        """Check Drive policy and validate the current managed region in Docs."""
        await self._preflight(token)
        document = await self._read_document(token)
        _report_range(document)
        return document

    async def publish(self, result: ReportResult) -> None:
        """Preflight, inspect marker bounds, and atomically replace the report once."""
        token = await self._access_token()
        document = await self._preflight_and_read(token)
        for attempt in range(2):
            tab_id, start, end = _report_range(document)
            # Recheck immediately before the content mutation, including on retry.
            await self._preflight(token)
            requests: list[dict[str, Any]] = [
                {"insertText": {"location": {"index": start, "tabId": tab_id}, "text": render_report(result)}},
            ]
            if start < end:
                requests.insert(0, {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end, "tabId": tab_id}}})
            response = await self._request(
                "POST",
                f"{DOCS_URL}/documents/{quote(self.config.document_id, safe='')}:batchUpdate",
                token,
                json={"requests": requests, "writeControl": {"requiredRevisionId": document["revisionId"]}},
                allow_conflict=True,
            )
            if attempt == 0 and _is_revision_conflict(response):
                document = await self._preflight_and_read(token)
                continue
            self._require_success(response)
            return

    async def _access_token(self) -> str:
        """Exchange the configured refresh grant for a short-lived bearer token."""
        response = await self._request(
            "POST",
            TOKEN_URL,
            None,
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        self._require_success(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleReportError("authentication unavailable") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise GoogleReportError("authentication unavailable")
        return token

    async def _preflight(self, token: str) -> dict[str, Any]:
        """Require an editable Google Doc with a complete owner-only permission view."""
        file_url = f"{DRIVE_URL}/files/{quote(self.config.document_id, safe='')}"
        response = await self._request(
            "GET", file_url, token,
            params={"fields": "id,mimeType,trashed,driveId,capabilities(canEdit)"},
        )
        self._require_success(response)
        metadata = self._json_object(response)
        if (
            metadata.get("id") != self.config.document_id
            or metadata.get("mimeType") != GOOGLE_DOCS_MIME
            or metadata.get("trashed") is not False
            or metadata.get("driveId") is not None
            or not isinstance(metadata.get("capabilities"), dict)
            or metadata["capabilities"].get("canEdit") is not True
        ):
            raise GoogleReportError("document preflight failed")
        page_token: str | None = None
        owners = 0
        while True:
            params: dict[str, str] = {"fields": "nextPageToken,permissions(type,role,deleted)"}
            if page_token:
                params["pageToken"] = page_token
            permissions_response = await self._request(
                "GET", f"{DRIVE_URL}/files/{quote(self.config.document_id, safe='')}/permissions", token,
                params={**params, "includePermissionsForView": "published"},
            )
            self._require_success(permissions_response)
            permissions = self._json_object(permissions_response)
            entries = permissions.get("permissions")
            if not isinstance(entries, list):
                raise GoogleReportError("permission inspection incomplete")
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("deleted", False) is not False or entry.get("type") != "user":
                    raise GoogleReportError("document sharing is not private")
                if entry.get("role") == "owner":
                    owners += 1
                else:
                    raise GoogleReportError("document sharing is not private")
            next_token = permissions.get("nextPageToken")
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token or next_token == page_token:
                raise GoogleReportError("permission inspection incomplete")
            page_token = next_token
        if owners != 1:
            raise GoogleReportError("document sharing is not private")
        return metadata

    async def _read_document(self, token: str) -> dict[str, Any]:
        """Read document structure and require a parseable revision-controlled body."""
        response = await self._request(
            "GET", f"{DOCS_URL}/documents/{quote(self.config.document_id, safe='')}", token,
            params={"includeTabsContent": "true"},
        )
        self._require_success(response)
        return self._json_object(response)

    async def _request(self, method: str, url: str, token: str | None, *, allow_conflict: bool = False, **kwargs: Any) -> httpx.Response:
        """Send a request only to fixed HTTPS Google origins with redirects disabled."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"oauth2.googleapis.com", "www.googleapis.com", "docs.googleapis.com"}:
            raise GoogleReportError("Google endpoint unavailable")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GoogleReportError("Google service unavailable") from exc
        if response.is_redirect:
            raise GoogleReportError("Google redirect refused")
        if response.status_code >= 400 and not (allow_conflict and response.status_code in {400, 409}):
            raise GoogleReportError("Google service rejected the request")
        return response

    @staticmethod
    def _require_success(response: httpx.Response) -> None:
        """Reject every non-success response with a fixed, data-free error."""
        if not response.is_success:
            raise GoogleReportError("Google service rejected the request")

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        """Decode a response as an object without surfacing provider text."""
        try:
            value = response.json()
        except ValueError as exc:
            raise GoogleReportError("Google response invalid") from exc
        if not isinstance(value, dict):
            raise GoogleReportError("Google response invalid")
        return value


def _report_range(document: dict[str, Any]) -> tuple[str, int, int]:
    """Find one ordered same-tab marker pair and return its UTF-16 content bounds."""
    if not isinstance(document.get("revisionId"), str) or not document["revisionId"]:
        raise GoogleReportError("document structure invalid")
    tabs = document.get("tabs")
    if not isinstance(tabs, list):
        raise GoogleReportError("document structure invalid")
    if any(isinstance(tab, dict) and tab.get("childTabs") for tab in tabs):
        raise GoogleReportError("nested document tabs are unsupported")
    matches: list[tuple[str, int, int, list[tuple[int, int, str]]]] = []
    begin_count = 0
    end_count = 0
    for tab in tabs:
        if not isinstance(tab, dict) or not isinstance(tab.get("documentTab"), dict):
            raise GoogleReportError("document structure invalid")
        props = tab.get("tabProperties")
        tab_id = props.get("tabId") if isinstance(props, dict) else None
        body = tab["documentTab"].get("body")
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(tab_id, str) or not isinstance(content, list):
            raise GoogleReportError("document structure invalid")
        paragraphs: list[tuple[str, int, int]] = []
        structures: list[tuple[int, int, str]] = []
        for item in content:
            if not isinstance(item, dict):
                raise GoogleReportError("document structure invalid")
            start, end = item.get("startIndex"), item.get("endIndex")
            if "sectionBreak" in item and start is None and isinstance(end, int):
                structures.append((0, end, "sectionBreak"))
                continue
            if "paragraph" not in item and start is None and isinstance(end, int):
                structures.append((end, end, "terminal"))
                continue
            if not isinstance(start, int) or not isinstance(end, int):
                raise GoogleReportError("document structure invalid")
            if "paragraph" not in item:
                kind = "sectionBreak" if "sectionBreak" in item or item == {"endIndex": end} else "structure"
                structures.append((start, end, kind))
                continue
            paragraph = item["paragraph"]
            elements = paragraph.get("elements") if isinstance(paragraph, dict) else None
            if not isinstance(elements, list):
                raise GoogleReportError("document structure invalid")
            chunks = []
            for element in elements:
                text_run = element.get("textRun") if isinstance(element, dict) else None
                value = text_run.get("content") if isinstance(text_run, dict) else None
                chunks.append(value if isinstance(value, str) else "<RICH>")
            text = "".join(chunks)
            if "<RICH>" in text:
                structures.append((start, end, "rich paragraph element"))
            paragraphs.append((text, start, end))
            begin_count += text == BEGIN_MARKER + "\n"
            end_count += text == END_MARKER + "\n"
        for i, (text, _start, marker_end) in enumerate(paragraphs):
            if text != BEGIN_MARKER + "\n":
                continue
            for end_text, end_start, _end_end in paragraphs[i + 1:]:
                if end_text == END_MARKER + "\n":
                    matches.append((tab_id, marker_end, end_start, structures))
                    break
    if begin_count != 1 or end_count != 1 or len(matches) != 1:
        raise GoogleReportError("document markers invalid")
    tab_id, start, end, structures = matches[0]
    if end < start or any(left < end and right > start for left, right, _kind in structures):
        raise GoogleReportError("managed region contains rich structure")
    return tab_id, start, end


def _is_revision_conflict(response: httpx.Response) -> bool:
    """Recognize only Google revision precondition failures without exposing payloads."""
    if response.status_code == 409:
        return True
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return False
    if error.get("status") == "FAILED_PRECONDITION" or error.get("reason") == "revisionIdMismatch":
        return True
    message = error.get("message")
    return isinstance(message, str) and "revision id" in message.lower() and "does not match" in message.lower()
