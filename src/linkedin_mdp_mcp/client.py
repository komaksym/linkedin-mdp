from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

LINKEDIN_BASE_URL = "https://api.linkedin.com"
DEFAULT_API_VERSION = "202312"
SNAPSHOT_EXHAUSTED_MESSAGES = frozenset({
    "No data found for this domain and memberId",
    "No data found for this memberId",
})


class LinkedInAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, service_error_code: int | None = None):
        """Keep the provider message available while preserving the public error text."""
        self.status_code = status_code
        self.message = message
        self.service_error_code = service_error_code
        super().__init__(f"LinkedIn API error {status_code}: {message}")


@dataclass(frozen=True)
class PageResult:
    elements: list[Any]
    page_count: int
    truncated: bool


class LinkedInMDPClient:
    """Tiny GET-only client for the exact Member Data Portability endpoints we expose."""

    def __init__(
        self,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        token = access_token.removeprefix("Bearer ").strip()
        if not token:
            raise ValueError("LinkedIn access token is empty")
        if len(api_version) != 6 or not api_version.isdigit():
            raise ValueError("LINKEDIN_API_VERSION must be YYYYMM, for example 202312")

        self._token = token
        self.api_version = api_version
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)
        self._max_retries = max_retries
        self._allowed_host = urlparse(LINKEDIN_BASE_URL).netloc

    @classmethod
    def from_env(cls) -> "LinkedInMDPClient":
        token = os.getenv("LINKEDIN_ACCESS_TOKEN") or os.getenv("LINKEDIN_TOKEN")
        if not token:
            raise RuntimeError(
                "Missing LINKEDIN_ACCESS_TOKEN. Put the token in the server environment, not in source code."
            )
        return cls(token, os.getenv("LINKEDIN_API_VERSION", DEFAULT_API_VERSION))

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def authorization_status(self) -> dict[str, Any]:
        return await self._get_json(
            "/rest/memberAuthorizations",
            {"q": "memberAndApplication"},
        )

    async def snapshot(
        self,
        domain: str,
        *,
        max_pages: int = 10,
        strict_elements: bool = False,
    ) -> dict[str, Any]:
        """Return all exact-distinct rows observed across paginated snapshot elements."""
        page = await self._get_paged(
            "/rest/memberSnapshotData",
            {"q": "criteria", "domain": domain},
            max_pages=max_pages,
            empty_on_404=True,
            strict_elements=strict_elements,
        )

        rows: list[Any] = []
        seen: set[str] = set()
        for element in page.elements:
            if not isinstance(element, dict):
                if strict_elements:
                    raise LinkedInAPIError(0, "malformed snapshot element")
                continue
            if strict_elements and element.get("snapshotDomain") != domain:
                raise LinkedInAPIError(0, "snapshot element domain does not match request")
            data = element.get("snapshotData")
            if not isinstance(data, list):
                if strict_elements:
                    raise LinkedInAPIError(0, "malformed snapshot data list")
                continue
            if strict_elements and element.get("source_result") in ("not_found", "unavailable"):
                raise LinkedInAPIError(0, "snapshot source is unavailable")
            for row in data:
                key = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        return {
            "api_version": self.api_version,
            "domain": domain,
            "rows": rows,
            "raw_elements": page.elements,
            "page_count": page.page_count,
            "truncated": page.truncated,
        }

    async def changelog(
        self,
        *,
        start_time: int | None = None,
        count: int = 50,
        max_pages: int = 5,
    ) -> dict[str, Any]:
        if not 1 <= count <= 50:
            raise ValueError("count must be between 1 and 50")
        params: dict[str, Any] = {"q": "memberAndApplication", "count": count}
        if start_time is not None:
            params["startTime"] = start_time

        page = await self._get_paged(
            "/rest/memberChangeLogs",
            params,
            max_pages=max_pages,
            empty_on_404=False,
            strict_envelope=True,
        )
        processed = [
            value
            for e in page.elements
            if isinstance(e, dict) and isinstance(value := e.get("processedAt"), int)
        ]
        return {
            "api_version": self.api_version,
            "events": page.elements,
            "next_start_time": max(processed) if processed else start_time,
            "page_count": page.page_count,
            "truncated": page.truncated,
        }

    async def _get_paged(
        self,
        path: str,
        params: dict[str, Any],
        *,
        max_pages: int,
        empty_on_404: bool,
        strict_elements: bool = False,
        strict_envelope: bool = False,
    ) -> PageResult:
        """Fetch every page and validate requested snapshot or source envelopes."""
        if not 1 <= max_pages <= 50:
            raise ValueError("max_pages must be between 1 and 50")

        url = self._make_url(path)
        query: dict[str, Any] | None = params
        elements: list[Any] = []
        pages = 0

        while pages < max_pages:
            try:
                payload = await self._get_json_url(url, query)
            except LinkedInAPIError as exc:
                if empty_on_404 and exc.status_code == 404 and pages == 0:
                    return PageResult(elements=[], page_count=0, truncated=False)
                if (
                    empty_on_404
                    and exc.status_code == 404
                    and pages > 0
                    and exc.message.removesuffix(".") in SNAPSHOT_EXHAUSTED_MESSAGES
                ):
                    return PageResult(elements=elements, page_count=pages, truncated=False)
                raise

            current = payload.get("elements", [])
            if strict_envelope and ("elements" not in payload or not isinstance(current, list)):
                raise LinkedInAPIError(0, "malformed changelog elements list")
            if strict_elements and not isinstance(current, list):
                raise LinkedInAPIError(0, "expected a snapshot elements list")
            if isinstance(current, list):
                elements.extend(current)
            if strict_elements:
                if payload.get("source_result") in ("not_found", "unavailable"):
                    raise LinkedInAPIError(0, "snapshot source is unavailable")
                self._validate_snapshot_paging(payload)
            pages += 1

            strict_pages = strict_elements or strict_envelope
            page_context = "changelog" if strict_envelope else "snapshot"
            next_url = self._next_link(payload, strict=strict_pages, context=page_context)
            if next_url is None:
                return PageResult(elements=elements, page_count=pages, truncated=False)
            if strict_pages:
                parsed = urlparse(next_url)
                if parsed.path != path:
                    raise LinkedInAPIError(0, f"{page_context} next link changed endpoint")
                next_params = parse_qs(parsed.query, keep_blank_values=True)
                if strict_envelope and "startTime" not in params and "startTime" in next_params:
                    raise LinkedInAPIError(0, "changelog next link changed request scope")
                for key, value in params.items():
                    expected = [str(value)]
                    if key in next_params and next_params[key] != expected:
                        raise LinkedInAPIError(0, f"{page_context} next link changed request scope")
                    next_params[key] = expected
                next_url = parsed._replace(query=urlencode(next_params, doseq=True)).geturl()
            url = next_url
            query = None

        return PageResult(elements=elements, page_count=pages, truncated=True)

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._get_json_url(self._make_url(path), params)

    async def _get_json_url(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self._assert_linkedin_url(url)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "LinkedIn-Version": self.api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise LinkedInAPIError(0, f"network failure: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue

            payload = self._safe_json(response)
            if response.is_success:
                if not isinstance(payload, dict):
                    raise LinkedInAPIError(response.status_code, "expected a JSON object")
                return payload

            message = payload.get("message") if isinstance(payload, dict) else None
            service_code = payload.get("serviceErrorCode") if isinstance(payload, dict) else None
            if response.status_code not in {429, 500, 502, 503, 504} or attempt >= self._max_retries:
                raise LinkedInAPIError(
                    response.status_code,
                    str(message or response.reason_phrase),
                    service_code if isinstance(service_code, int) else None,
                )
            await asyncio.sleep(0.25 * (2**attempt))

        raise AssertionError("unreachable")

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"message": response.text[:500] or response.reason_phrase}

    @staticmethod
    def _make_url(path: str) -> str:
        if not path.startswith("/rest/"):
            raise ValueError("Only LinkedIn /rest/ endpoints are allowed")
        return urljoin(LINKEDIN_BASE_URL, path)

    def _assert_linkedin_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != self._allowed_host:
            raise LinkedInAPIError(0, "refusing to send LinkedIn credentials to an unexpected host")

    def _next_link(self, payload: dict[str, Any], *, strict: bool = False, context: str = "snapshot") -> str | None:
        """Return the single validated next link, or no link at the end."""
        paging = payload.get("paging")
        if not isinstance(paging, dict):
            if strict and "paging" in payload:
                raise LinkedInAPIError(0, f"malformed {context} paging object")
            return None
        links = paging.get("links")
        if not isinstance(links, list):
            if strict and "links" in paging:
                raise LinkedInAPIError(0, f"malformed {context} paging links")
            return None

        if strict:
            next_links: list[str] = []
            for link in links:
                if not isinstance(link, dict):
                    raise LinkedInAPIError(0, f"malformed {context} paging link")
                rel = link.get("rel")
                href = link.get("href")
                if not isinstance(rel, str) or not isinstance(href, str) or not href:
                    raise LinkedInAPIError(0, f"malformed {context} paging link")
                if rel.lower() == "next":
                    next_links.append(href)
            if len(next_links) > 1:
                raise LinkedInAPIError(0, f"ambiguous {context} next links")
            if next_links:
                candidate = urljoin(LINKEDIN_BASE_URL, next_links[0])
                self._assert_linkedin_url(candidate)
                return candidate
            return None

        for link in links:
            if not isinstance(link, dict):
                continue
            if str(link.get("rel", "")).lower() != "next":
                continue
            href = link.get("href")
            if not isinstance(href, str) or not href:
                continue
            candidate = urljoin(LINKEDIN_BASE_URL, href)
            self._assert_linkedin_url(candidate)
            return candidate
        return None

    @staticmethod
    def _validate_snapshot_paging(payload: dict[str, Any]) -> None:
        """Reject malformed snapshot element containers before aggregation."""
        if "elements" not in payload or not isinstance(payload.get("elements"), list):
            raise LinkedInAPIError(0, "malformed snapshot elements")
        if "page_count" in payload:
            count = payload.get("page_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise LinkedInAPIError(0, "malformed snapshot page count")
