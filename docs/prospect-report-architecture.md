# Prospect report architecture

## Usage

```text
input.json
  -> strict boundary validation
  -> immutable evidence + claims + cited validations
  -> resolve(as_of)
  -> canonical report.json
```

The operator runs:

```bash
python -m linkedin_mdp_mcp.prospect_report input.json \
  --as-of 2026-09-30T12:00:00Z \
  --output report.json
```

Research does not mutate hidden resolver state. A researcher adds cited evidence and a validation record to a new input file, then regenerates the report.

## Candidate comparison

| Constraint | Stateless resolver | Local CaseBook |
| --- | --- | --- |
| Identity safety | Explicit target binding on every observation | Same |
| Evidence preservation | Full evidence remains in each report | Full evidence plus case references |
| Employment atomicity | Whole role claims only | Whole role claims only |
| Concurrent jobs | One validation can support a complete set of roles | Requires case semantics for a role set |
| Channel separation | Pure derivation per channel | Pure derivation per channel |
| Provenance | Claims and validations reference evidence IDs | Adds resolution IDs and case history |
| Determinism | Input + `as_of` fully determines output | Also depends on persisted case state |
| Public surface | One batch-to-report operation | Batch + CaseBook lifecycle |
| Operational cost | File validation and atomic report write | Adds locking and two-file consistency |

## Synthesis decision

Use the stateless resolver for this slice. Durable case workflow state is not yet required by a live caller. Keeping adjudication as input evidence makes replay, review and future migration into Supabase simpler because every result is derivable from explicit evidence.

The implementation lives at `linkedin_mdp_mcp.prospect_report` rather than a new top-level `prospect_report` package. This keeps the existing wheel configuration unchanged and preserves the selected architecture's public contract: JSON in, deterministic report JSON out.

## Invariants

- A LinkedIn profile URL is a matching key only after strict host/path validation. Names never establish identity.
- Company and title stay in the same employment claim. The resolver never synthesizes a role from separate observations.
- Multiple current roles are valid only when one applicable cited validation supports that complete role set.
- Provider name, input order, retrieval order and evidence count do not decide a conflict.
- `observed_at` records when evidence was seen. It never substitutes for event/effective time.
- Email and LinkedIn facts are derived independently. Drafts and planned actions do not establish sends.
- Missing evidence remains unknown.
- Stable IDs, canonical ordering and explicit `as_of` make permutations and retries byte-for-byte reproducible.
