"""Exercise exact-profile qualification evidence through the shortlist boundary."""

from e2e_invitation_shortlist import (
    PROFILE_A,
    PROFILE_B,
    candidate,
    qualification,
    run,
    source_export,
)


def test_candidate_citations_must_attest_exact_profile() -> None:
    """A citation for another LinkedIn profile cannot qualify this candidate."""
    row = candidate(PROFILE_A)
    row["identity"]["name"]["citations"][0]["profile_url"] = PROFILE_B

    result = run(source_export(), qualification(candidates=[row]))

    assert result["invitations"] == []
    assert result["withheld_counts"]["qualification_invalid_citation"] == 1
