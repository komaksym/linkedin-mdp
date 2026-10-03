# Implementation plans

## Google Docs morning report

### Contract

- Add a dependency-light report adapter and orchestration CLI on `feature/google-docs-report`; call `reconcile_connections()` directly and do not change the reconciliation engine.
- `ReportResult` has `COMPLETE`, `BLOCKED`, or `FAILED`, an aware actual run timestamp, and counts only for `COMPLETE`. Provider or incomplete-snapshot failures publish unavailable counts; Supabase/internal failures publish unavailable counts with an explicit uncertain-write warning.
- Google config, refresh auth, document metadata, complete paginated permission privacy, and marker structure are preflighted before reconciliation. The document must be an editable, nontrashed Google Doc with only one owner user permission and no other grants. Recheck the same gate before one atomic marker-region replacement guarded by `requiredRevisionId`; refetch and recheck once only for revision conflict.
- Markers are exact standalone paragraphs `[BEGIN AUTOMATED REPORT]` / `[END AUTOMATED REPORT]` in one tab. Validate uniqueness/order/plain text, use UTF-16 indices, preserve all outside content, notes, and formatting.
- Google failures preserve the previous document and fail the CLI. Public output is fixed generic status text only; never print exception details or report data.
- Add standalone synthetic-transport E2E scenarios with safe repeatable JSON verdict evidence. Add a main-only, opt-in scheduled/manual workflow; share `connections-reconciliation` concurrency with sync, cancel false. No report outputs, caches, artifacts, summaries, or secrets outside the reporter step.
- Document OAuth setup, restricted sharing, seven-day Testing refresh-token expiry, scheduler opt-in, and best-effort static UTC schedule limitations.

### Milestones

1. Write E2E contract scenarios and evidence runner before implementation.
2. Implement report result/publisher, orchestration script, workflow serialization/opt-in, and setup guide.
3. Run standalone E2E scenarios, then repository lint/typecheck/existing tests/build; fix failures.
4. Publish a separate dependent PR with the system infographic; keep scheduling disabled until live OAuth delivery is verified.

### E2E scenarios

- Complete nonempty and complete empty runs publish all six counts and preserve notes surrounding the managed region, including emoji/non-BMP UTF-16 indexing.
- LinkedIn failure and incomplete snapshot publish `BLOCKED` and unavailable counts, then exit nonzero.
- Supabase failure publishes `FAILED`, unavailable counts, and uncertain-write wording, then exits nonzero.
- OAuth/config failure occurs before LinkedIn/Supabase reconciliation; permission inspection paginates fully and rejects any extra/non-user/deleted/unknown grants or bad metadata.
- One revision conflict triggers fresh read, full permission recheck, and one retry; repeated conflict or other Docs failure leaves the old text untouched.
- Redirects, malformed/duplicate/reversed/cross-tab/rich-text markers, or ambiguous document shape fail closed.
- Captured CLI output and `--evidence` JSON contain only fixed statuses and scenario verdicts, never tokens, document contents, counts, provider data, or exception text.

### Verification outcome

- Complete: 25 synthetic HTTP-boundary E2E scenarios, including actual client pagination, database write uncertainty, owner-only and published sharing gates, UTF-16 note preservation, and revision retry.
- Complete: Ruff, scoped mypy, 19 existing tests, workflow YAML contract checks, sdist and wheel build. Whole-source mypy has a pre-existing `client.py:124` type error outside this change.
- Added a credentials-free PR workflow to repeat the synthetic E2E and existing tests.
- Created and read back an owner-only Google Doc, currently marked SETUP PENDING. Its ID and contents are not committed.
- Pending operator setup: durable Google OAuth grant, repository secrets, merge dependency and this PR, manual workflow-to-Doc verification, then schedule opt-in. Synthetic tests do not verify live OAuth delivery.

- Addressed Greptile cleanup diagnostics: LinkedIn and Supabase close failures now emit the same sanitized cleanup status as Google close failures. Two additional pipeline scenarios prove the diagnostic without leaking exception details.

## Snapshot pagination fix

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

### Verification failure modes

1. One snapshot element is selected and distinct rows from another page are dropped.
2. Overlapping snapshot elements duplicate rows in the public result.
3. A unique row can appear on either an earlier or later page, so page order cannot be the oracle.
4. Exact duplicates must collapse while materially different rows remain distinct.
5. Verification must not reuse production JSON canonicalization, or both paths can self-confirm the same defect.

### Milestones

1. Pin the regression where overlapping pages share rows but each can contain distinct data.
2. Merge distinct snapshot rows without changing the public response shape.
3. Make the live MCP E2E derive expected rows with an independent structural-equality oracle and self-check that the old single-snapshot behavior is rejected.
4. Add a Codex project-local `verify` skill that launches, doctors, drives, captures evidence, and cleans up the real MCP service.
5. Prove the skill once end to end, then run narrow tests, full tests, build, live E2E, and PR review.

## Snapshot exhaustion fix

Keep this small fix on PR 5's existing branch, in one commit as requested.

1. Add full-pipeline E2E cases before production edits. Known no-data exhaustion after successful pages must retain rows and publish COMPLETE. First-page no-data, generic later-page 404, non-404 errors, and page-limit truncation must remain blocked.
2. Preserve the structured provider error message and classify only exact known snapshot no-data 404s after successful pages as normal exhaustion. Preserve public signatures and nearby behavior.
3. Run the regression first red, then green. Run scoped lint/typecheck, E2E, existing tests and build. Independently review the diff.
4. Push one commit to the existing PR branch. Use the approved validation workflow to run that exact commit and read back the live Google Doc, including timestamp, status, verified counts, notes and owner-only sharing. Publish sanitized evidence and an updated raster system infographic in the PR.

Throughput checkpoint: implementation and independent review run in parallel; live credentials remain in GitHub, and the test runner keeps report contents out of public logs.

Validation before commit: the regression against the original client failed with expected exit 0 versus actual exit 1 and `morning report: blocked`. The patched client passes all 32 HTTP-boundary E2E scenarios and 19 existing tests. Scoped Ruff, mypy and package build pass. The `processedAt` comprehension received a type-only narrowing correction so the touched client passes mypy. Independent review found no actionable issues. The exact committed SHA will be used for the live report check; its private readback evidence and infographic will be recorded in the PR conversation.
