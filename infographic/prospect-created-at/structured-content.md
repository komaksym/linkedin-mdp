# Prospect timestamp collection

Learning objective: understand where the existing timestamp was dropped and how it now survives collection.

Three separated layers: Supabase existing prospects.created_at; collector request existing column; encrypted export exact timestamp retained. Verification band: synthetic E2E 19 checks passed. Scope: live values not inspected; no schema or schedule change.
