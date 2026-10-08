from __future__ import annotations

import argparse
import asyncio
import os

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.inbox_sync import reconcile_inbox
from linkedin_mdp_mcp.supabase_client import SupabaseClient


async def run(apply: bool, account_profile_url: str | None) -> int:
    """Run inbox reconciliation and emit only a fixed status token."""
    linkedin = None
    supabase = None
    failed = False
    try:
        account_url = account_profile_url or os.getenv("LINKEDIN_ACCOUNT_PROFILE_URL")
        if not account_url:
            failed = True
        else:
            linkedin = LinkedInMDPClient.from_env()
            supabase = SupabaseClient.from_env()
            await reconcile_inbox(
                linkedin,
                supabase,
                account_profile_url=account_url,
                dry_run=not apply,
            )
    except Exception:  # noqa: BLE001
        failed = True
    for client in (supabase, linkedin):
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            failed = True
    if failed:
        print("FAILED")
        return 1
    print("APPLIED" if apply else "DRY_RUN_COMPLETE")
    return 0


def main() -> None:
    """Parse explicit apply intent and run one inbox sync operation."""
    parser = argparse.ArgumentParser(description="Plan or apply LinkedIn MDP inbox evidence")
    parser.add_argument("--apply", action="store_true", help="persist planned message evidence")
    parser.add_argument("--account-profile-url", help="LinkedIn account /in/ profile URL")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply, args.account_profile_url)))


if __name__ == "__main__":
    main()
