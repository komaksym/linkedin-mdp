from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

LINKEDIN_BASE_URL = "https://api.linkedin.com"
DEFAULT_API_VERSION = "202312"


class LinkedInAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, service_error_code: int | None = None):
        self.status_code = status_code
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

    async def snapshot(self, domain: str, *, max_pages: int = 10) -> dict[str, Any]:
        page = await self._get_paged(
            "/rest/memberSnapshotData",
            {"q": "criteria", "domain": domain},
            max_pages=max_pages,
            empty_on_404=True,
        )

        rows: list[Any] = []
        # memberSnapshotData paginates snapshot versions, not disjoint row chunks.
        # The newest element is last; concatenating versions duplicates nearly every row.
        for element in reversed(page.elements):
            if isinstance(element, dict):
                data = element.get("snapshotData")
                if isinstance(data, list):
                    rows = data
                    break

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
        )
        processed = [
            e.get("processedAt")
            for e in page.elements
            if isinstance(e, dict) and isinstance(e.get("processedAt"), int)
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
    ) -> PageResult:
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
                if empty_on_404 and exc.status_code == 404:
                    return PageResult(elements=[], page_count=pages, truncated=False)
                raise

            current = payload.get("elements", [])
            if isinstance(current, list):
                elements.extend(current)
            pages += 1

            next_url = self._next_link(payload)
            if next_url is None:
                return PageResult(elements=elements, page_count=pages, truncated=False)
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

    def _next_link(self, payload: dict[str, Any]) -> str | None:
        paging = payload.get("paging")
        if not isinstance(paging, dict):
            return None
        links = paging.get("links")
        if not isinstance(links, list):
            return None

        for link in links:
            if not isinstance(link, dict) or str(link.get("rel", "")).lower() != "next":
                continue
            href = link.get("href")
            if not isinstance(href, str) or not href:
                continue
            candidate = urljoin(LINKEDIN_BASE_URL, href)
            self._assert_linkedin_url(candidate)
            return candidate
        return None
