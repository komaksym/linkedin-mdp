# LinkedIn CONNECTIONS -> Supabase Reconciliation Design

Date: 2026-09-23

## Goal

Implement the smallest production-shaped LinkedIn MDP ingestion slice:

```text
LinkedIn CONNECTIONS snapshot
        ->
normalize public profile URL
        ->
match prospects.linkedin_url_key
        ->
write LINKEDIN_CONNECTION_FOUND evidence
        ->
outreach_state()
```

The LinkedIn client remains strictly read-only. Supabase is the only system mutated by this slice.

## Current state

The repository already exposes `linkedin_connections` / `LinkedInMDPClient.snapshot("CONNECTIONS")` and the existing live LinkedIn path is proven.

Supabase currently has:

- `public.prospects` with 1,117 rows;
- `public.events` with 603 baseline events;
- `public.outreach_state(text)`;
- a uniqueness guard on `(source, external_key)` when `external_key` is non-null.

There is also a one-time encrypted helper that pulls changelog + connections into a GitHub Actions artifact. It is exploratory transport only. It does not write to Supabase and is not the production reconciliation path.

## Scope

This slice DOES:

1. fetch a complete CONNECTIONS snapshot;
2. parse each connection's public LinkedIn profile URL;
3. normalize that URL using the same semantics as `public.normalize_linkedin_url`;
4. match it to an existing prospect;
5. create one normalized `LINKEDIN_CONNECTION_FOUND` event per matched connection;
6. make reruns idempotent;
7. report counts only: fetched, matched, unmatched, inserted, already-present;
8. provide a manual GitHub Actions entry point for a real E2E run.

This slice DOES NOT:

- ingest INVITATIONS;
- ingest INBOX;
- use changelog cursors;
- run on a schedule;
- create prospects for unknown LinkedIn members;
- infer disconnects or invitation lifecycle state;
- integrate Clay or LGM.

## Data mapping

LinkedIn's Connections data includes the public profile URL and connection date. The raw MDP row is treated as provider evidence.

Expected useful fields:

```text
URL
Connected On
First Name
Last Name
Email Address
Company
Position
```

Only `URL` is required for identity matching.

### Identity

For each snapshot row:

```text
row["URL"]
    ->
normalize_linkedin_url_python(...)
    ->
prospects.linkedin_url_key
```

The Python normalizer must match the current SQL behavior:

- trim whitespace;
- remove query string and fragment;
- convert any LinkedIn subdomain / http variant to `https://www.linkedin.com`;
- remove trailing slashes.

Tests must include the same normalization examples already used by the database migration.

If `URL` is missing, invalid, or does not match an existing prospect, no prospect is created and no event is written. The row contributes only to the unmatched count.

## Event representation

Matched rows become:

```text
source      = LINKEDIN_MDP
event_type  = LINKEDIN_CONNECTION_FOUND
prospect_id = matched prospect
external_key = connection:<canonical_linkedin_url>
```

`external_key` deliberately represents the durable fact "this profile was observed in CONNECTIONS", not a particular sync run.

### occurred_at

If `Connected On` is present and parseable, use that date as the event date at UTC midnight and mark:

```json
{
  "timestamp_semantics": "event_date_day_precision"
}
```

If it is absent or unparseable, use the sync observation time and mark:

```json
{
  "timestamp_semantics": "observed_at"
}
```

The provider row remains in `payload.raw` so evidence is auditable. Raw rows must never be printed to GitHub Actions logs.

## Idempotency

Required invariant:

```text
run 1 -> inserts missing matched connection evidence
run 2 with identical snapshot -> inserts 0 events
```

Idempotency must be enforced by Postgres, not only by a pre-read in Python.

The current partial unique index has the desired data semantics but is awkward as a PostgREST upsert conflict target.

Change it to an ordinary unique constraint/index on:

```text
(source, external_key)
```

Postgres permits multiple NULL values in a normal unique constraint, so nullable `external_key` behavior remains compatible while PostgREST can use `on_conflict=source,external_key`.

The sync writer will use Supabase's REST API with service-role authorization and conflict-ignore semantics.

## Runtime architecture

Do not call the MCP server from the sync process. Reuse the underlying read-only client directly.

```text
GitHub Action / CLI
      |
      +--> LinkedInMDPClient.snapshot("CONNECTIONS")
      |
      +--> Supabase REST
               |
               +--> read prospects id + linkedin_url_key
               +--> insert normalized events
```

This avoids starting a local MCP HTTP server only to call our own code.

Suggested code boundaries:

```text
src/linkedin_mdp_mcp/connection_sync.py
    pure normalization + row -> event planning
    orchestration logic

src/linkedin_mdp_mcp/supabase_client.py
    minimal service-role REST client

scripts/sync_connections.py
    CLI entry point

tests/test_connection_sync.py
tests/test_supabase_client.py
```

No new Python dependency is required because `httpx` already exists.

## Configuration and secrets

Runtime inputs:

```text
LINKEDIN_ACCESS_TOKEN
LINKEDIN_API_VERSION
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

In GitHub Actions:

- `ORION_TOKEN` continues to map to `LINKEDIN_ACCESS_TOKEN`;
- `SUPABASE_SERVICE_ROLE_KEY` must be a GitHub Actions secret;
- `SUPABASE_URL` may be a repository variable or fixed non-secret project URL.

The service-role key must never be committed or logged.

## GitHub Actions

First version is manual only:

```text
workflow_dispatch
    ->
run connection sync once
    ->
print aggregate counts
```

Do not schedule it yet.

A schedule is added only after a real E2E run proves:

1. the snapshot is complete and not truncated;
2. prospect matching behaves as expected;
3. the first run inserts the expected missing evidence;
4. the immediate second run inserts zero events;
5. `outreach_state()` changes only for the intended prospects.

## Error handling

Fail the run if:

- LinkedIn snapshot retrieval fails;
- snapshot reports `truncated=true`;
- Supabase authentication fails;
- prospect lookup fails;
- event insert fails for anything other than an expected duplicate conflict.

Do not fail because some connection rows are unmatched. Report their count only.

Do not print raw connection rows, names, email addresses, or private LinkedIn data in CI logs.

## Testing

### Unit tests

- URL normalization variants match SQL semantics.
- Missing/invalid URL -> unmatched.
- known canonical URL -> correct prospect.
- `Connected On` parse -> day-precision timestamp.
- missing/bad date -> observed-at semantics.
- event key is deterministic.

### HTTP client tests

Use `httpx.MockTransport` to verify:

- service-role credentials are sent only to the configured Supabase host;
- prospects are read correctly;
- event writes request conflict-ignore behavior;
- errors do not expose credentials.

### Live verification

Before calling the slice complete:

```text
baseline event count
    ->
run sync
    ->
inspect LINKEDIN_MDP / LINKEDIN_CONNECTION_FOUND count
    ->
run sync again immediately
    ->
confirm second run inserts 0
    ->
spot-check outreach_state()
```

## Known limitation

This first slice records positive connection evidence only.

It does not yet represent a later disconnection when a profile disappears from a future CONNECTIONS snapshot. That requires explicit snapshot-membership/reconciliation semantics and is intentionally deferred rather than guessed.

For outbound safety this is conservative: a person ever confirmed as connected remains suppressed from "needs connection" flows until the later reconciliation model is implemented.

## Definition of done

This slice is done when a manual GitHub Actions run against the real LinkedIn MDP and real Supabase project:

- reads the full CONNECTIONS snapshot;
- matches existing prospects by canonical LinkedIn URL;
- writes fresh `LINKEDIN_CONNECTION_FOUND` evidence with source `LINKEDIN_MDP`;
- logs no private row data;
- leaves unmatched rows untouched;
- produces zero new events on an immediate identical rerun;
- leaves the five-tool LinkedIn MCP surface read-only and unchanged.
