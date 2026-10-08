# Structured content: E2E oracle 404 handling

## Title
Connections oracle learns terminal 404.

## Objective
Show reviewers what changed: failed reference pages now classify as retry, not_found, or fail.

## Sections

### 1. The problem (verbatim)
- Key concept: any HTTP 404 was fatal.
- Content: Direct CONNECTIONS reference failed with HTTP 404.
- Visual: red terminal node before the MCP calls.
- Labels: 404, fatal, proof blocked.

### 2. The classifier (verbatim)
- Key concept: _reference_status_action.
- Content: 429/500/502/503/504 retry; 404 not_found; anything else fail.
- Visual: three-way switch with three exits.
- Labels: retry, not_found, fail.

### 3. The evidence (verbatim)
- Key concept: proof continues without reference data.
- Content: connections_reference_not_found true; invitations, inbox, changelog still exercised.
- Visual: evidence file node with check marks.
- Labels: not_found, exit 0.

## Data points
- 33 tests pass (30 existing + 3 new).
- Live run: exit 0, 2136 invitation rows, 1211 inbox rows, 10 changelog events.
