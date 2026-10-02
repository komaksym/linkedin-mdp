# Actionable LinkedIn report

The report gives person-level recommendations with a name, direct profile link, reason, source evidence, and a manual next action. Aggregate sync counts remain diagnostics.

## Prioritized invitations

Select up to 25 qualified prospects after excluding existing connections, previously invited profiles, opt-outs, unresolved identities, and candidates whose qualification employer conflicts with the current-employer mapping. Keep the existing US PVF targeting and three-per-company lifetime cap across company aliases.

The owner-approved capacity assumption uses each person's latest known employer as present. Count each canonical profile once across all outgoing invitation history. The report must retain source evidence IDs and pointers for provider invitation rows, CRM sent facts, and actual Supabase invitation events. Historical event dates remain audit metadata. They do not establish which employer receives the cap count.

Rank eligible candidates using observed professional activity, mutual connections when available, connection count, and profile completeness, including whether a profile photo is present. Show the factors, their dates, unknown signals, and the scoring version. The score is a heuristic priority score, not an acceptance probability. Do not fill the list with ineligible people to reach 25.

## DM actions

Group DM recommendations by action:

- Reply when the latest verified inbound LinkedIn message is later than the latest outbound message. Include the relevant message and a reminder to respond. The owner writes the reply.
- Prepare a researched first-DM draft for a qualified recent connection when the complete supplied inbox snapshot has no observed prior DM. Ground the opener in saved exact-profile, cited background research and label the bounded history.
- Follow up when a verified outbound DM is at least 72 hours old and has no later reply. Show elapsed time and read status as a reminder. The owner writes the follow-up. Missing read evidence means unknown. No reply does not prove that the person ignored the message.

Exclude opt-outs and terminal negative responses from automatic follow-up recommendations. Keep LinkedIn and email history separate. Invitation notes must not count as first DMs. Missing, stale, malformed, or truncated sources withhold affected actions instead of producing false empty queues.

## Read-only report boundary

The report reads source exports and writes private JSON and Markdown report files. It does not write to Supabase, store recommendations or drafts, or send messages. LinkedIn MDP sync owns persistence of observed real invitations, sent messages, acceptances, and replies. The owner sends every invitation and DM.

The private DM report classifies positive sent and received DM evidence, includes researched first-DM drafts, and shows reply or follow-up reminders. A suggested action does not prove that the action happened. Missing or delayed MDP snapshots mean unknown, not not-sent. No reply or follow-up text is generated. See `docs/private-dm-actions.md` for the offline input contract and coverage limits.

## Current slice and remaining work

PR10 builds a private invitation shortlist with all-history capacity accounting under the latest-known-present employer assumption. It retains exact person identity, current-employer citations, source evidence IDs and pointers, observed invitation date metadata, ranking facts, and explicit unknowns. It does not write report output to Supabase.

The date-added and private DM report slices read existing sources. Independent MDP action ingestion and status projection remain separate work. Do not add schema or infrastructure changes without a separately reviewed diff.
