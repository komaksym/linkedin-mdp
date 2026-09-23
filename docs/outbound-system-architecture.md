# LinkedIn MDP Outbound System Architecture

## Objective

Build a production-usable outbound system where:

```text
LinkedIn MDP = read-only source of truth for LinkedIn reality
Supabase/Postgres = operational source of truth and decision layer
Clay = enrichment
La Growth Machine (LGM) = outbound execution
ChatGPT/MCP = operator and reasoning interface
Google Sheets = legacy/import/reporting surface, not operational truth
```

## Core architecture

```text
                    +----------------+
                    |      Clay      |
                    |   enrichment   |
                    +-------+--------+
                            |
                            | enrichment evidence
                            v
+----------------+   +-------------------+   +--------------------+
| LinkedIn MDP   |-->|     Supabase      |-->|        LGM         |
| read-only      |   | operational truth |   | outbound executor  |
| observation    |   | state + rules     |   | email / LinkedIn   |
+-------+--------+   +---------^---------+   +---------+----------+
        |                      |                       |
        | LinkedIn evidence    | execution evidence    |
        +----------------------+-----------------------+
                               |
                               v
                        ChatGPT / MCP
```

## Responsibilities

- **LinkedIn MDP = sensor.** Observes actual LinkedIn state through the official read-only Member Data Portability API. It never performs LinkedIn mutations.
- **Supabase/Postgres = brain.** Stores prospects and normalized provider evidence, derives current state, applies suppression and eligibility rules, and is the only component that decides whether a prospect may enter outreach.
- **Clay = enrichment provider.** Adds enrichment evidence such as company/person research and discovered email addresses. Clay does not decide outreach eligibility.
- **La Growth Machine (LGM) = actuator/executor.** Executes approved outbound sequences. LGM does not independently decide whether a prospect is safe or eligible to contact.
- **ChatGPT/MCP = operator interface.** Queries unified state and eventually triggers controlled actions such as queueing already-eligible prospects.

## Core invariant

```text
store evidence
    ->
derive state
    ->
decide eligibility
    ->
execute
    ->
reconcile
```

Do not collapse provider claims into one mutable status field.

## Command vs observation

LGM and LinkedIn MDP represent different kinds of evidence:

```text
LGM
  = what the system asked, scheduled, or executed

LinkedIn MDP
  = what LinkedIn actually exposes as having happened
```

Example:

```text
LGM_LINKEDIN_STEP
        +
LINKEDIN_INVITE_SENT observed by MDP
        =
execution claim + independent LinkedIn evidence
```

Both events should coexist.

This lets the system distinguish:

```text
"we attempted this"
```

from:

```text
"LinkedIn confirms this exists"
```

That distinction is required for reconciliation and failure detection.

## Authority by fact

| Fact | Primary authority |
| --- | --- |
| Prospect identity / operational state | Supabase |
| Outreach eligibility / suppression | Supabase |
| Enrichment result | Clay evidence stored in Supabase |
| LGM campaign membership | LGM evidence stored in Supabase |
| LGM email execution | LGM evidence stored in Supabase |
| LinkedIn connection evidence | LinkedIn MDP |
| LinkedIn invitation evidence | LinkedIn MDP |
| LinkedIn message evidence | LinkedIn MDP |

## Provider event flow

All providers should write normalized evidence into the same event model:

```text
LinkedIn MDP --+
Clay ----------+--> normalized events --> derived state --> eligibility
LGM -----------+                              |
                                               v
                                              LGM
```

Current intended event families:

```text
LinkedIn
  LINKEDIN_CONNECTION_FOUND
  LINKEDIN_INVITE_SENT
  LINKEDIN_MESSAGE_OUTBOUND
  LINKEDIN_MESSAGE_INBOUND

Clay
  CLAY_ENRICHED
  CLAY_EMAIL_FOUND

LGM
  LGM_LEAD_ADDED
  LGM_EMAIL_SENT
  LGM_LINKEDIN_STEP
  LGM_REPLY
  LGM_CAMPAIGN_FINISHED
```

Raw evidence remains separate from derived current state.

## Prospect lifecycle

```text
Prospect
   |
   v
Supabase
NEEDS_ENRICHMENT
   |
   v
Clay
   |
   | CLAY_ENRICHED / CLAY_EMAIL_FOUND
   v
Supabase
   |
   | eligibility / suppression rules
   |
   +-- already replied? --------> suppress
   +-- active conversation? ----> suppress
   +-- already in campaign? ----> suppress
   +-- bad / unsafe email? -----> suppress
   +-- cooldown active? --------> suppress
   +-- still needs enrichment? -> Clay
   |
   +-- safe and ready ----------> READY
                                  |
                                  v
                                 LGM
                                  |
                         execute sequence
                                  |
                                  v
                         LGM events/webhooks
                                  |
                                  v
                              Supabase
```

## LinkedIn reconciliation model

Fresh LinkedIn MDP to Supabase reconciliation is not implemented yet.

Target model:

```text
changelog = cheap incremental signal about where to look
snapshot  = durable reconciliation evidence
events    = idempotent normalized evidence
state     = derived from accumulated evidence
```

Recommended behavior:

```text
frequent sync:
  changelog
      ->
  fetch affected snapshots
      ->
  normalize
      ->
  dedupe
      ->
  resolve prospect identity
      ->
  insert only new events

periodic repair:
  full CONNECTIONS snapshot
  full INVITATIONS snapshot
  full INBOX snapshot
      ->
  reconcile again
```

Repeated ingestion of the same provider record must create zero duplicate events.

## Important constraints

```text
MUST:
- LinkedIn integration remains strictly read-only
- use official LinkedIn MDP API only
- Supabase remains operational source of truth
- raw provider evidence remains separate from derived state
- confirmed evidence must be distinguishable from inference
- writes must be idempotent
- secrets stay out of source and chat
- private LinkedIn messages must not be publicly logged

MUST NOT:
- scrape LinkedIn
- automate LinkedIn browser actions
- use private LinkedIn APIs
- let Clay independently decide outreach eligibility
- let LGM independently decide outreach eligibility
- invent invitation lifecycle states
- invent message read states
- treat Google Sheets as operational truth
```

## Mental model

```text
Clay     = learn more
MDP      = observe reality
Supabase = remember + decide
LGM      = act
ChatGPT  = operate
```

The foundational design principle is:

```text
command != observation
```

An automation system becomes reliable when it separately records what it intended to do and what the external system actually shows happened.
