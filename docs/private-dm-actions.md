# Private DM action report

`scripts/build_private_dm_actions.py` reads local JSON exports and creates a private JSON report and Markdown report. It makes no LinkedIn or database request, writes no Supabase record, and sends no message. The owner writes replies and follow-ups and reviews any first-DM draft before sending.

The CLI requires three mode-`0600` JSON files:

- `--source`: one encrypted collector bundle with `schema_version: 1`, an aware `collected_at`, `account_member_urn`, complete `snapshots.INBOX` and `snapshots.CONNECTIONS`, a complete `prospects` read, and a nested `changelog`. Each snapshot needs `source_result: "success"`, `truncated: false`, a positive `page_count`, and consistent `rows` and `raw_elements`. Prospects need unique IDs, stable count metadata, and `completed_at`. The nested changelog needs `events`, `truncated: false`, a positive `page_count`, `next_start_time`, and an aware `collected_at`. Its watermark may be `null` only for an empty complete event list. Successful message CREATE activities need matching owner, actor, author, resource, thread, content, attachments, and time. A profile URL cannot prove the account-member binding. The changelog is consumed only from this bundle.
- `--policy`: `profiles` keyed by exact canonical LinkedIn profile URL. First-DM eligibility uses one matching CONNECTIONS `Connected On` calendar date, supplied as ISO `YYYY-MM-DD` or the provider English `D Mon YYYY` format, within the last 30 UTC calendar days. No acceptance instant is required or inferred. If `accepted_at` is supplied, it must be an aware timestamp no later than the report clock whose UTC date matches the provider day. Qualification also needs `country: "US"`, `pvf_employer: true`, at least one valid HTTPS `qualification_citations` URL, and `opt_out: false`. Missing, invalid, or conflicting provider dates withhold first DMs; the policy timestamp alone cannot establish connection recency. Replies and follow-ups use observed message history and the opt-out gate; they do not require first-DM qualification.
- `--research`: `rows` with an exact `profile_url`, `identity_match.canonical_profile_url_exact: true`, a nonempty `proposed_personalized_opener`, and nonempty `supporting_facts` with `fact` and valid HTTPS `source_url`. This is saved, cited input; the report does not fetch or independently verify research pages.

Run the CLI with `--source`, `--policy`, `--research`, `--output-dir`, and `--account-profile-url`. `--now` accepts an aware ISO time for repeatable offline runs. The output directory must not exist. The CLI creates it with mode `0700` and creates `private-dm-actions.json` and `private-dm-actions.md` with mode `0600`. Standard output is a fixed success or failure line. A standalone `--changelog` argument is unsupported.

## Publish the two DM Google Docs

`scripts/publish_private_dm_docs.py` consumes an existing `private-dm-actions.json`, one retained publication verification artifact, and a private `verification.json`. Pass `--verification` in argv mode. The Path API accepts actions, retained evidence, and verification paths in that order. The publisher publishes the `first_dm` rows to the first-time DM Doc and the `follow_up` rows to the follow-up Doc. It does not publish `reply` rows.

`verification.json` uses schema version `1`. Its exact top-level fields are `version`, `candidate_sha256`, and `queues`. It must contain `queues.first_dm.verdict`, `queues.reply.verdict`, and `queues.follow_up.verdict`, and each queue row contains only `verdict`. Each verdict must be `PASS`, `FAIL`, or `WITHHOLD`. Keep the file private with mode `0600`, like the actions and retained-evidence files. Missing, malformed, stale, or permissive verification returns `private DM docs: input invalid` before Google configuration or HTTP work.

`candidate_sha256` is the lowercase SHA-256 digest of the exact raw bytes in `private-dm-actions.json`. The publisher hashes the current actions file again and requires an exact match. Any byte change, including whitespace-only changes, makes an older verification artifact stale and refuses publication before Google access.

The publisher gates each owned Doc by its queue verdict. It prepares and publishes the first-time DM Doc only when `first_dm.verdict` is exactly `PASS`. It prepares and publishes the follow-up Doc only when `follow_up.verdict` is exactly `PASS`. `FAIL` and `WITHHOLD` leave that Doc unchanged. The `reply` verdict is validated, but this publisher has no reply Doc and never publishes reply rows.

All current planner, retained-evidence, managed-marker, destination, and private-input validators still run before a verdict can select a destination. A malformed artifact or retained evidence is therefore rejected even when one queue is denied. If neither owned queue is `PASS`, the command exits `0`, prints `private DM docs: no eligible destinations`, and reads no Google configuration or makes no HTTP request.

Set only the environment values needed by an eligible destination:

- `GOOGLE_FIRST_DM_REPORT_DOCUMENT_ID` for the first-time DM Doc.
- `GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID` for the follow-up Doc.
- `GOOGLE_OAUTH_CLIENT_ID`.
- `GOOGLE_OAUTH_CLIENT_SECRET`.
- `GOOGLE_OAUTH_REFRESH_TOKEN`.

The two Doc IDs must differ when both owned queue verdicts are `PASS`. Each target Doc must be owner-only and restricted. The Docs use the same `[BEGIN AUTOMATED REPORT]` and `[END AUTOMATED REPORT]` managed region as the connections report. The publisher preserves text outside those markers and reuses the shared privacy, revision, and conflict checks. When both owned queues pass, it preflights both Docs before the first mutation. When one owned queue passes, it requires and touches only that Doc.

The retained evidence artifact must be a private verification record whose `publication` counts match the current planner artifact. `cleared_first_dms` equals the planner `first_dm` length, `conditional_researched_drafts` equals its `rows` length, and `strict_planner_withheld_rows` equals the planner `withheld` length. Each retained row must carry the exact conditional status. A mismatch refuses publication.

The first-time DM Doc can include a saved researched draft from the planner. The draft is still conditional input for owner review. The follow-up Doc is a manual reminder. It can include the previously verified outbound message, timestamp, elapsed time, and evidence references. The report never generates new follow-up copy.

The scheduled morning workflow does not publish these two Docs. The repository has no durable scheduled step that creates `private-dm-actions.json` from the required private inputs before publication. Keep the schedule connections-only until that input path exists. Do not commit the private action artifact or upload it as a public workflow artifact.

The classifier only verifies a DM when a successful CREATE activity uniquely matches a one-to-one INBOX row by thread, people, direction, UTC second, text, and attachments. Typed invitation notes remain separate. Unknown rows, malformed activities, conflicting replays, group threads, incomplete sources, and stale acquisitions withhold affected actions. Read state stays `unknown`. A first-DM row shows `connected_on` with calendar-day precision and `connection_source`; it does not invent a UTC acceptance instant. The `first_dm` reason means no prior DM was observed in the supplied snapshot; the consent changelog covers a bounded 28-day period and cannot prove lifetime absence or deleted history. `date_added` comes only from a unique matched prospect's valid `created_at` and never changes eligibility.

`tests/e2e_private_dm_actions.py` runs synthetic classifier, planner, and CLI scenarios and writes an aggregate verdict artifact at `artifacts/private-dm-actions-e2e-evidence.json`. `tests/e2e_dm_uncertainty_scope.py` separately verifies the timing boundary for anonymous evidence and writes `artifacts/dm-uncertainty-scope-e2e-evidence.json`. `tests/e2e_private_dm_doc_report.py` verifies both, either, and neither Google Doc destination; all verifier verdicts; exact candidate-byte binding; malformed and missing verification; private permissions; destination separation; service failure; cleanup; marker preservation; privacy refusal; and follow-up reminder behavior through synthetic Google HTTP boundaries. These checks use constructed source shapes. They do not verify a real provider changelog, live account identity, current database contents, or live Google delivery.

Group-thread uncertainty is attributed to every identified participant and leaves unrelated conversations eligible. Participant-less evidence remains unresolved. It always blocks a first-DM claim. For a verified reply or follow-up, it blocks only when its time is unknown or is new enough to change that action decision; older anonymous evidence cannot invalidate a newer verified message state. Only an explicit true policy opt-out is labelled `opt_out`; incomplete or conflicting policy records use `policy_opt_out_state_unverified`.

A typed MEDIA activity with exact matching nonempty attachments may have empty text. Missing INBOX CONTENT is treated as empty only during exact correlation; blank TEXT without attachments remains unverified.

CONNECTIONS completeness and freshness gate first-DM eligibility only. Replies and follow-ups use verified inbox/changelog history and prospect coverage. Connection coverage problems remain visible in the report coverage section.
