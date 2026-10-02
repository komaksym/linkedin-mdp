"""Expose the synthetic private DM action matrix to pytest."""

from e2e_private_dm_actions import cli_scenario, failure_artifact_scenario, run_matrix


def test_private_dm_actions_e2e() -> None:
    """Repeat the synthetic classifier, planner, and CLI scenarios."""
    assert run_matrix()
    cli_scenario()
    failure_artifact_scenario()
