# Invitation shortlist

`build_invitation_shortlist.py` reads a complete private collector export and a separate cited qualification envelope. It writes a private JSON report and Markdown report. It does not connect to Supabase, schedule work, or send invitations. The JSON contains the report, aggregate reconciliation counts, and a proposed `AGENT_ACTION_REPORT / LINKEDIN_INVITATION_RECOMMENDED` event plan. The plan is evidence for a later persistence step and does not mean a message was sent.

The provider collector must report success, `truncated: false`, and a positive `page_count` for `CONNECTIONS`, `INVITATIONS`, and `INBOX`. `page_count` counts fetched HTTP pages. `raw_elements` retains snapshot elements, so its length can differ from the page count. Its distinct raw row set must equal the collector's exported distinct `rows`; exact duplicates in raw elements are allowed. Supabase `prospects` and `events` envelopes must report success, `truncated: false`, a positive page count, stable consistency metadata, and an exact `row_count`. Source acquisition time must be no more than 24 hours old. Provider generation time stays unknown when the snapshot does not supply it.

Qualification facts bind to the exact canonical LinkedIn profile URL. Current name, role, US country, and PVF employer require an HTTP citation with timezone-aware `retrieved_at` no older than 90 days. The caller attests that the cited fact was researched; the builder checks citation shape and dates but does not fetch or verify webpage contents. The private JSON and event plan state this limit. `source_date` is optional. If an official source has no publication or update date, set it to `null`; the report preserves that unknown and uses retrieval time as the observation date. Never invent a publication date. A supplied stale source date withholds the fact. Each employer ID and alias must appear in the verified `company_registry`. Alias collisions across company IDs reject the input. The table below shows the required shape; the URLs and values are synthetic.

```json
{
  "schema_version": 1,
  "company_registry": [
    {"company_id": "co-1", "name": "Example PVF", "aliases": ["Example PVF", "Example PVF LLC"]}
  ],
  "candidates": [
    {
      "profile_url": "https://www.linkedin.com/in/example-person",
      "identity": {
        "name": {"value": "Example Person", "citations": [{"url": "https://evidence.example/profile", "source_date": "2026-09-20", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "current_role": {"value": "Buyer", "citations": [{"url": "https://evidence.example/role", "source_date": "2026-09-20", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "country": {"value": "US", "citations": [{"url": "https://evidence.example/location", "source_date": "2026-09-20", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "pvf_employer": {"value": true, "company_id": "co-1", "company_name": "Example PVF LLC", "citations": [{"url": "https://evidence.example/employer", "source_date": "2026-09-20", "retrieved_at": "2026-10-02T10:00:00Z"}]}
      },
      "rank_factors": {
        "activity": {"value": true, "observed_at": "2026-10-01T10:00:00Z", "citations": [{"url": "https://evidence.example/activity", "source_date": "2026-10-01", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "mutual_connections": {"value": 3, "observed_at": "2026-10-01T10:00:00Z", "citations": [{"url": "https://evidence.example/connections", "source_date": "2026-10-01", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "connection_count": {"value": 480, "observed_at": "2026-10-01T10:00:00Z", "citations": [{"url": "https://evidence.example/count", "source_date": "2026-10-01", "retrieved_at": "2026-10-02T10:00:00Z"}]},
        "profile_photo": {"value": true, "observed_at": "2026-10-01T10:00:00Z", "citations": [{"url": "https://evidence.example/photo", "source_date": "2026-10-01", "retrieved_at": "2026-10-02T10:00:00Z"}]}
      }
    }
  ],
  "history_company_bindings": []
}
```

The active CLI and pure function require `--cap-scope all_history`; there is no weaker CRM-only policy option. Every outgoing invitation event needs its own company-at-event attestation. A binding uses `{ "evidence_id", "profile_url", "company_id", "company_at_event": true, "event_date", "employment_interval", "citations" }`, where `employment_interval` has inclusive `start_date` and optional `end_date`. A source may instead provide `historical_claim_date` equal to the invitation event date. The event date must exactly match the retained source event date. Current-employer facts alone cannot bind an older invitation. Citations keep `retrieved_at` and may have `source_date: null` when the official page is undated. Evidence IDs are `history-evidence-sha256:` plus SHA-256 of compact sorted JSON for `["invitation", full_provider_row]` or `["event", [source, external_key_or_id_or_fallback]]`. Missing bindings create actionable private queue rows containing the exact canonical profile URL when known, evidence ID, source row pointer, event date, and reason. Explicit CRM `Invite Sent Date` values and sent statuses also create invitation events. A strict `YYYY-MM-DD` CRM date records day precision; malformed dates or sent statuses without dates remain unresolved queue items. The CRM `Company` field never proves the employer at invitation time. `Accepted Date` and connection state exclude a candidate but do not establish an outgoing invitation or consume capacity. Unsupported URLs remain unresolved and are never printed as raw source rows. Each invitation event is bound separately, so a person invited at different employers can consume a slot at each verified company. Capacity counts distinct exact profiles per company. Current connections exclude prospects but do not consume invitation slots. An empty, complete history is different from missing evidence. Provider `Sent At` values use the retained `M/D/YY, h:mm AM/PM` format. The report preserves the raw time and calendar day with `provider_local_day` precision and unknown timezone; it never invents an instant. Supabase invitation events use `occurred_at` only when their payload explicitly says `timestamp_semantics: actual` and `timestamp_precision: date` or `instant`. MDP history events use `provider_sent_at` only with `provider_local_time_timezone_unspecified` semantics. Import and observation timestamps remain unknown for cap purposes.

Each recommendation audit event retains its qualification citations, known and unknown ranking factors, source digest, cap scope, recorded company count, and supporting historical evidence IDs. The output remains a recommendation plan and never writes to Supabase.

The priority heuristic is versioned as `invitation-priority-v1`. It awards up to 4 points for observed exact-person activity, 3 for mutual connections, 2 for connection count, and 1 for a profile photo. Activity must be at most 30 days old. Other ranking signals must be at most 90 days old. Numeric inputs are capped at 20 mutual connections and 500 total connections for scoring. Missing or stale signals stay unknown and do not produce an all-unknown zero score. The score is a sorting aid, not an acceptance probability. Stable ties use the canonical profile URL. Eligibility and remaining lifetime company slots are applied before selecting at most 25 rows.

Run with private files and a new output directory:

```sh
python scripts/build_invitation_shortlist.py \
  --source /private/path/source.json \
  --qualification /private/path/qualification.json \
  --output-dir /private/path/shortlist-2026-10-02 \
  --cap-scope all_history
```

Input files must be regular files with no group or world permissions. The CLI creates the new output directory with mode `0700` and both reports with mode `0600`. Existing output directories are refused. Standard output contains only a fixed completion status; validation errors never include row content, names, URLs, or message text. `tests/e2e_invitation_shortlist.py` exercises synthetic source boundaries. `tests/test_invitation_shortlist_e2e.py` runs the matrix and writes a sanitized artifact with fixed verdicts and a digest.

Review boundary corrections: a successfully fetched empty provider page may have zero snapshot elements; positive page count, complete status and matching raw/exported row sets still apply. CRM date keys use the same normalized labels as send detection; conflicting alias values leave the date unknown. Date-precision events retain the source calendar day before UTC conversion. Explicit negative opt-out values (`false`, `no`, `0`, empty or absent) remain negative; affirmative or uninterpretable nonempty flags exclude the prospect.
