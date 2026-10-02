# Implementation plans

## Inbox evidence and actionable report

Summary: add the first reviewable prerequisite for the owner's two-list report. Preserve private message observations for existing prospects without treating unknown invitation notes as confirmed DMs. The complete desired report contract is in `docs/action-report-contract.md`.

Verification after review fixes: 39 synthetic HTTP-boundary inbox scenarios, all 32 existing report E2E scenarios, and 20 pytest checks pass. Scoped Ruff passes while excluding the unchanged existing quoted-annotation rule. Full-source mypy plus the CLI and live verifier passes across 10 files. The package builds successfully. Independent review accepted the whole-thread taint, subject-identity, and explicit UTC-format corrections. The observed provider string fields are covered by fixtures. The implementation has no schema, workflow, schedule, or outreach-send change; the separately approved one-time workflow verified actual inbox persistence.

The live verification utility compares complete stored payloads and all event timestamps, preserving earlier observation times on idempotent replay. Its missing-credentials path emits only a fixed failure message. The approved live run https://github.com/komaksym/linkedin-mdp/actions/runs/36985062303 verified persistence, full private readback, and duplicate-free frozen-snapshot replay at revision `5cc23814450c14b4a8d3312c682b5f65fa87e22c`. Updated PR CI run 36985032311 passed. Actual DM queue readiness remains pending reliable message classification and coverage checks. A pytest entry point repeats the existing E2E runner in PR CI without changing workflow infrastructure.

Applied principles: Model the Domain keeps event bodies in `InboxSyncPlan` and sanitized counts in `InboxSyncSummary`. Sequence Work into Verifiable Units makes inbox evidence the first slice before ranking, research, and action publication.

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

## Inbox evidence sync

### Live timestamp correction

The approved credentialed validation reproduced zero planned events. Read-only diagnostics confirmed supported prospect identities and mapped rows whose dates use `YYYY-MM-DD HH:MM:SS UTC`. The ISO-only parser rejected that format. Two new HTTP-boundary persistence/replay assertions failed before changing parsing; the correction accepts only the exact explicit UTC format. Unknown abbreviations, conflicting offsets, invalid calendar dates, and naive timestamp semantics remain covered. The suite now has 39 scenarios. Revalidate lint, types, tests, build, and actual persistence/readback/replay before claiming the slice works live.

### Contract

- Read complete MDP INBOX snapshots through strict element and paging validation before any store write. Preserve no-read-receipt semantics as unknown.
- Plan typed, duplicate-safe `LINKEDIN_MESSAGE_OBSERVED` events only when direction and the exact known prospect peer are established from canonical LinkedIn `/in/` URLs.
- Withhold every message in any conversation whose participant union exceeds two. Never infer direction from names or row order.
- Preserve provider message content and timestamp semantics. Aware timestamps use provider time; naive provider timestamps use the aware observation time and say that local timezone is unspecified.
- Default the CLI to dry-run. Require `--apply` for writes and emit only fixed status output.
- Keep this slice limited to inbox sync code, its CLI, docs, and synthetic HTTP-boundary E2E evidence.

### Milestones

1. Record failure scenarios and run them red against the current code before production edits.
2. Implement the typed event planner, fail-closed snapshot reader, opt-in CLI, and setup/semantics documentation.
3. Run synthetic HTTP-boundary E2E, scoped lint/typecheck, existing tests, and package build. Fix any failures.
4. Save sanitized, repeatable E2E verdict evidence. Leave review, PR publishing, and live sync to the coordinating task.

### E2E failure scenarios

- Preserve exact inbound and outbound 1:1 messages, match only an exact known prospect identity, and replay with no duplicate insert.
- Canonicalize supported LinkedIn profile hosts while rejecting lookalike hosts, credentials, extra path segments, and non-`/in/` profiles.
- Never use names to infer sender direction. Reject self, group, ambiguous, unknown-peer, malformed-row, and malformed-timestamp records with aggregate skip reasons.
- If participant union across rows makes a conversation a group thread, withhold the entire conversation, including rows that look 1:1 in isolation.
- Preserve aware timestamps. For accepted naive local timestamps, use observed time and mark provider-local timezone as unspecified. Keep read state unknown.
- Reject invalid/truncated snapshot envelopes, malformed page elements and links, first-page or generic 404s, and page-limit truncation before prospect lookup or writes. Accept the known late exhaustion 404 after a successful page.
- Verify duplicate-safe persisted event bodies and no writes on preflight failure or dry-run. Sanitize CLI output and keep `--apply` opt-in.

### Throughput checkpoint

- Blocking first steps: record and run the HTTP-boundary scenarios red before production edits.
- Independent workstreams: n/a. The contract couples snapshot validation, planning, and apply behavior, and the clone has one owner.
- Shared mutable state: one owner writes the exclusive clone; plan, scenario runner, implementation, and evidence happen in order.
- Smallest safe decomposition: one implementation owner keeps the 1:1/group/thread safety invariant visible across the single slice.
