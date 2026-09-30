"""Run reconciliation and publish only sanitized status to the console."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, Literal

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient
from linkedin_mdp_mcp.connection_sync import ConnectionSyncError, reconcile_connections
from linkedin_mdp_mcp.google_doc_report import (
    GoogleDocConfig,
    GoogleDocPublisher,
    ReportCounts,
    ReportResult,
)
from linkedin_mdp_mcp.supabase_client import SupabaseClient


async def run() -> int:
    """Reconcile, publish COMPLETE/BLOCKED/FAILED, and return a generic exit status."""
    try:
        config = GoogleDocConfig.from_env()
        publisher = GoogleDocPublisher(config)
    except Exception:
        print("morning report: configuration unavailable", file=sys.stderr)
        return 2

    linkedin: Any = None
    supabase: Any = None
    result: ReportResult
    code = 0
    try:
        # Google auth and all document preflight checks happen before reconciliation.
        await publisher.prepare()
        guarded_linkedin = None
        try:
            linkedin = LinkedInMDPClient.from_env()
            guarded_linkedin = _SnapshotStage(linkedin)
            supabase = SupabaseClient.from_env()
            summary = await reconcile_connections(guarded_linkedin, supabase)
        except LinkedInAPIError:
            result = ReportResult("BLOCKED", datetime.now(timezone.utc), None)
            code = 1
        except ConnectionSyncError:
            status: Literal["BLOCKED", "FAILED"] = "BLOCKED" if guarded_linkedin and guarded_linkedin.snapshot_incomplete else "FAILED"
            result = ReportResult(status, datetime.now(timezone.utc), None)
            code = 1
        except Exception:
            result = ReportResult("BLOCKED" if linkedin is None else "FAILED", datetime.now(timezone.utc), None)
            code = 1
        else:
            result = ReportResult(
                "COMPLETE",
                datetime.now(timezone.utc),
                ReportCounts(
                    fetched_rows=summary.fetched_rows,
                    matched_rows=summary.matched_rows,
                    unique_matched=summary.unique_matched,
                    unmatched_rows=summary.unmatched_rows,
                    inserted=summary.inserted,
                    already_present=summary.already_present,
                ),
            )
        await publisher.publish(result)
    except Exception:
        print("morning report: Google publication unavailable", file=sys.stderr)
        return 2
    finally:
        if supabase is not None:
            try:
                await supabase.aclose()
            except Exception:
                code = 1
        if linkedin is not None:
            try:
                await linkedin.aclose()
            except Exception:
                code = 1
        try:
            await publisher.aclose()
        except Exception:
            print("morning report: cleanup unavailable", file=sys.stderr)
            code = 2
    print(f"morning report: {result.status.lower()}")
    return code


class _SnapshotStage:
    """Track whether reconciliation rejected a provider snapshot before database work."""

    def __init__(self, client: Any) -> None:
        """Wrap the provider while preserving its snapshot and close methods."""
        self._client = client
        self.snapshot_incomplete = False

    async def snapshot(self, domain: str, *, max_pages: int = 10) -> Any:
        """Record whether the provider returned an incomplete snapshot."""
        snapshot = await self._client.snapshot(domain, max_pages=max_pages)
        self.snapshot_incomplete = (
            not isinstance(snapshot, dict)
            or snapshot.get("truncated") is not False
            or not isinstance(snapshot.get("page_count"), int)
            or snapshot.get("page_count", 0) < 1
            or not isinstance(snapshot.get("rows"), list)
        )
        return snapshot


def main() -> None:
    """Run the asynchronous reporting command and exit with its status only."""
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
