# linkedin-mdp-mcp

Minimal read-only MCP bridge for LinkedIn's official **Member Data Portability API**.

It intentionally exposes only five tools:

- `linkedin_authorization_status`
- `linkedin_connections`
- `linkedin_invitations`
- `linkedin_inbox`
- `linkedin_member_changelog`

There is no arbitrary HTTP tool, no browser automation, and no LinkedIn write action.

## Architecture

```text
ChatGPT / MCP client
        |
        | Streamable HTTP / MCP
        v
linkedin-mdp-mcp
        |
        | hardcoded GET-only calls
        | Bearer token stays server-side
        v
api.linkedin.com
  /rest/memberAuthorizations
  /rest/memberSnapshotData?domain=CONNECTIONS
  /rest/memberSnapshotData?domain=INVITATIONS
  /rest/memberSnapshotData?domain=INBOX
  /rest/memberChangeLogs
```

## Central outreach state

Supabase/Postgres now contains the first central-state vertical slice while the LinkedIn MCP surface remains exactly five read-only tools.

```text
prospects
   |
   +--> events  (normalized evidence from LinkedIn / Clay / LGM)
   |
   +--> outreach_state(linkedin_url)  (derived current state)
```

The schema lives in `supabase/migrations/`.

`public.outreach_state(text)` derives fields including:

- current LinkedIn connection evidence;
- latest outgoing invitation timestamp;
- latest outbound/inbound activity;
- reply state;
- LGM campaign status;
- Clay enrichment completion;
- a conservative derived state and next action.

`INVITE_SENT_NOT_CONNECTED` means only that an outgoing invitation exists and there is no current connection evidence. It does not mean LinkedIn reports the invitation as pending.

The operational tables have RLS enabled and are not exposed to `anon` or `authenticated`; the RPC is intended for trusted server-side access via `service_role`.

## Manual invitation history reconciliation

Run `PYTHONPATH=src uv run python scripts/sync_invitations.py` with `LINKEDIN_ACCESS_TOKEN`, `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY` set. The main-only `Sync LinkedIn Invitations` GitHub workflow runs the same command on demand. Both surfaces print aggregate counts only; command failures print a fixed error without provider rows or tracebacks.

The command fetches all available INVITATIONS pages before reading Supabase, rejects incomplete or malformed pages, and writes duplicate-safe `LINKEDIN_INVITATION_HISTORY_FOUND` events only for existing prospects. It accepts only `OUTGOING` rows with a usable invitee profile URL and provider `Sent At` value. Each event retains the raw row, original timestamp, canonical URL, `observed_via=INVITATIONS`, and `lifecycle_state=unknown`. A timestamp without timezone uses observation time for `occurred_at` and sets `timestamp_semantics=observed_at`; the provider's local time is kept separately and is never assigned a guessed timezone.

The current `outreach_state` SQL does not yet read this new history event type. Invitation-based candidate exclusion requires a separately approved migration; this command only records evidence.

Run `uv run --extra dev python scripts/verify_invitation_sync.py` to regenerate `artifacts/invitation_sync_verification.json` from synthetic HTTP-boundary tests without exporting member data.

## Run locally

Using `uv`:

```bash
uv sync --extra dev
export LINKEDIN_ACCESS_TOKEN='...'
export LINKEDIN_API_VERSION='202312'
uv run linkedin-mdp-mcp
```

MCP endpoint: `http://127.0.0.1:8000/mcp`

Inspect it with:

```bash
npx @modelcontextprotocol/inspector@latest
```

Choose **Streamable HTTP** and enter `http://127.0.0.1:8000/mcp`.

## Safe ChatGPT connection

For a personal/private first deployment, keep the service private and use **Secure MCP Tunnel** rather than placing an unauthenticated endpoint on the public internet. Register the resulting MCP connection in ChatGPT developer mode.

## Authentication

The server accepts the LinkedIn token only from an environment variable:

```bash
LINKEDIN_ACCESS_TOKEN=...
```

`LINKEDIN_TOKEN` is also accepted as a compatibility alias. Never commit the token or put it into MCP tool arguments.

## Live smoke test

Once a token is present:

```bash
uv run linkedin-mdp-smoke
```

The smoke test calls all five LinkedIn capabilities but prints only counts/metadata, not member rows.

## Tests

```bash
uv run pytest -q
```

The tests verify endpoint/finder shapes, pagination, read-only MCP annotations, the exact five-tool surface, and a guard that prevents a malicious pagination link from forwarding the bearer token to another host.

## Important semantics

- `INVITATIONS` is historical data. Do not treat missing connection as proof of a specific LinkedIn state such as rejected, withdrawn, or expired. A useful derived state is simply `INVITE_SENT_NOT_CONNECTED`.
- Do not infer read/unread message state from a missing `readAt` field.
- Changelog history is bounded by LinkedIn's API behavior. Use snapshots for durable current/history inspection and changelog for recent changes.
- Snapshot 404 for an unavailable/empty domain is normalized to an empty result.

## Deliberately not included in v0.1

- scheduled LinkedIn-to-Supabase synchronization
- Clay enrichment ingestion
- LGM campaign/API/webhook ingestion
- OAuth refresh/token storage
- deployment-specific auth beyond the private Supabase state boundary
- any LinkedIn mutation
