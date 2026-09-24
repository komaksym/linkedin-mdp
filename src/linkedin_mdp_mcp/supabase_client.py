from __future__ import annotations

import os
from typing import Any, Sequence
from urllib.parse import urljoin, urlparse

import httpx


class SupabaseAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Supabase API error {status_code}: {message}")


class SupabaseClient:
    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        page_size: int = 1000,
    ) -> None:
        parsed = urlparse(base_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("SUPABASE_URL must be an https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("SUPABASE_URL must contain only scheme, host, and optional path")
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")

        key = service_role_key.strip()
        if not key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is empty")

        self._base_url = base_url.rstrip("/") + "/"
        self._allowed_scheme = parsed.scheme
        self._allowed_host = parsed.netloc
        self._key = key
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
        self._page_size = page_size

    @classmethod
    def from_env(cls) -> "SupabaseClient":
        base_url = os.getenv("SUPABASE_URL")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not base_url:
            raise RuntimeError("Missing SUPABASE_URL")
        if not service_role_key:
            raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY")
        return cls(base_url, service_role_key)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def prospect_ids_by_linkedin_key(self) -> dict[str, str]:
        result: dict[str, str] = {}
        offset = 0

        while True:
            response = await self._request(
                "GET",
                "/rest/v1/prospects",
                params={
                    "select": "id,linkedin_url_key",
                    "linkedin_url_key": "not.is.null",
                    "order": "id.asc",
                    "limit": str(self._page_size),
                    "offset": str(offset),
                },
            )
            payload = self._json_list(response)

            for item in payload:
                if not isinstance(item, dict):
                    raise SupabaseAPIError(response.status_code, "expected prospect objects")
                prospect_id = item.get("id")
                linkedin_url_key = item.get("linkedin_url_key")
                if not isinstance(prospect_id, str) or not isinstance(linkedin_url_key, str):
                    raise SupabaseAPIError(
                        response.status_code,
                        "prospect response is missing id or linkedin_url_key",
                    )
                existing = result.get(linkedin_url_key)
                if existing is not None and existing != prospect_id:
                    raise SupabaseAPIError(
                        response.status_code,
                        "duplicate linkedin_url_key returned for different prospects",
                    )
                result[linkedin_url_key] = prospect_id

            if len(payload) < self._page_size:
                return result
            offset += self._page_size

    async def insert_events_ignore_duplicates(
        self,
        events: Sequence[dict[str, Any]],
    ) -> int:
        if not events:
            return 0

        response = await self._request(
            "POST",
            "/rest/v1/events",
            params={"on_conflict": "source,external_key"},
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            json=list(events),
        )
        payload = self._json_list(response)
        return len(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        url = self._make_url(path)
        request_headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)

        try:
            response = await self._http.request(
                method,
                url,
                params=params,
                headers=request_headers,
                json=json,
            )
        except httpx.HTTPError as exc:
            raise SupabaseAPIError(0, "network failure") from exc

        if not response.is_success:
            raise SupabaseAPIError(response.status_code, response.reason_phrase or "request failed")
        return response

    def _make_url(self, path: str) -> str:
        if not path.startswith("/rest/v1/"):
            raise ValueError("Only Supabase /rest/v1/ endpoints are allowed")
        candidate = urljoin(self._base_url, path.lstrip("/"))
        parsed = urlparse(candidate)
        if parsed.scheme != self._allowed_scheme or parsed.netloc != self._allowed_host:
            raise SupabaseAPIError(0, "refusing to send Supabase credentials to an unexpected host")
        return candidate

    @staticmethod
    def _json_list(response: httpx.Response) -> list[Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SupabaseAPIError(response.status_code, "expected JSON response") from exc
        if not isinstance(payload, list):
            raise SupabaseAPIError(response.status_code, "expected a JSON array")
        return payload
