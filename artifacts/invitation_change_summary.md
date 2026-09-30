# Invitation history change

- src/linkedin_mdp_mcp/invitation_sync.py adds complete-snapshot matching and durable historical invitation events.
- src/linkedin_mdp_mcp/client.py adds opt-in strict provider page validation and a type-narrowed changelog timestamp list.
- tests/test_invitation_sync_e2e.py exercises the real HTTP clients across synthetic pagination, malformed rows, failures and replay.
- scripts/sync_invitations.py exposes an aggregate-only manual reconciliation command.
- .github/workflows/sync-invitations.yml adds a main-only manual invocation using the existing secrets and pinned actions.
- scripts/verify_invitation_sync.py regenerates the privacy-safe verification result.
- artifacts/invitation_sync_verification.json records the repeatable HTTP-boundary check result.
- artifacts/invitation_production_baseline.json records read-only production evidence of the eligibility bug.
- artifacts/invitation_eligibility_acceptance.md records the pending database behavior checks before any SQL change.
- README.md documents operation and the pending eligibility gate.
- PLANS.md records the design, milestones, failure cases and validation.
- infographic/invitation-history-reconciliation contains the raster system visual and reproducible source, analysis, structured content and prompt.

The infographic shows the target flow. Its final eligibility component is pending database migration approval. Ingestion alone does not fix recommendations. No production write or SQL migration has been performed.

Design review compared a separate invitation module with a generic reconciler. Both reviewers and the cross-judge preferred the separate module to keep CONNECTIONS behavior unchanged. Strict page validation was retained as a necessary shared transport capability. The final review found malformed link relations and a missing docstring; both were corrected. Comment review recommended zero deletions.
