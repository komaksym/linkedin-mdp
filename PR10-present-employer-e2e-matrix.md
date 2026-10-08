# PR10 present-employer red-first matrix

Data shape under test: `current_employers` maps raw LinkedIn profile URL keys to `{company_id, citations}`. URLs normalize through the inbox exact-identity normalizer. The owner-approved assumption is that the latest known employer is present. Invitation, CRM, and actual Supabase send evidence is grouped by canonical person and charged once to that employer across all history. Evidence IDs and source pointers remain in report audit data.

| Failure case | Expected behavior |
| --- | --- |
| Invitation has no source date | Still counts against mapped latest employer. |
| Same person appears in provider, CRM, and actual send event history | Counts once and retains all three evidence references. |
| Historical CRM Company disagrees with latest employer | Ignore it as employer proof; latest employer owns the count. |
| Profile URL aliases normalize to conflicting current employers | Withhold all capacity claims and queue the identity conflict. |
| Candidate qualification employer differs from owner mapping | Withhold that candidate. |
| Historical invitation has unsupported identity or absent/unregistered mapping | Withhold the global shortlist and retain an explicit queue item. |
| Recommendation report is built | No recommendation event plan or persistence key is emitted. |
| Existing sent/accepted/connectivity/suppression, ranking, exact identity, and transport freshness rules | Preserve prior behavior. |

## Red-first evidence

Run `uv run --cache-dir /private/tmp/action-report-uv-cache --extra dev pytest -q tests/e2e_invitation_shortlist.py` against base `19a769a55a85330ccb43b26e3433c34303e431f0` before implementation. Existing 70 scenarios passed. The five new behavior assertions failed as intended: current-employer all-history accounting, canonical mapping conflict, candidate/mapping conflict, unknown historical identity, and no event plan.

- Result: 70 passed, 5 failed. `pytest` first failed to start because the global shim pointed at a removed Python; rerunning through the isolated UV environment completed the red-first matrix.
- Sanitized result digest: `sha256:8cd8d7a1ea3aa939`

## Green verification

The revised matrix passes 75 scenarios plus its artifact wrapper. Parent full suite passes 22 checks; scoped strict Ruff, mypy over 12 source files, and wheel/source distribution builds pass. Private real-source CLI returns an explicit unresolved current-employer queue under the empty qualification envelope, without event plans or database access. Outputs retain 0700/0600 permissions. No live shortlist eligibility or finished report publication is claimed.

## Review correction

Greptile found a valid canonical employer assignment could remain in audit after an invalid alias queued that same person. Six cases covering malformed assignment, unknown company and missing citations in both orders failed before the correction. Every queued exact profile now loses its resolved assignment and capacity attribution. CodeRabbit found two distinct unsupported employer identity keys collapsed into one queue item; a dedicated scenario reproduced that loss before the correction. Each employer key now has a stable opaque evidence ID and input key index pointer. All 82 scenarios plus the wrapper, 22 full-suite checks, scoped Ruff, 12-file mypy and builds pass. The existing raster diagram still describes these unknown-handling guards accurately.

## Unicode key repair

Two JSON-decoded unpaired surrogate key cases failed before the repair because raw UTF-8 hashing raised UnicodeEncodeError before unresolved evidence was recorded. Current-employer key IDs now hash ASCII-escaped JSON bytes. Identity normalization stays unchanged, and invalid keys remain unresolved without exposing them. The 84-scenario matrix plus wrapper, 22 full-suite checks, scoped strict Ruff, 12-file mypy and source/wheel builds pass. Surrogate fixtures use distinct numeric codepoints without a lint suppression.
