# Actionable LinkedIn report

The report gives person-level recommendations with a name, direct profile link, reason, source evidence, and a manual next action. Aggregate sync counts remain diagnostics.

## Prioritized invitations

Select up to 25 qualified prospects after excluding existing connections, previously invited profiles, opt-outs, unresolved identities, and candidates whose qualification employer conflicts with the current-employer mapping. Keep the existing US PVF targeting and three-per-company lifetime cap across company aliases.

The owner-approved capacity assumption uses each person's latest known employer as present. Count each canonical profile once across all outgoing invitation history. The report must retain source evidence IDs and pointers for provider invitation rows, CRM sent facts, and actual Supabase invitation events. Historical event dates remain audit metadata. They do not establish which employer receives the cap count.

Rank eligible candidates using observed professional activity, mutual connections when available, connection count, and profile completeness, including whether a profile photo is present. Show the factors, their dates, unknown signals, and the scoring version. The score is a heuristic priority score, not an acceptance probability. Do not fill the list with ineligible people to reach 25.

## DM actions

Group DM recommendations by action:

- Reply when the latest verified inbound LinkedIn message is later than the latest outbound message. Include the relevant message and a reminder to respond. The owner writes the reply.
- Prepare a researched first-DM draft for a verified accepted connection when complete relevant inbox evidence establishes no prior outbound DM. Ground the opener in saved background research.
- Follow up when a verified outbound DM is at least 72 hours old and has no later reply. Show elapsed time and read status as a reminder. The owner writes the follow-up. Missing read evidence means unknown. No reply does not prove that the person ignored the message.

Exclude opt-outs and terminal negative responses from automatic follow-up recommendations. Keep LinkedIn and email history separate. Invitation notes must not count as first DMs. Missing, stale, malformed, or truncated sources withhold affected actions instead of producing false empty queues.

## Read-only report boundary

The report reads source exports and writes private JSON and Markdown report files. It does not write to Supabase, store recommendations or drafts, or send messages. LinkedIn MDP sync owns persistence of observed real invitations, sent messages, acceptances, and replies. The owner sends every invitation and DM.

Later reviewed slices may classify MDP inbox records into sent and received DMs, derive status from complete observations, include researched first-DM drafts, and show reply or follow-up reminders. A suggested action does not prove that the action happened. Missing or delayed MDP snapshots mean unknown, not not-sent. No reply or follow-up text is generated.

## Current slice and remaining work

PR10 builds a private invitation shortlist with all-history capacity accounting under the latest-known-present employer assumption. It retains exact person identity, current-employer citations, source evidence IDs and pointers, observed invitation date metadata, ranking facts, and explicit unknowns. It does not write report output to Supabase.

The next reviewed slices cover independent MDP action ingestion and status projection, researched first-DM drafts in the private report, incoming reply and due follow-up reminders, and prospect `date_added` visibility from the existing database insertion timestamp. Do not add schema or infrastructure changes without a separately reviewed diff.
