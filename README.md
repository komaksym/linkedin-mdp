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

- automatic LinkedIn-to-Supabase synchronization
- Clay enrichment ingestion
- LGM campaign/API/webhook ingestion
- OAuth refresh/token storage
- deployment-specific auth beyond the private Supabase state boundary
- any LinkedIn mutation
