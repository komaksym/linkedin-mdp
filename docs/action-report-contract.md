# Actionable LinkedIn report

The report has two person-level lists. Aggregate sync counts are diagnostics, not the report's useful output. Each row includes a name, direct profile link, a reason, source evidence, and a concrete next action or message draft.

## Prioritized invitations

Select up to 25 qualified prospects after excluding existing connections, previously invited profiles, opt-outs, and unresolved identities. Retain the existing US PVF targeting and three-per-company lifetime cap across verified company aliases.

Rank eligible candidates using observed professional activity, mutual connections when available, connection count, and profile completeness, including whether a profile photo is present. Show the factors, their dates, unknown signals, and the scoring version. These are inputs to a heuristic priority score. A score is not an acceptance probability until real invitation outcomes support calibration. Save sent and accepted outcomes separately to make that calibration possible. Do not fill the list with ineligible people just to reach 25.

## DM actions

Group the DM list by action:

- Reply when the latest verified inbound LinkedIn message is later than the latest outbound message. Include the relevant message and a reminder to respond; the owner writes the reply.
- Prepare a researched first-DM draft for a verified accepted connection when complete relevant inbox evidence establishes no prior outbound DM. Automatically write a personalized opener grounded in the saved background research.
- Follow up when a verified outbound DM is at least 72 hours old and has no later reply. Show elapsed time and read status as a reminder; the owner writes the follow-up. Missing read evidence means unknown. No reply does not prove the person ignored the message.

Only first-DM drafts are generated automatically. Replies and follow-ups remain manual, with no generated response text. The report does not send any messages.

Exclude opt-outs and terminal negative responses from automatic follow-up recommendations. Keep LinkedIn and email history separate. Invitation notes must not count as first DMs. Missing, stale, malformed, or truncated sources withhold affected actions instead of producing false empty queues.

## Research and storage

For newly accepted connections eligible for first DMs, automatically research the exact person through Exa, Clay, or another available research provider. Save supported professional facts, cited URLs, source dates when known, retrieval time, provider, identity binding, uncertainty, and status in Supabase before publishing a personalized first-message opener. Surface research failures and allow bounded retries. Never invent a personalization fact when research fails.

Log source observations, research attempts and results, report recommendations, drafts, actual sends, acceptances, replies, and run outcomes as distinct facts. A draft or recommendation does not establish that a message was sent. Replay must not duplicate evidence. Preserve failed research attempts separately so they cannot prevent a later successful result from being saved.

The scheduled agent orchestrates the run. Keep ingestion, validation, queue building, persistence, and publication in repository code where practical. The agent can use connected Exa tools and pass a validated research envelope to repository code for persistence. GitHub CI cannot reuse the agent connector's authorization. Schema and scheduled infrastructure changes require separate approval.

## Current slice and remaining work

This PR adds only private INBOX evidence planning and optional persistence for existing prospects. It preserves exact participant identity, message content and subject, attachment evidence, provider time, and uncertainty. It does not produce the two lists yet.

Live inbox persistence and replay are verified. Next, classify invitation notes versus DMs, establish relevant source coverage, integrate invitation eligibility, build the queues, connect research persistence and first-message drafting, and publish the actual person-level report. Enable the scheduled path after live report verification, then observe a scheduled run before claiming completion.
