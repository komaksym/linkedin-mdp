from __future__ import annotations

import asyncio
import json

from .client import LinkedInMDPClient


async def _run() -> None:
    client = LinkedInMDPClient.from_env()
    try:
        auth = await client.authorization_status()
        connections = await client.snapshot("CONNECTIONS", max_pages=2)
        invitations = await client.snapshot("INVITATIONS", max_pages=2)
        inbox = await client.snapshot("INBOX", max_pages=2)
        changes = await client.changelog(count=10, max_pages=1)

        # Deliberately print counts/metadata instead of private member rows.
        summary = {
            "authorization_keys": sorted(auth.keys()),
            "connections_rows": len(connections["rows"]),
            "invitations_rows": len(invitations["rows"]),
            "inbox_rows": len(inbox["rows"]),
            "changelog_events": len(changes["events"]),
            "changelog_next_start_time": changes["next_start_time"],
        }
        print(json.dumps(summary, indent=2))
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
