"""Replay normal snapshot exhaustion through the unchanged full reporting pipeline."""

import asyncio
import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

import httpx


async def main():
    """Save the observed verdict, then assert documented exhaustion yields COMPLETE."""
    fixture = runpy.run_path("tests/e2e_doc_report.py", run_name="e2e_fixture")

    class EndOfData(fixture["Services"]):
        """Use synthetic records and the verified live terminal HTTP response shape."""

        def linkedin(self, request):
            """Return two valid pages followed by LinkedIn's no-data sentinel."""
            if self.provider_calls < 2:
                response = super().linkedin(request)
                payload = json.loads(response.content)
                payload["paging"] = {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?q=criteria&domain=CONNECTIONS&start=" + str(self.provider_calls)}]}
                return httpx.Response(200, json=payload)
            self.provider_calls += 1
            return httpx.Response(404, json={"message": "No data found for this domain and memberId.", "status": 404})

    with patch.dict(os.environ, fixture["ENV"]):
        services = EndOfData("complete")
        code, output = await fixture["pipeline"](services)
    complete = "Status: COMPLETE" in services.text
    evidence = {
        "kind": "synthetic full reporting pipeline replay of verified live terminal response",
        "expected_status": "COMPLETE",
        "actual_status": "COMPLETE" if complete else "BLOCKED" if "Status: BLOCKED" in services.text else "other",
        "expected_exit": 0,
        "actual_exit": code,
        "provider_requests": services.provider_calls,
        "database_requests": services.database_calls,
        "reproduced_bug": not complete and code == 1,
        "safe_cli_output": output.strip(),
    }
    Path(__file__).with_name("replay-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    assert complete and code == 0, "Documented end-of-data should yield COMPLETE; unchanged PR yields BLOCKED and exits 1"


if __name__ == "__main__":
    asyncio.run(main())
