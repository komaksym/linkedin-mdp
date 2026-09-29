# Connections

## Sub-features

- Exact public tool name: linkedin_connections.
- max_pages pagination control.
- Exact-distinct rows merged across overlapping snapshot elements.
- raw_elements, page_count, and truncated metadata remain available.

## How to get to it (user POV)

Connect an MCP client to the service and call linkedin_connections. Use max_pages 10 when verifying pagination.

## Driving it with verify.sh

Run the full drive. tests/e2e_live.py fetches CONNECTIONS directly from LinkedIn as an independent pagination reference, then calls linkedin_connections through MCP and compares page count, truncation, raw distinct rows, and returned rows. The drive requires at least two reference pages and requires the live reference to differ from the pre-fix single-snapshot projection.

Inspect connections_rows, connections_distinct_reference_rows, connections_reference_snapshot_versions, connections_page_count, connections_reference_page_count, connections_legacy_single_snapshot_rows, connections_live_legacy_would_differ, connections_oracle_self_check, and connections_truncated in the evidence JSON.

## Gotchas

LinkedIn returns overlapping snapshot elements and does not expose freshness metadata that proves first or last page is newest. The direct reference and MCP calls happen seconds apart, so a concurrent LinkedIn snapshot change can correctly make verification fail and require a rerun. Do not infer domain identity beyond exact JSON equality.
