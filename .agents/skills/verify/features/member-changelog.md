# Member changelog

## Sub-features

- Exact public tool name: linkedin_member_changelog.
- Optional start_time cursor in epoch milliseconds.
- count from 1 through 50.
- max_pages pagination control and truncation metadata.

## How to get to it (user POV)

Connect an MCP client to the service and call linkedin_member_changelog. The standard live drive uses count 10 and max_pages 1.

## Driving it with verify.sh

Run the full drive. It calls linkedin_member_changelog through MCP and records changelog_events and changelog_truncated in the evidence JSON.

## Gotchas

LinkedIn bounds changelog history. Use snapshots for durable state inspection and changelog for recent changes.
