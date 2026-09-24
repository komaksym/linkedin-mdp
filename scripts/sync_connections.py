from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.connection_sync import reconcile_connections
from linkedin_mdp_mcp.supabase_client import SupabaseClient


async def main() -> None:
    linkedin = LinkedInMDPClient.from_env()
    supabase = None
    try:
        supabase = SupabaseClient.from_env()
        summary = await reconcile_connections(linkedin, supabase)
    finally:
        if supabase is not None:
            await supabase.aclose()
        await linkedin.aclose()

    print(json.dumps(asdict(summary), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
