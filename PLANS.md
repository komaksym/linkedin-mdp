# Google Docs morning report

## Contract

- Add a dependency-light report adapter and orchestration CLI on `feature/google-docs-report`; call `reconcile_connections()` directly and do not change the reconciliation engine.
- `ReportResult` has `COMPLETE`, `BLOCKED`, or `FAILED`, an aware actual run timestamp, and counts only for `COMPLETE`. Provider or incomplete-snapshot failures publish unavailable counts; Supabase/internal failures publish unavailable counts with an explicit uncertain-write warning.
- Google config, refresh auth, document metadata, complete paginated permission privacy, and marker structure are preflighted before reconciliation. The document must be an editable, nontrashed Google Doc with only one owner user permission and no other grants. Recheck the same gate before one atomic marker-region replacement guarded by `requiredRevisionId`; refetch and recheck once only for revision conflict.
- Markers are exact standalone paragraphs `[BEGIN AUTOMATED REPORT]` / `[END AUTOMATED REPORT]` in one tab. Validate uniqueness/order/plain text, use UTF-16 indices, preserve all outside content, notes, and formatting.
- Google failures preserve the previous document and fail the CLI. Public output is fixed generic status text only; never print exception details or report data.
- Add standalone synthetic-transport E2E scenarios with safe repeatable JSON verdict evidence. Add a main-only, opt-in scheduled/manual workflow; share `connections-reconciliation` concurrency with sync, cancel false. No report outputs, caches, artifacts, summaries, or secrets outside the reporter step.
- Document OAuth setup, restricted sharing, seven-day Testing refresh-token expiry, scheduler opt-in, and best-effort static UTC schedule limitations.

## Milestones

1. Write E2E contract scenarios and evidence runner before implementation.
2. Implement report result/publisher, orchestration script, workflow serialization/opt-in, and setup guide.
3. Run standalone E2E scenarios, then repository lint/typecheck/existing tests/build; fix failures.
4. Publish a separate dependent PR with the system infographic; keep scheduling disabled until live OAuth delivery is verified.

## E2E scenarios

- Complete nonempty and complete empty runs publish all six counts and preserve notes surrounding the managed region, including emoji/non-BMP UTF-16 indexing.
- LinkedIn failure and incomplete snapshot publish `BLOCKED` and unavailable counts, then exit nonzero.
- Supabase failure publishes `FAILED`, unavailable counts, and uncertain-write wording, then exits nonzero.
- OAuth/config failure occurs before LinkedIn/Supabase reconciliation; permission inspection paginates fully and rejects any extra/non-user/deleted/unknown grants or bad metadata.
- One revision conflict triggers fresh read, full permission recheck, and one retry; repeated conflict or other Docs failure leaves the old text untouched.
- Redirects, malformed/duplicate/reversed/cross-tab/rich-text markers, or ambiguous document shape fail closed.
- Captured CLI output and `--evidence` JSON contain only fixed statuses and scenario verdicts, never tokens, document contents, counts, provider data, or exception text.

## Verification outcome

- Complete: 25 synthetic HTTP-boundary E2E scenarios, including actual client pagination, database write uncertainty, owner-only and published sharing gates, UTF-16 note preservation, and revision retry.
- Complete: Ruff, scoped mypy, 19 existing tests, workflow YAML contract checks, sdist and wheel build. Whole-source mypy has a pre-existing `client.py:124` type error outside this change.
- Added a credentials-free PR workflow to repeat the synthetic E2E and existing tests.
- Created and read back an owner-only Google Doc, currently marked SETUP PENDING. Its ID and contents are not committed.
- Pending operator setup: durable Google OAuth grant, repository secrets, merge dependency and this PR, manual workflow-to-Doc verification, then schedule opt-in. Synthetic tests do not verify live OAuth delivery.

- Addressed Greptile cleanup diagnostics: LinkedIn and Supabase close failures now emit the same sanitized cleanup status as Google close failures. Two additional pipeline scenarios prove the diagnostic without leaking exception details.
