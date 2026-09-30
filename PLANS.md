# Snapshot pagination fix

## Prospect evidence resolution

Summary: add one local evidence-file-to-report feature that preserves observations, reconciles current employment from atomic, cited role claims and derives message state per channel. No live storage, transport API, send capability or infrastructure changes. Ground the existing transport/event boundaries, compare a pure resolver with a stateful case service, then implement the chosen caller contract with executable acceptance checks written first.

Milestones: Ground; Sketch and cross-judge at least two structural alternatives; Agree by default; Implement; Scrap only if repeated deviations invalidate the design. The design artifact records accepted deviations before the next implementation unit.

Architecture decision: use the stateless batch-to-report resolver (Candidate A). Candidate B's local `CaseBook` adds a second durable state store, locking, reopen semantics and report/case consistency before this slice has any caller that needs persisted adjudication workflow state. The selected slice keeps review decisions as cited validation records in the input batch, so the same immutable input plus explicit `as_of` always reproduces the same report. Implement it inside the existing `linkedin_mdp_mcp` package to avoid changing package discovery; this is the only structural deviation from Candidate A's standalone top-level package sketch.

Failure scenarios, recorded before implementation:

1. Input order, repeated records or provider page order chooses a different company or erases alternatives.
2. Same-name people or mismatched profile URLs are merged; untrusted host/path is accepted as a LinkedIn person identity.
3. A newer retrieval timestamp, stale enrichment or several vendors copying one source silently wins an employment conflict.
4. Company from one job and title from another become an invented employment record; simultaneous jobs are treated as mutually exclusive without evidence.
5. Research without a citation, reliable effective time or exact identity match resolves a conflict; future-dated or expired evidence is treated current.
6. A draft or planned step counts as sent; an email reply changes LinkedIn state; equal or unknown event times imply an ordered reply.
7. An absent event becomes proof of never-sent, not-connected or unread, or historical connection evidence becomes verified current membership.
8. Duplicate IDs with contradictory content overwrite evidence, or a malformed input yields a successful report.
9. CLI errors print raw evidence or output exists partially after failure; repeated CLI runs cannot be compared through a saved artifact.

Validate narrow acceptance checks first, then lint, typecheck, full tests and package build. Keep synthetic verification separate from live-source claims. Live collector integration remains gated on the existing CONNECTIONS slice.

Summary: `memberSnapshotData` can return overlapping snapshot elements across pages without version metadata that proves which page is current. Merge distinct JSON rows across all returned elements so pagination cannot silently discard data, and make the verifier independent enough to detect the old single-snapshot defect.

## Verification failure modes

1. One snapshot element is selected and distinct rows from another page are dropped.
2. Overlapping snapshot elements duplicate rows in the public result.
3. A unique row can appear on either an earlier or later page, so page order cannot be the oracle.
4. Exact duplicates must collapse while materially different rows remain distinct.
5. Verification must not reuse production JSON canonicalization, or both paths can self-confirm the same defect.

## Milestones

1. Pin the regression where overlapping pages share rows but each can contain distinct data.
2. Merge distinct snapshot rows without changing the public response shape.
3. Make the live MCP E2E derive expected rows with an independent structural-equality oracle and self-check that the old single-snapshot behavior is rejected.
4. Add a Codex project-local `verify` skill that launches, doctors, drives, captures evidence, and cleans up the real MCP service.
5. Prove the skill once end to end, then run narrow tests, full tests, build, live E2E, and PR review.
