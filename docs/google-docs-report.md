# Private morning report

Bookmark one owner-only Google Doc. The reporter updates its automated section after each run, preserving your notes around it. It publishes aggregate CONNECTIONS reconciliation counts; it does not produce a person-by-person action queue.

## One-time setup

1. Create a Google Doc in your own Drive with **General access: Restricted** and no other users. Do not publish it to the web or put it in a shared drive. Give the automated section these exact standalone marker paragraphs:

   ```text
   [BEGIN AUTOMATED REPORT]
   Status: SETUP PENDING
   [END AUTOMATED REPORT]

   Operator notes
   ```

2. Use a Google Cloud project you control. Enable the Google Docs API and Google Drive API, configure the OAuth consent screen, and create an OAuth client. Authorize your own account with offline access for these scopes:

   ```text
   https://www.googleapis.com/auth/documents
   https://www.googleapis.com/auth/drive.metadata.readonly
   ```

   Obtain a refresh token through Google's documented OAuth authorization-code flow. Keep the client secret and refresh token out of source code, command logs, chat, and PRs. The connected Codex Google Drive app's authorization is separate and cannot be reused by GitHub Actions. An external OAuth app in **Testing** generally gets a seven-day refresh token for these scopes; use a durable consent configuration before enabling an unattended schedule. See [Google's OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server) and [refresh-token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

3. Add these **GitHub Actions repository secrets** using GitHub's secret settings:

   - `GOOGLE_REPORT_DOCUMENT_ID`: the Doc's ID, kept private even though it is not a password.
   - `GOOGLE_OAUTH_CLIENT_ID`
   - `GOOGLE_OAUTH_CLIENT_SECRET`
   - `GOOGLE_OAUTH_REFRESH_TOKEN`

   Reuse the existing `ORION_TOKEN` and `SUPABASE_SERVICE_ROLE_KEY` secrets. The Google account must own the target document; the publisher rejects every additional sharing grant, including published access.

4. After the reporting change and its reconciliation dependency reach `main`, manually run **Private Morning Report** on `main`. Open the Doc and verify its actual timestamp, status, and counts against the reconciliation outcome. Check that your notes survived. A synthetic test does not replace this live authorization and delivery check.

5. Only after that check, set the repository variable `GOOGLE_DOC_REPORT_ENABLED` to `true`. Remove it or set it to `false` to disable automatic runs. Manual dispatch remains available for recovery.

## Schedule and privacy

The workflow requests a run daily at 06:17 UTC: 07:17 in Warsaw in winter and 08:17 in summer. GitHub scheduling is best effort, can be delayed, and can be disabled after inactivity in public repositories. The workflow runs only from `main` and shares a concurrency group with the existing sync workflow so the two do not reconcile simultaneously. GitHub concurrency can replace pending runs; it is not a durable queue. Bookmarking the Doc gives one-click access even if the latest run fails, so always check its timestamp.

The reporting workflow prints only fixed status messages. It does not upload the report as an artifact, write a job summary, or cache report data. The code, workflow, run status, and generic failure category remain public because the repository is public. The older **Sync LinkedIn Connections** workflow still prints its existing aggregate summary; this change does not alter its output.

Privacy and marker checks happen before reconciliation and again before each Doc update. The content replacement is atomic and requires the revision that was read. If you edit concurrently, the publisher refetches and retries once; other Google failures leave the previous dated report in place and fail the workflow. Sharing can change between the check and the update: Google does not provide a transaction spanning Drive permissions and Docs content. Keep the Doc restricted.

## Reading the result

LinkedIn can end snapshot pagination with a specific no-data HTTP 404 after successful pages. The client treats that exact response as normal completion and retains all fetched rows. A first-page 404, an unrecognized later-page 404, or a page limit still blocks reconciliation. The advertised pagination total is not a reliable stopping rule.

| Status | Meaning | Action |
| --- | --- | --- |
| COMPLETE | The full provider snapshot reconciled; all six counts are available. | Review unmatched rows if nonzero. Counts alone do not identify individual prospects. |
| BLOCKED | The provider snapshot was unavailable or incomplete; counts are unknown. | Restore provider access or pagination, then rerun. |
| FAILED | Reconciliation failed; counts are unknown and database writes may have happened. | Check database state, fix the failure, then rerun the duplicate-safe reconciliation. |

Google authorization/publication failures cannot update the same Doc with a failure report. The previous timestamp remains visible; check the GitHub run status if it is stale. Revoke the refresh token and remove the GitHub secrets to remove the workflow's Google access.

## Repeatable verification

```sh
uv sync --extra dev
PYTHONPATH=src:. .venv/bin/python tests/e2e_doc_report.py --evidence /tmp/google-doc-report-evidence.json
```

This exercises the real reporter and reconciliation code against synthetic HTTP boundaries. The evidence file contains scenario verdicts only, not real reconciliation results or credentials. Live workflow-to-Doc verification requires the one-time setup above.

The **Report Boundary E2E** PR check repeats this synthetic pipeline and the existing tests without access to repository secrets. Its verdicts are public; they contain no production report data.
