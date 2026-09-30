# Invitations

## Sub-features

- Exact public tool name: linkedin_invitations.
- Historical INVITATIONS snapshot rows.
- max_pages pagination control and truncation metadata.

## How to get to it (user POV)

Connect an MCP client to the service and call linkedin_invitations with the desired max_pages value.

## Driving it with verify.sh

Run the full drive. It calls linkedin_invitations through MCP and records invitations_rows and invitations_truncated in the evidence JSON.

## Gotchas

INVITATIONS is historical data. A missing current connection does not prove an invitation is pending, rejected, withdrawn, or expired.
