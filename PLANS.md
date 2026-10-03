# Implementation plans

## PR14 changelog pagination scope repair

Summary: reject a changelog next link that adds `startTime` when the caller did not set one, before a second HTTP request or encryption. Keep valid pagination cursors and an unchanged caller-supplied `startTime`.

Milestones: (1) extend the existing synthetic HTTP/CMS E2E and observe the missing rejection, (2) add the narrow client scope check, (3) verify the E2E and repository lint, types, tests, and build. The E2E failed before the fix with `unexpected synthetic collection failure`; after the fix, all 45 synthetic verdicts pass, including the seeded unchanged-start-time path. Full mypy (8 source files), 21 pytest tests, and sdist/wheel build pass. Parent reran the 45-verdict HTTP/CMS E2E, default scoped Ruff without exclusions, and diff check successfully. Independent correctness and comment review found no issues. The system infographic shows cursor progress with an immutable original time window.

## Private source changelog acquisition

Summary: include the existing read-only LinkedIn member changelog response in the encrypted private source bundle, with actual collector observation timestamps and an explicitly unknown provider freshness. Preserve each raw provider event exactly and accept a null watermark only for a complete empty response.

Failure cases to prove before implementation: (1) successful nonempty events are omitted or modified, (2) collector timestamps are absent or derived from provider event timestamps, (3) a complete empty response with a null watermark is rejected, (4) a nonempty response with a null watermark, zero/negative/boolean watermark is accepted, (5) malformed/missing envelope fields, malformed event entries, wrong-endpoint or wrong-query next links, malformed next links, or page-limit truncation are accepted, (6) valid pagination loses its original request scope, and (7) any failure creates an encrypted artifact. The collector must use only GETs and preserve the existing `created_at` projection and encrypted-only handling.

Milestones: (1) extend the synthetic HTTP/CMS E2E and run it red against the current collector, (2) add strict changelog acquisition to the collector, (3) rerun the E2E and narrow lint/typecheck/tests/build, and (4) prepare an inactive workflow refresh proposal outside `.github/workflows` for review.

Validation: the expanded E2E first failed on omitted changelog events/timestamps, empty watermark metadata, malformed events, missing and invalid watermarks, wrong endpoint/query pagination links, malformed next links, truncation, and lost query scope on valid pagination. It now passes all cases, including no artifact on collection failure and unchanged `created_at` checks. Mypy, the full 21-test suite, and package build pass in the declared `uv` environment. Ruff passes on touched source with pre-existing `UP037` in unchanged `client.py:62` excluded. System Python lacked the declared MCP client export and `build` module, so validation used `uv`.

## Private prospect creation timestamp

Summary: export the existing `prospects.created_at` field in the encrypted private source bundle, preserving its provider value without deriving or backfilling dates.

Failure cases: omission from the Supabase projection drops exact timestamps on each counted page; a missing source value must not be fabricated; source or paging failure must not create an artifact; plaintext must not appear in the ciphertext or fixed CLI logs.

Milestones: (1) make the synthetic HTTP and CMS roundtrip fail on timestamp omission, (2) add the field to the prospect projection, (3) rerun the E2E and repository checks. No schema, workflow, live source call, or outreach change belongs to this slice.

Throughput checkpoint: one owner holds this isolated checkout and its coupled collector/E2E files. The fixed `TABLE_COLUMNS` map owns the row shape; the database mock honors `select`, so the E2E detects projection loss. Review and PR publication remain with the coordinating task.

Validation: the timestamp scenario failed before the projection edit and all 19 synthetic verdicts passed afterward. Scoped Ruff and mypy passed, the full 21-test repository suite passed in an isolated runtime using the declared dependencies, and source/wheel builds passed. The initially installed local MCP package lacked `Client`; using the declared project runtime resolved that collection failure. Independent read-only review found no actionable issues.

PR review correction: two synthetic failure injections first showed that an unrelated encryption fault could count as an expected negative rejection and an early certificate failure preserved prior all-true evidence. The E2E now attributes each negative case to its expected collection error, lets encryption failures propagate, and records failed run status before certificate generation. All 22 synthetic verdicts, 21 isolated repository tests, scoped Ruff, 10-file mypy, and wheel build pass after the correction.

## Inbox evidence and actionable report

Summary: add the first reviewable prerequisite for the owner's two-list report. Preserve private message observations for existing prospects without treating unknown invitation notes as confirmed DMs. The complete desired report contract is in `docs/action-report-contract.md`.

Owner clarification: automatically generate only researched first-DM drafts, with a personalized opener grounded in persisted Clay, Exa, or other provider evidence. Reply and follow-up rows show conversation evidence and action reminders; the owner writes those messages. Sending remains manual. Preserve the invitation ranking and Supabase audit requirements.

UTC correction checkpoint (`5cc2381`): 39 synthetic HTTP-boundary inbox scenarios, all 32 existing report E2E scenarios, and 20 pytest checks passed at that revision. Scoped Ruff passed while excluding the unchanged existing quoted-annotation rule. Full-source mypy plus the CLI and live verifier passed across 10 files. The package built successfully. Independent review accepted the whole-thread taint, subject-identity, and explicit UTC-format corrections. The observed provider string fields are covered by fixtures. The implementation has no schema, workflow, schedule, or outreach-send change; the separately approved one-time workflow verified actual inbox persistence. The later review-repair checkpoint below records the expanded suites.

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

### PR 8 review repair

Summary: repair the four reported review findings on the verified owning branch, then babysit the latest GitHub verdict. Preserve the complete report as the next authorized work after this prerequisite.

- [x] Reproduced endpoint/domain pagination escape before the client correction; reject changed scope before any database access, retain valid partial pagination links within the original request scope.
- [x] Reproduced missing-content attachment loss and valid empty-plan verifier failure; keep null/nonstring content invalid and distinguish empty verification from persisted-event proof. The new review runner also fails against original revision `0893b72` and passes against the repair.
- [x] Aligned live-validation documentation with recorded evidence and remaining report work. The report contract already reflected successful live verification.
- [x] First repair checkpoint (`af41b35`): 41 inbox scenarios, 8 review scenarios, 32 report scenarios, 21 pytest checks, lint, types, and build passed. Independent review was clean; all four threads were answered. CI run 36989915353 and live persistence/readback/replay run 36990096445 passed against this revision.
- [x] CodeRabbit follow-up (`c29b06f`): replaced optimization-removable assertions with unconditional checks. The corrupt-readback E2E subprocess under `python -O` failed before the correction and now passes, alongside normal nonempty readback and valid empty verification. This checkpoint had 11 review scenarios; CI run 36991010814 and live readback/replay run 36991082417 passed.
- [x] Inviter identity review correction: the real verifier boundary rejected equivalent apex/www/regional profile URLs before the fix. It now canonicalizes every outgoing inviter with the existing strict profile normalizer before cardinality checks, while invalid and distinct identities remain rejected before writes. The expanded 15-scenario review E2E, 21 pytest checks, scoped Ruff, full-source mypy (10 files), and package build pass. No schema, workflow, or schedule changes.
- [ ] Follow fresh Greptile/CodeRabbit review and CI to merge-ready. Continue one report feature at a time with concrete live artifacts.

Throughput checkpoint: root owns client pagination and existing E2E; one repair worker owns inbox content, live verifier and separate E2E files. A read-only worker inventories the next report slice. No shared-file edits or parallel pushes.

### Live timestamp correction

The approved credentialed validation reproduced zero planned events. Read-only diagnostics confirmed supported prospect identities and mapped rows whose dates use `YYYY-MM-DD HH:MM:SS UTC`. The ISO-only parser rejected that format. Two new HTTP-boundary persistence/replay assertions failed before changing parsing; the correction accepts only the exact explicit UTC format. Unknown abbreviations, conflicting offsets, invalid calendar dates, and naive timestamp semantics remain covered. At this historical UTC checkpoint the inbox suite had 39 scenarios; later review repairs expand it as recorded above. Lint, types, tests, build, and actual persistence/readback/replay passed for that checkpoint.

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
