# Snapshot pagination fix

Summary: `memberSnapshotData` can return overlapping snapshot elements across pages without version metadata that proves which page is current. Merge distinct JSON rows across all returned elements so pagination cannot silently discard data.

## Milestones

1. Pin the regression where overlapping pages share rows but each can contain distinct data.
2. Merge distinct snapshot rows without changing the public response shape.
3. Exercise multi-page connections through the live MCP E2E and emit a repeatable privacy-safe summary.
4. Run narrow tests, full tests, build, live E2E, then review the PR.
