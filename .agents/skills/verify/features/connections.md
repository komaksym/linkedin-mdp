# Connections

## Sub-features

- Exact public tool name: linkedin_connections.
- max_pages pagination control.
- Exact-distinct rows merged across overlapping snapshot elements.
- raw_elements, page_count, and truncated metadata remain available.

## How to get to it (user POV)

Connect an MCP client to the service and call linkedin_connections. Use max_pages 10 when verifying pagination.

## Driving it with verify.sh

Run the full drive. tests/e2e_live.py requires at least two CONNECTIONS pages, derives expected rows with an independent type-sensitive structural oracle, and self-checks that the pre-fix single-snapshot behavior is rejected.

Inspect connections_rows, connections_distinct_raw_rows, connections_snapshot_versions, connections_page_count, connections_live_legacy_would_differ, connections_oracle_self_check, and connections_truncated in the evidence JSON.

## Gotchas

LinkedIn returns overlapping snapshot elements and does not expose freshness metadata that proves first or last page is newest. Do not infer domain identity beyond exact JSON equality.
