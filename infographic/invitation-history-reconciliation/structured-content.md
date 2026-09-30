# LinkedIn invitation history reconciliation
Learning objective. Explain how historical outbound invitation evidence prevents another fresh invitation without inferring pending status.

- Complete snapshot. LinkedIn INVITATIONS, outbound only. Incomplete snapshot stops before writes.
- Identity match. Canonical profile URL, existing prospects only.
- Durable evidence. LINKEDIN_INVITATION_HISTORY_FOUND. Key uses URL and provider timestamp. Replay inserts no duplicates.
- Eligibility. Prior invite evidence blocks a fresh invite. Lifecycle stays unknown.
- Operational guardrails. Unknown provider timezone preserved. Aggregate-only logs.
