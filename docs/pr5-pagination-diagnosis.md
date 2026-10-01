# LinkedIn pagination root cause

The client mistakes normal snapshot exhaustion for an incomplete fetch. This blocks report generation after valid pages have already arrived.

## Evidence

The read-only live probe used the unchanged PR head `041be40f5141d9cfe1a9851945a337c09a9ebe01`. [Run 36909210750](https://github.com/komaksym/linkedin-mdp/actions/runs/36909210750) recorded:

| Request | HTTP result | Endpoint and finder | Domain |
| --- | --- | --- | --- |
| Initial page | 200 | Correct | CONNECTIONS |
| start=1 | 200 | Correct | CONNECTIONS |
| start=2 | 404, known no-data message | Correct | CONNECTIONS |

The probe emitted only technical flags. Tokens, member records, response bodies, and document identifiers were not logged. It made no database or document writes.

[LinkedIn's official snapshot documentation](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/shared/member-snapshot-api) instructs callers to continue pagination until a no-data response. It warns that the advertised total may omit offline pages. HTTP 404 with that specific response therefore serves as an end-of-data signal, not proof of a broken next link.

`client.py` lines 158–161 in the tested commit catch a snapshot 404 only before the first successful page. Any later 404 is re-raised. Commit `9570e3acdde7d441db0e5657ca3d54a7cb092c13` introduced that restriction. The report then catches the resulting `LinkedInAPIError` and publishes BLOCKED. The reporting behavior is correct for an actual incomplete snapshot; the client has misclassified normal exhaustion.

The synthetic replay sends two successful pages followed by the verified terminal-response shape through the actual client, reconciliation, reporter, and Google publisher against simulated HTTP services. The unchanged PR emits BLOCKED, exits 1, and never calls the database. The expected COMPLETE assertion fails with the specific diagnosed disagreement. Two repeated executions produce the same outcome. The existing 25 E2E scenarios pass because their later-page 404 is a generic error, not this provider-specific exhaustion response.

## Proposed smallest fix

1. Keep the provider's structured error message on `LinkedInAPIError` so the pagination layer can classify an exact, documented no-data response without inspecting a formatted exception string.
2. For snapshot pagination only, after at least one successful page, treat an exact known no-data 404 as successful exhaustion. Return the accumulated elements and successful page count, with `truncated=False`.
3. Keep first-page unavailability, unrecognized later-page 404s, other HTTP errors, and page-limit truncation on their existing paths. Do not change changelog pagination, host validation, snapshot row merging, or reporting logic. Do not stop using the advertised total.

Before implementation, extend the pipeline E2E scenarios to cover known exhaustion after valid pages, generic later-page 404, first-page known no-data, and non-404 failures. The exhaustion scenario must publish COMPLETE with the exact retained fixture counts; genuine failures must still publish BLOCKED with unknown counts and no database work. Then run the live report again and verify its timestamp, COMPLETE status, counts, notes, and owner-only permissions.

The fix is proposed only. No application implementation was changed. COMPLETE production reconciliation remains unverified until the fix is implemented and retested.

## Repeat the reproduction

From the isolated PR checkout with its test environment installed:

```sh
PYTHONPATH=src:. .venv/bin/python diagnostics/pr5-pagination-repro.py
```

The current expected diagnostic result is exit 1 and `Documented end-of-data should yield COMPLETE; unchanged PR yields BLOCKED and exits 1`. This is an intentional failing reproduction, not a failed diagnostic setup.

The [second exact-message probe](https://github.com/komaksym/linkedin-mdp/actions/runs/36909794460) repeated the same 200, 200, 404 sequence and confirmed exact equality with a known terminal no-data message.
