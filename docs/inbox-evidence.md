# Inbox evidence sync

`python scripts/sync_inbox.py` reads and validates the full MDP INBOX snapshot, matches exact LinkedIn member profile URLs to existing prospects, and prints `DRY_RUN_COMPLETE`. It does not write events. Add `--apply` to opt into duplicate-safe writes. Set `LINKEDIN_ACCOUNT_PROFILE_URL` or pass `--account-profile-url` to identify the account profile. Credentials come from the existing LinkedIn and Supabase environment variables.

The sync keeps only messages with one sender and one recipient, where the account is exactly one participant and the other participant is an existing prospect. It uses profile URLs for direction and never uses display names. Group conversations, self-messages, unknown peers, malformed rows, and unsupported timestamps do not produce events. If any row shows that a conversation has more than two participants or has malformed participant data, the sync withholds every row for that conversation.

Events preserve the provider row in `payload.raw`, along with content, subject, attachment fields, canonical participants, direction, provider timestamp, and timestamp semantics. The export supplies recipient profile URLs and attachments as strings, and the sync retains those raw values. Empty content is accepted only when an attachment field has a value. Aware timestamps are normalized to UTC and marked `actual`. Naive provider timestamps use the observed time for `occurred_at` and are marked `observed_at` plus `provider_local_time_timezone_unspecified`; the sync does not assume a local timezone. Read state and `message_kind` are always `unknown`.

`skipped_rows`, `withheld_thread_rows`, and `skip_reasons` explain how many input rows could not be planned. They describe this snapshot's evidence only. They do not count prospects ready for outreach or prove that a conversation has no earlier messages.

INBOX rows may include invitation notes. This slice does not collect CHANGELOG activity data to distinguish invitation notes from direct messages. Message classification, message coverage checks, and confirmation that an event belongs in first-message or follow-up readiness remain prerequisites before those workflows use this evidence. The sync does not update outreach state or claim that a message was read.

The E2E runner uses synthetic LinkedIn and Supabase HTTP transports. Run `uv run python tests/e2e_inbox_sync.py` to repeat the scenarios and refresh `artifacts/inbox-e2e-evidence.json`. The evidence contains only a source revision label and fixed scenario verdicts. The pytest entry point runs this same E2E script in the existing PR checks without a workflow change.

`diagnostics/verify_inbox_live.py` is the prepared live validation utility. It uses complete outgoing invitation evidence to establish the account identity, captures one real INBOX snapshot, applies it through the production writer, reads back every expected event body, and replays that same captured snapshot. It verifies no duplicate inserts and unchanged persisted facts, including earlier observation timestamps for existing naive-time records. Its output contains fixed verdicts only. It needs the trusted runtime credentials; no live run or database write is claimed by the synthetic evidence.

The complete report requirements and remaining milestones are recorded in [Actionable LinkedIn report](action-report-contract.md).

The live provider also supplies `YYYY-MM-DD HH:MM:SS UTC` timestamps. This exact format has explicit UTC semantics and is stored as actual provider time. Other timezone abbreviations and conflicting offset-plus-abbreviation forms remain unsupported; they are never assigned an assumed timezone.

The approved [live validation run](https://github.com/komaksym/linkedin-mdp/actions/runs/36985062303) verified nonempty persistence, complete payload and timestamp readback, and duplicate-free replay at revision `5cc23814450c14b4a8d3312c682b5f65fa87e22c`. The verdict contains no member data. This establishes the inbox evidence path; it does not establish DM classification or action readiness.
