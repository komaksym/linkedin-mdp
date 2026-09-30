# Inbox

## Sub-features

- Exact public tool name: linkedin_inbox.
- INBOX history for sent and received messages and conversations.
- max_pages pagination control and truncation metadata.

## How to get to it (user POV)

Connect an MCP client to the service and call linkedin_inbox with the desired max_pages value.

## Driving it with verify.sh

Run the full drive. It calls linkedin_inbox through MCP and records inbox_rows and inbox_truncated in the evidence JSON.

## Gotchas

Do not infer read or unread state from a missing readAt field. Evidence must remain aggregate only and must not publish message contents.
