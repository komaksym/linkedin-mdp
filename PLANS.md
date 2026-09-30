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

## Issue #6. Invitation history reconciliation

Summary. Ingest complete outbound INVITATIONS history as durable events for existing prospects. Use event evidence to exclude prior invites, connections, and LinkedIn DMs from fresh invite selection. Keep lifecycle unknown and provider timezone uncertainty explicit.

### Milestones

- [x] Ground the current CONNECTIONS, Supabase, and state flow.
- [x] Compare a parallel invitation module with a generic domain reconciler. Choose the parallel module to keep working CONNECTIONS code unchanged.
- [x] Write failure cases and end-to-end HTTP-boundary tests before implementation.
- [x] Implement invitation planning, reconciliation, manual command, and aggregate-only workflow.
- [ ] With user approval, update the event-derived SQL state and eligibility rule.
- [x] Run lint, typecheck, tests, build, and repeatable verification artifacts.
- [x] Review and generate the system infographic.
- [ ] Publish the PR.
- [ ] Finish approved eligibility SQL and production replay verification.

### Invitation reconciliation failure cases (written before implementation)

1. A truncated snapshot, swallowed first-page 404, or absent provider page must never touch Supabase.
2. A later-page provider error must never touch Supabase, even if earlier pages contain valid outbound rows.
3. Malformed `raw_elements` (non-object element, wrong domain, missing or non-list `snapshotData`) must fail before Supabase; the generic client silently skips some of these.
4. Inbound rows must not establish invitation evidence; malformed rows, invalid URLs, and missing or invalid timestamps must be counted and skipped.
5. Canonical URL variants and equivalent provider timestamps must collapse to one event; distinct provider timestamps must remain distinct.
6. Only existing prospects can receive events. A second run over the same provider history must insert zero rows.
7. Naive provider timestamps cannot become absolute event times. Their event time is the labeled observation time, while the provider text and normalized local timestamp remain in payload and key.
8. The command must emit aggregate JSON or a fixed safe error and never print provider rows or a raw traceback.
9. Every page must contain a valid `elements` list and usable pagination metadata; an empty list is a valid page, while a broken continuation is not.

### Throughput checkpoint

- Blocking first steps. Confirm issue and current main, inspect provider field shape, and get migration approval before schema changes.
- Independent workstreams. One implementation owner; independent read-only design review and production inspection; infographic preparation after code review.
- Shared mutable state. Give the implementation owner exclusive access to its worktree until it stops. Reviewers are read-only.
- Smallest safe decomposition. The parser, orchestration, HTTP-boundary tests, and manual command share one event contract and belong to one owner.

### Contract and decisions

Use an invitation-specific plan and summary dataclass. Reuse canonical URL normalization and the existing host-pinned Supabase event writer. Key events by canonical invitee URL plus the stable normalized provider timestamp. Only OUTGOING records establish previously invited evidence. Skip unusable or unmatched rows with aggregate counts. Never use observation time in the durable key. Store the original provider timestamp, timestamp semantics, observed_via INVITATIONS, and lifecycle_state unknown. Fetch all pages before writes; reject truncation, later-page errors, malformed page envelopes, and unavailable snapshots. A timestamp with no timezone is not an absolute sent time; use observed_at for occurred_at with explicit semantics.

The production outreach_state function ignores invitation history today. A database-function migration needs approval under AGENTS.md. No migration is introduced until permission arrives.

Validation. The independent review required rejection of pagination links with missing or non-string relations. Two HTTP cases reproduced the failure before the guard was added. The generated technical system infographic shows the target flow; eligibility is explicitly pending approval. Per-file changes and the production read-only baseline are recorded in artifacts/.
