"""Repeat the synthetic actual-action HTTP and event-store matrix in pytest."""

from e2e_actual_action_sync import failure_artifact_scenario, main


def test_actual_action_sync_e2e() -> None:
    """Run the real client and one combined event-store insert boundary."""
    main()
    failure_artifact_scenario()
