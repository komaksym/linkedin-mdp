# Snapshot pagination fix

Summary: `memberSnapshotData` returns overlapping snapshot versions, and the client currently chooses the last paged version. Live data shows that can regress to an older subset. Select the first returned snapshot version and verify the behavior through the MCP surface.

## Milestones

1. Pin the regression where page 1 contains the current snapshot and page 2 contains an older subset.
2. Change snapshot selection without changing the public response shape.
3. Exercise multi-page connections through the live MCP E2E and emit a repeatable summary.
4. Run narrow tests, full tests, build, live E2E, then review the PR.
