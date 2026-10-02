# Private source collector timestamp check

The collector reads the existing `prospects.created_at` column and keeps its exact value in the encrypted prospect rows. A missing value stays missing. This slice changes no schema, workflow, schedule, or outreach behavior.

Run `PYTHONPATH=src python diagnostics/action-report-private-source-20261002/e2e_private_source.py` from the repository root. The test uses synthetic HTTP responses with the actual clients and OpenSSL CMS roundtrip. Its database mock applies the requested `select` columns, so omitting `created_at` from the collector projection fails the timestamp check. It writes only fixed synthetic verdicts to `e2e-evidence.json`; the temporary certificate, key, and ciphertext are removed when the test ends. The test does not contact live services.
