"""Repeat the combined private action report end-to-end matrix in pytest."""

from e2e_combined_action_report import failure_artifact_scenario, run_matrix


def test_combined_action_report_e2e() -> None:
    """Exercise the real offline CLI and synthetic Google HTTP boundary."""
    assert run_matrix()
    failure_artifact_scenario()
