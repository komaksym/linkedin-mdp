# Prospect Evidence Reconciliation

## Learning objective

Show the stateless batch-to-report resolver and the invariants that prevent conflicting prospect rows from becoming invented facts.

## Central pipeline

1. `input.json`
2. `strict boundary validation`
3. `immutable evidence + claims + cited validations`
4. `resolve(as_of)`
5. `canonical report.json`

## Architecture decision

Use the stateless resolver for this slice. Keeping adjudication as input evidence makes every result derivable from explicit evidence.

## Invariant callouts

### Identity
A LinkedIn profile URL is a matching key only after strict host/path validation. Names never establish identity.

### Employment
Company and title stay in the same employment claim. The resolver never synthesizes a role from separate observations.

Multiple current roles are valid only when one applicable cited validation supports that complete role set.

### Channel state
Email and LinkedIn facts are derived independently. Drafts and planned actions do not establish sends.

Missing evidence remains unknown.

### Determinism and research
Provider name, input order, retrieval order and evidence count do not decide a conflict.

Research does not mutate hidden resolver state. A researcher adds cited evidence and a validation record to a new input file, then regenerates the report.

Stable IDs, canonical ordering and explicit `as_of` make permutations and retries byte-for-byte reproducible.

## Text labels

Prospect Evidence Reconciliation
Evidence in → deterministic report out
INPUT.JSON
STRICT VALIDATION
IMMUTABLE EVIDENCE
RESOLVE(AS_OF)
CANONICAL REPORT.JSON
IDENTITY
ATOMIC EMPLOYMENT
CHANNEL STATE
CITED RESEARCH
DETERMINISTIC REPLAY
STATELESS RESOLVER
