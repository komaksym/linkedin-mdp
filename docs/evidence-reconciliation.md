# Prospect evidence reconciliation

This rule governs derived prospect facts. It does not change the raw LinkedIn MDP response or turn a snapshot page into a database event.

## Rule

Identify a prospect by a normalized LinkedIn profile URL. Keep source observations as evidence. Derive each fact separately from evidence that can establish that fact. Never choose a whole snapshot row because it arrived later, appears on a later page, or was inserted into the database later.

For each derived fact, retain its value, source, source record ID, event time when known, observation time, and confidence or `unknown`. A source event time may order observations from the same source. Observation time says when we saw the record, not when the underlying fact changed. If reliable evidence conflicts or has no usable order, report `unknown` or `needs_review`; do not silently pick a winner.

| Fact | Positive evidence | What does not prove the opposite or establish freshness |
| --- | --- | --- |
| Current company | An explicitly current role on a LinkedIn profile, or another dated primary source that identifies the same person. Keep the source URL and checked time. | A CONNECTIONS `Company` field, a search result's crawl date, or later pagination position. Search can suggest a candidate, but a search hit alone cannot prove employment is current. |
| Email sent | A Gmail message in **Sent** with the matched recipient, a stable Gmail message ID, and its sent timestamp. | A draft, a campaign step, or a DB row saying “planned.” No search hit means `unknown` unless the search covered all relevant sending accounts and the full period. |
| LinkedIn invitation sent | A matching outbound invitation in MDP `INVITATIONS`, with its provider timestamp when available. | An invitation record does not establish that it is still pending. Absence from a partial or unavailable snapshot does not establish “not sent.” |
| LinkedIn connection accepted | The prospect appears in an authoritative, complete *current* CONNECTIONS snapshot, or LinkedIn provides an explicit acceptance event. | A historical invitation, a non-truncated union of unordered snapshot versions, or an old `Connected On` value. Without a proven current snapshot version, the result is positive connection evidence, not verified current membership. |
| LinkedIn DM sent | A matching outbound message in MDP `INBOX`, identified by conversation and recipient with a provider message ID or timestamp. | A planned LinkedIn step, an invitation, or absence from an incomplete inbox. Missing `readAt` does not prove unread. |

Before an automated send, verify the corresponding channel state from its authoritative source. If the source is unavailable, incomplete, ambiguously matched, or contradictory, stop that send and request review. Do not turn missing evidence into a negative fact. In particular, `INVITE_SENT_NOT_CONNECTED` means “invite evidence exists and connection evidence is not established”; it is not LinkedIn's pending status.

## Where this applies

`LinkedInMDPClient.snapshot()` remains a lossless transport of exact-distinct JSON rows and `raw_elements`. Its pagination order is not a version clock. The proposed Supabase sync should normalize provider observations into `public.events`; `public.outreach_state(text)` should derive prospect actions from those events. The current sync design is not implemented, and the current SQL function treats any `LINKEDIN_CONNECTION_FOUND` event as connected. That is positive historical evidence, not a current-membership check. Do not label it verified current until the source version and completeness are established.

## Checks that prevent the same mistake

1. Two rows with the same URL and different `Company` values must not select a current company based on page order or DB insertion order.
2. A sent Gmail message must override a planned email step. A Gmail draft must not count as sent.
3. An outbound invitation plus no confirmed connection must not become “rejected,” “pending,” or “accepted.”
4. A truncated, unavailable, or unordered CONNECTIONS snapshot must not turn missing rows into “not connected.”
5. A verified outbound DM must be tied to the intended recipient and conversation; a campaign step alone must not count as delivery.

Implement these as end-to-end checks when the relevant Gmail and LinkedIn ingestion paths exist. Preserve source IDs in evidence so the derived state can be audited without relying on a row's position.
