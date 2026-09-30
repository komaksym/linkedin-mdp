"""Manual, aggregate-only INVITATIONS history reconciliation command."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.invitation_sync import reconcile_invitations
from linkedin_mdp_mcp.supabase_client import SupabaseClient


async def main() -> None:
    """Reconcile one complete snapshot and print only aggregate counts."""
    linkedin: LinkedInMDPClient | None = None
    supabase: SupabaseClient | None = None
    try:
        linkedin = LinkedInMDPClient.from_env()
        supabase = SupabaseClient.from_env()
        summary = await reconcile_invitations(linkedin, supabase)
        print(json.dumps(asdict(summary), sort_keys=True))
    finally:
        if supabase is not None:
            await supabase.aclose()
        if linkedin is not None:
            await linkedin.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print(
            json.dumps({"error": "invitation reconciliation failed"}), file=sys.stderr
        )
        raise SystemExit(1) from None
