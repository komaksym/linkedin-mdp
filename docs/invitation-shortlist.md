# Invitation shortlist

`scripts/build_invitation_shortlist.py` reads a complete private collector export and a cited qualification envelope. It writes a private JSON report and Markdown report. It does not connect to Supabase, store recommendations, schedule work, or send invitations.

## Input completeness

The provider collector must report success, `truncated: false`, and a positive `page_count` for `CONNECTIONS`, `INVITATIONS`, and `INBOX`. `page_count` counts fetched HTTP pages. `raw_elements` retains snapshot elements, so its length can differ from the page count. Its distinct raw row set must equal the collector's exported distinct `rows`; exact duplicate raw elements are allowed. Row comparison hashes canonical ASCII-escaped JSON, so JSON-permitted escaped surrogate metadata does not invalidate matching rows.

Supabase `prospects` and `events` envelopes must report success, `truncated: false`, a positive page count, stable consistency metadata, and an exact `row_count`. Source acquisition time must be no more than 24 hours old. Provider generation time stays unknown when the snapshot does not supply it.

## Qualification and current employers

Qualification facts bind to exact canonical LinkedIn profile URLs. Current name, role, US country, and PVF employer require an HTTP citation with timezone-aware `retrieved_at` no older than 90 days. The caller attests that the cited fact was researched. The builder checks citation shape and retrieval freshness but does not fetch webpage contents.

The `current_employers` object maps raw profile URL keys to cited registered present employers (legacy single company or explicit set). URLs normalize with the same Unicode-preserving identity normalizer used for inbox participants. Multiple keys that normalize to one profile may repeat the same company assignment. Conflicting assignments, unregistered companies, invalid citations, or unsupported identities enter the private queue and withhold all capacity claims. The owner's explicit assumption is that each explicitly cited current job is present. Citation retrieval time must be recent; source publication or indexing date does not determine historical employer eligibility.

Each unresolved employer key retains a stable opaque evidence ID and input key index pointer. An invalid assignment for any canonical alias removes that person from resolved employer and capacity audit entries.

A candidate's qualification company must match the current-employer mapping. A conflict withholds that candidate. The report retains the current-employer citations and labels the present-employer assumption in its audit.

The example uses synthetic identities and URLs.

```json
{
  "schema_version": 1,
  "company_registry": [
    {"company_id": "co-1", "name": "Example PVF", "aliases": ["Example PVF", "Example PVF LLC"]}
  ],
  "current_employers": {
    "https://www.linkedin.com/in/example-person": {
      "company_id": "co-1",
      "citations": [{"url": "https://evidence.example/employer", "source_date": null, "retrieved_at": "2026-10-02T10:00:00Z"}]
    }
  },
  "candidates": [
    {
      "profile_url": "https://www.linkedin.com/in/example-person",
      "identity": {
        "name": {"value": "Example Person", "citations": [{"url": "https://evidence.example/profile", "source_date": null, "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "current_role": {"value": "Buyer", "citations": [{"url": "https://evidence.example/role", "source_date": null, "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "country": {"value": "US", "citations": [{"url": "https://evidence.example/location", "source_date": null, "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "pvf_employer": {"value": true, "company_id": "co-1", "company_name": "Example PVF LLC", "citations": [{"url": "https://evidence.example/employer", "source_date": null, "retrieved_at": "2026-10-02T10:00:00Z"}]}
      },
      "rank_factors": {}
    }
  ]
}
```

## All-history capacity

The CLI and pure function require `--cap-scope all_history`. Every outgoing invitation source is grouped by normalized exact profile identity. Each historical person consumes one invitation slot at each company in their complete present-employer set, regardless of whether the evidence comes from provider invitations, explicit CRM sent facts, or actual Supabase invitation events. Historical invitation dates and previous employer claims remain audit data. They do not gate capacity or change the present-employer set.

The JSON audit retains each source evidence ID, source row pointer, observed event date when available, profile URL, and the current-employer citations. Unknown or unsupported invitation identities and profiles without a valid current-employer mapping enter `research_queue`. Any unresolved queue row withholds the entire shortlist and all remaining-slot claims. The `Company` field in CRM is never employer proof. Accepted dates and connections do not consume invitation slots. They still exclude people from recommendations under the existing eligibility rules.

Provider `Sent At` values preserve the retained `M/D/YY, h:mm AM/PM` text and provider-local calendar date when parseable. The timezone remains unknown. Supabase event dates remain source audit metadata; observation or import time is never substituted for the provider date.

## Ranking and output

The priority heuristic is versioned as `invitation-priority-v1`. It awards up to 4 points for observed exact-person activity, 3 for mutual connections, 2 for connection count, and 1 for a profile photo. Activity must be at most 30 days old. Other ranking signals must be at most 90 days old. Numeric inputs are capped at 20 mutual connections and 500 total connections for scoring. Missing or stale signals stay unknown and do not produce an all-unknown zero score. The score is a sorting aid, not an acceptance probability. Stable ties use the canonical profile URL. Eligibility and remaining lifetime company slots apply before the report selects at most 25 rows.

Each selected invitation row may also show `date_added`, read only from `created_at` on exactly one canonical-profile-matched Supabase prospect row. This is the database record creation timestamp, not a LinkedIn connection or acceptance date. Preserve a valid timezone-aware ISO string exactly and show its `prospects.rows[i].created_at` source pointer. Missing, malformed, naive, or future timestamps remain `unknown`; the report retains the unique source row ID and a fixed reason without copying an invalid value. Zero or multiple matching prospect rows have no source provenance. Qualification data cannot override this field, and it never affects shortlist eligibility, ranking, exclusions, or capacity.

The report preserves existing exact identity checks, suppression handling, connected and previously invited exclusions, candidate qualification checks, ranking behavior, and transport freshness rules. The report includes no event plan, persistence key, draft, or hypothetical Supabase event.

Run with private files and a new output directory:

```sh
python scripts/build_invitation_shortlist.py \
  --source /private/path/source.json \
  --qualification /private/path/qualification.json \
  --output-dir /private/path/shortlist-2026-10-02 \
  --cap-scope all_history
```

Input files must be regular files with no group or world permissions. The CLI creates the new output directory with mode `0700` and both reports with mode `0600`. It refuses existing output directories. Standard output contains only a fixed completion status. Validation errors never include row content, names, URLs, or message text. `tests/e2e_invitation_shortlist.py` covers synthetic source boundaries. `tests/test_invitation_shortlist_e2e.py` runs the matrix and writes a sanitized artifact with fixed verdicts and a digest.

A successfully fetched empty provider page may have zero snapshot elements when its status and exported rows prove completeness. CRM opt-out fields treat explicit negative values (`false`, `no`, `0`, empty, or absent) as negative. Affirmative or uninterpretable nonempty values suppress a prospect.

### Concurrent present employers

Legacy `{ "company_id": "co-a", "citations": [...] }` assignments remain valid singleton claims. For explicit concurrent jobs, use `{ "assignments": [{ "company_id": "co-a", "citations": [...] }, { "company_id": "co-b", "citations": [...] }], "coverage": "complete", "unknown_reasons": [] }`. Each company needs its own dated citations. Duplicate same-company roles and citations collapse without consuming extra capacity. No primary employer or corporate alias is inferred. Do not mix singleton fields with `assignments`.

Under the owner's all-jobs-present assumption, every historical outgoing profile counts once per assigned company across all history. A candidate's qualified company must belong to its present set. Selection requires room in every present company and consumes one slot in each. A person still appears once in the invitation list. Distinct canonical URL keys may repeat an identical complete company set; different sets remain conflicting instead of being silently unioned. Empty, unregistered, partial or unnamed assignments retain explicit research reasons and withhold global capacity claims. Incomplete sets are omitted from resolved assignment and capacity audits; their cited positives remain in the original qualification input, not asserted as complete report assignments.

`current_employer_assignments` keeps its existing flat profile/company/citations rows, with multiple rows for concurrent employers. The additive `current_employer_sets` audit lists each resolved profile's company IDs and complete coverage. `company_usage.remaining_slots` retains its historical-capacity meaning; `remaining_slots_after_selection` additionally shows remaining capacity after all proposed invitations. Neither a proposal nor an assignment records an actual send. Existing combined report rendering accepts the flat rows without a schema change.
