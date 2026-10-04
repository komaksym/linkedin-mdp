# Private Google Doc reports

The reporting feature uses three owner-only Google Docs. The connections reporter publishes aggregate CONNECTIONS reconciliation counts. The private DM publisher writes first-time DM actions and follow-up reminders to separate Docs. Each publisher updates only the automated section and preserves your notes around it.

The scheduled morning workflow currently updates only the connections Doc. The repository does not yet have a durable scheduled step that creates `private-dm-actions.json`, so the workflow cannot safely publish the two DM Docs yet.

## One-time setup

1. Create three Google Docs in your own Drive. Use one Doc for connections, one for first-time DMs, and one for follow-ups. Set **General access: Restricted** and do not add other users. Do not publish the Docs to the web or put them in a shared drive. Give each automated section these exact standalone marker paragraphs:

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

   - `GOOGLE_REPORT_DOCUMENT_ID`: the connections Doc ID. This existing name stays unchanged for compatibility.
   - `GOOGLE_FIRST_DM_REPORT_DOCUMENT_ID`: the first-time DM Doc ID.
   - `GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID`: the follow-up Doc ID.
   - `GOOGLE_OAUTH_CLIENT_ID`
   - `GOOGLE_OAUTH_CLIENT_SECRET`
   - `GOOGLE_OAUTH_REFRESH_TOKEN`

   Keep all three Doc IDs private even though they are not passwords. Reuse the existing `ORION_TOKEN` and `SUPABASE_SERVICE_ROLE_KEY` secrets. The Google account must own each target document. The publisher rejects every additional sharing grant, including published access.

4. After the reporting change and its reconciliation dependency reach `main`, manually run **Private Morning Report** on `main`. Open the connections Doc and verify its actual timestamp, status, and counts against the reconciliation outcome. Check that your notes survived. A synthetic test does not replace this live authorization and delivery check.

5. Only after that check, set the repository variable `GOOGLE_DOC_REPORT_ENABLED` to `true`. Remove it or set it to `false` to disable automatic runs. Manual dispatch remains available for recovery.

The two DM Doc IDs are ready for `scripts/publish_private_dm_docs.py`, but the scheduled workflow does not use them yet. Publish the DM Docs only from a run that already has the private `private-dm-actions.json` input.

## Current live rollout state

As of 2026-10-05, a live Drive readback confirmed these restricted, owner-only Docs:

- `LinkedIn connections / invitations report`
- `LinkedIn first-time DM report`
- `LinkedIn follow-up DM report`

Keep the three document IDs in secrets. Do not store them in repository documentation.

## Schedule and privacy

The workflow requests a run daily at 06:17 UTC: 07:17 in Warsaw in winter and 08:17 in summer. GitHub scheduling is best effort, can be delayed, and can be disabled after inactivity in public repositories. The workflow runs only from `main` and shares a concurrency group with the existing sync workflow so the two do not reconcile simultaneously. GitHub concurrency can replace pending runs. It is not a durable queue. Bookmarking the connections Doc gives one-click access even if the latest run fails, so always check its timestamp.

The morning workflow does not create `private-dm-actions.json`. `scripts/build_private_dm_actions.py` creates that file only from explicit private inputs supplied to the CLI. Until a durable production acquisition and build step exists on the runner, do not add `scripts/publish_private_dm_docs.py` to the scheduled job. Adding the publisher alone would either fail because the input file is absent or require an unsafe ad hoc source for private message data.

The reporting workflow prints only fixed status messages. It does not upload the report as an artifact, write a job summary, or cache report data. The code, workflow, run status, and generic failure category remain public because the repository is public. The older **Sync LinkedIn Connections** workflow still prints its existing aggregate summary; this change does not alter its output.

Each publisher checks privacy and markers before it updates a Doc. The content replacement is atomic and requires the revision that was read. If you edit concurrently, the publisher refetches and retries once. Other Google failures leave the previous report in place. Sharing can change between the check and the update because Google does not provide a transaction that spans Drive permissions and Docs content. Keep all three Docs restricted.

## Reading the connections result

LinkedIn can end snapshot pagination with a specific no-data HTTP 404 after successful pages. The client treats that exact response as normal completion and retains all fetched rows. A first-page 404, an unrecognized later-page 404, or a page limit still blocks reconciliation. The advertised pagination total is not a reliable stopping rule.

| Status | Meaning | Action |
| --- | --- | --- |
| COMPLETE | The full provider snapshot reconciled; all six counts are available. | Review unmatched rows if nonzero. Counts alone do not identify individual prospects. |
| BLOCKED | The provider snapshot was unavailable or incomplete; counts are unknown. | Restore provider access or pagination, then rerun. |
| FAILED | Reconciliation failed; counts are unknown and database writes may have happened. | Check database state, fix the failure, then rerun the duplicate-safe reconciliation. |

Google authorization/publication failures cannot update the same Doc with a failure report. The previous timestamp remains visible; check the GitHub run status if it is stale. Revoke the refresh token and remove the GitHub secrets to remove the workflow's Google access.

## Reading the DM results

The first-time DM Doc is dedicated to first-DM status. It can show cleared rows, conditional researched drafts, and withheld rows. A saved researched draft can appear there, but the owner still reviews and sends it. The publisher keeps `read_state` as `unknown` unless evidence proves another state.

The follow-up Doc is a reminder report. It can show the verified previous outbound message, its timestamp, elapsed time, evidence references, and the action `Owner writes follow-up`. It does not generate follow-up copy. Reply rows remain in the private local action report and go to neither Google Doc.

## Repeatable verification

```sh
uv sync --extra dev
PYTHONPATH=src:. .venv/bin/python tests/e2e_doc_report.py --evidence /tmp/google-doc-report-evidence.json
PYTHONPATH=src:. .venv/bin/python tests/e2e_private_dm_doc_report.py --evidence /tmp/private-dm-doc-report-evidence.json
```

These checks exercise the connections reporter and the DM publication entry point against synthetic Google HTTP boundaries. The evidence files contain scenario verdicts only, not real report data or credentials. Live workflow-to-Doc verification requires the one-time setup above.

The **Report Boundary E2E** PR check repeats both synthetic publication checks and the existing tests without access to repository secrets. Its verdicts are public and contain no production report data.
