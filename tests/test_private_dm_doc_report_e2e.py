"""Expose the private DM Google Doc synthetic E2E to pytest."""

from e2e_private_dm_doc_report import run_scenarios


async def test_private_dm_doc_report_e2e() -> None:
    """Repeat the two-document Google boundary contract."""
    assert await run_scenarios()
