"""Read complete private evidence and encrypt it without writing plaintext to disk."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.supabase_client import SupabaseClient

BASE_REVISION = "817fe2da7d433e6b00ac4bf98851bd4b0eef31f4"
DOMAINS = ("CONNECTIONS", "INVITATIONS", "INBOX")
TABLE_COLUMNS = {
    "prospects": "id,lead_id,linkedin_url,attributes,created_at",
    "events": "id,prospect_id,source,event_type,external_key,occurred_at,payload,created_at",
}


def require(condition: bool) -> None:
    """Reject incomplete external evidence without exposing provider data."""
    if not condition:
        raise RuntimeError("private source collection failed")


async def read_table(store: SupabaseClient, table: str, *, page_size: int, max_pages: int) -> dict[str, Any]:
    """Read a stable ordered exact-count table with duplicate and page-cap checks."""
    started = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    total: int | None = None
    for page in range(max_pages):
        offset = len(rows)
        response = await store._request("GET", f"/rest/v1/{table}", params={"select": TABLE_COLUMNS[table], "order": "id.asc", "limit": str(page_size), "offset": str(offset)}, headers={"Prefer": "count=exact"})
        count = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"(?:(\d+)-(\d+)|\*)/(\d+)", count)
        require(match is not None)
        if match is None:
            raise RuntimeError("private source collection failed")
        reported = int(match[3])
        require(total is None or total == reported)
        total = reported
        payload = store._json_list(response)
        require(len(payload) <= page_size and len(rows) + len(payload) <= total)
        if payload:
            require(match[1] is not None and int(match[1]) == offset and int(match[2]) == offset + len(payload) - 1)
        else:
            require(offset == total)
        for row in payload:
            require(isinstance(row, dict))
            identity = row.get("id")
            require(isinstance(identity, str) and bool(identity) and identity not in ids)
            ids.add(identity)
            rows.append(row)
        if len(rows) == total:
            return {"attempted_at": started, "completed_at": datetime.now(UTC).isoformat(), "source_result": "success", "page_count": page + 1, "truncated": False, "row_count": total, "consistency": "stable_count_and_unique_ids; not a transaction snapshot", "rows": rows}
        require(bool(payload))
    raise RuntimeError("private source collection failed")


async def collect_sources(linkedin: LinkedInMDPClient, store: SupabaseClient, *, page_size: int = 500, max_db_pages: int = 200, snapshot_pages: int = 50) -> dict[str, Any]:
    """Collect all three strict snapshots and both existing tables with no writes."""
    require(1 <= page_size <= 1000 and 1 <= max_db_pages <= 200 and 1 <= snapshot_pages <= 50)
    snapshots: dict[str, Any] = {}
    for domain in DOMAINS:
        started = datetime.now(UTC).isoformat()
        snapshot = await linkedin.snapshot(domain, max_pages=snapshot_pages, strict_elements=True)
        require(snapshot.get("domain") == domain and snapshot.get("truncated") is False)
        pages = snapshot.get("page_count")
        require(type(pages) is int and pages > 0)
        elements = snapshot.get("raw_elements")
        require(isinstance(elements, list) and all(isinstance(element, dict) and element.get("snapshotDomain") == domain and isinstance(element.get("snapshotData"), list) for element in elements))
        rows = snapshot.get("rows")
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows))
        snapshots[domain] = {"attempted_at": started, "completed_at": datetime.now(UTC).isoformat(), "source_result": "success", "provider_generated_at": None, "upstream_freshness": "unknown unless established by retained provider metadata", **snapshot}
    tables = {}
    for table in TABLE_COLUMNS:
        tables[table] = await read_table(store, table, page_size=page_size, max_pages=max_db_pages)
    return {"schema_version": 1, "base_revision": BASE_REVISION, "collected_at": datetime.now(UTC).isoformat(), "snapshots": snapshots, **tables}


def encrypt_bundle(bundle: dict[str, Any], certificate: Path, output: Path) -> None:
    """Send private JSON through stdin to AES-256 CMS and create only ciphertext."""
    require(certificate.is_file() and not output.exists())
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    result = subprocess.run(["openssl", "cms", "-encrypt", "-aes256", "-binary", "-outform", "DER", str(certificate)], input=encoded, capture_output=True, check=False)
    require(result.returncode == 0 and bool(result.stdout))
    with output.open("xb") as target:
        target.write(result.stdout)
    output.chmod(0o600)


async def export(certificate: Path, output: Path) -> None:
    """Load existing runner credentials, collect privately, and close both clients."""
    linkedin = LinkedInMDPClient.from_env()
    try:
        store = SupabaseClient.from_env()
        try:
            encrypt_bundle(await collect_sources(linkedin, store), certificate, output)
        finally:
            await store.aclose()
    finally:
        await linkedin.aclose()


def main() -> int:
    """Print only fixed verdicts, suppressing private exceptions and subprocess output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(export(args.certificate, args.output))
    except Exception:  # noqa: BLE001
        print("private source export: failed")
        return 1
    print("private source export: encrypted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
