"""Expose the synthetic private DM action matrix to pytest."""

from pathlib import Path

from e2e_dm_uncertainty_scope import main as uncertainty_scope_main
from e2e_private_dm_actions import cli_scenario, failure_artifact_scenario, run_matrix


def test_private_dm_actions_e2e(tmp_path: Path) -> None:
    """Repeat the synthetic classifier, planner, CLI, and uncertainty-scope scenarios."""
    assert run_matrix()
    cli_scenario()
    failure_artifact_scenario()
    uncertainty_scope_main(tmp_path / "dm-uncertainty-scope-e2e-evidence.json")
