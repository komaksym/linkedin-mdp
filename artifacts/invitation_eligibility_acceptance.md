# Invitation eligibility acceptance

Write these behavioral checks before the SQL change.

1. Existing prospect with only CLAY_ENRICHED evidence remains READY for a channel eligibility check.
2. Historical outbound invitation without authoritative timezone becomes INVITE_SENT_NOT_CONNECTED; invite_sent_at remains null; next_action is WAIT.
3. History with an authoritative provider timestamp derives invite_sent_at from provider time.
4. Any prior LINKEDIN_INVITE_SENT, LINKEDIN_INVITATION_HISTORY_FOUND, LINKEDIN_CONNECTION_FOUND, LINKEDIN_MESSAGE_OUTBOUND or LINKEDIN_MESSAGE_INBOUND blocks a fresh invite independently of campaign or enrichment state.
5. Campaign state and unrelated email events preserve existing behavior.
6. Missing prospect, unavailable or failed source never authorizes a send.
7. No historical invite becomes PENDING, accepted, rejected, withdrawn or expired.
8. Production read-only baseline currently has 2 invitation-history prospects, 0 recognized as invited, and 2 actionable. After approved migration those historical-only prospects must no longer remain actionable.

No schema changes were made. A schema migration remains pending explicit human approval under AGENTS.md.
