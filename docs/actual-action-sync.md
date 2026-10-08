# Independent actual-action sync

`scripts/sync_actual_actions.py` stages current raw/export INVITATIONS, CONNECTIONS, and INBOX snapshots plus a bounded 28-day consent changelog before any Supabase lookup or write. Its strict reader records local acquisition times; these are observation times, not provider generation timestamps. A global coverage or shape failure aborts without store access. Scoped DM uncertainty withholds that action while unrelated verified actions may proceed.

Run dry by default with an explicit account identity:

```sh
python scripts/sync_actual_actions.py \
  --account-profile-url 'https://www.linkedin.com/in/example' \
  --account-member-urn 'urn:li:person:example' \
  --output-dir /private/secure/new-sync-output
```

The script reads `LINKEDIN_ACCESS_TOKEN` (or `LINKEDIN_TOKEN`), `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY`. `--output-dir` must name a new directory; it creates a mode-0700 directory and a mode-0600 aggregate-only manifest. Stdout has fixed result tokens. `--apply` opts into one duplicate-safe event insert after complete acquisition, validation, and one prospect lookup. A transport failure during the insert reports `PERSISTENCE_OUTCOME_UNKNOWN`; retry is safe because the store deduplicates `(source, external_key)`. If the insert succeeds but manifest writing or client closing fails, stdout reports `APPLIED_DIAGNOSTIC_FAILED` and exits nonzero while preserving the known applied result. A partial invocation-owned manifest is removed. Dry-run inserted/already-present counts are unknown, not zero.

The batch reuses the reviewed invitation planner and existing connection planner. Invitation history events retain their local observation-time semantics, and a found connection is observed connected rather than assigned an invented acceptance instant. A verified DM has an exact CREATE activity matched to one INBOX row by the shared classifier. Its event key hashes account member URN and activity ID, so ordering and replay do not change identity. Event payloads record actual occurrence time, direction, thread, canonical profile, classifier version, source digest and pointers, and **only** the matched positive INBOX row and CREATE event as durable private evidence (including attachment IDs). Recommendations, drafted messages, and unrelated source rows are never sync inputs or event payloads.

The current `outreach_state` SQL recognizes `LINKEDIN_MESSAGE_OUTBOUND` and `LINKEDIN_MESSAGE_INBOUND` evidence. `LINKEDIN_INVITATION_HISTORY_FOUND` projection requires a separately reviewed SQL change. Imported `Invite`/`ConnectionStatus` CRM attributes are left alone; a changed live status must be established by applied events and an RPC readback. This slice does not activate a schedule or migration, call live providers or Supabase during validation, or send outreach.

The copied dependencies are exact focused files, pending integration of their reviewed branches: PR7 `invitation_sync.py` at `1f2f010c57e12cb3fa58f83fdcd5b46b37349cc4` (SHA-256 `3560ac7527930c3ce2d361b6cbcbcc93e5cff076299593eee2edb2ca9da761fd`) and PR14 `client.py` at `66e8fe47d8cf70fcde7f87ebd3b43c1ccf22eaf7` (SHA-256 `0850e805cd0a68563a479a50a0221ed431adf8a59213b925580699c1dba5eeea`). The repeatable E2E runner uses synthetic HTTP responses and a duplicate-enforcing fake event store; its public artifact has verdicts and aggregate counts, never source data. Its canonical digest ASCII-escapes JSON before hashing, so an escaped surrogate in unrelated provider metadata cannot block an otherwise verified action.

A future provider connection day aborts the sync before any Supabase access; it cannot become an actual event timestamp. Pytest writes synthetic evidence to a temporary path; only the explicit E2E runner regenerates the tracked artifact.

The 57 synthetic scenarios include a real CLI/provider boundary regression for an originally unbounded changelog request. A next link that adds `startTime` fails before a second changelog request or any database access. The regression selects the unbounded reader mode at the client call boundary; normal sync still requests its bounded 28-day window.
