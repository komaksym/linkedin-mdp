create or replace function public.normalize_linkedin_url(value text)
returns text
language sql
immutable
strict
security invoker
set search_path = public
as $$
  select nullif(
    regexp_replace(
      regexp_replace(
        regexp_replace(
          btrim(value),
          '[?#].*$',
          ''
        ),
        '^https?://([[:alnum:]-]+\\.)?linkedin\\.com',
        'https://www.linkedin.com',
        'i'
      ),
      '/+$',
      ''
    ),
    ''
  );
$$;

create table public.prospects (
  id uuid primary key default gen_random_uuid(),
  linkedin_url text not null,
  linkedin_url_key text generated always as (
    public.normalize_linkedin_url(linkedin_url)
  ) stored,
  attributes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint prospects_linkedin_url_key_not_null
    check (linkedin_url_key is not null)
);

create unique index prospects_linkedin_url_key_uidx
  on public.prospects (linkedin_url_key);

create table public.events (
  id uuid primary key default gen_random_uuid(),
  prospect_id uuid not null references public.prospects(id),
  source text not null check (btrim(source) <> ''),
  event_type text not null check (btrim(event_type) <> ''),
  external_key text,
  occurred_at timestamptz not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index events_source_external_key_uidx
  on public.events (source, external_key)
  where external_key is not null;

create index events_prospect_occurred_idx
  on public.events (prospect_id, occurred_at desc, created_at desc);

alter table public.prospects enable row level security;
alter table public.events enable row level security;

revoke all on table public.prospects, public.events from anon, authenticated;
grant select, insert, update, delete on table public.prospects, public.events to service_role;

create or replace function public.outreach_state(p_linkedin_url text)
returns table (
  linkedin_url text,
  connected boolean,
  invite_sent_at timestamptz,
  last_outbound_at timestamptz,
  last_inbound_at timestamptz,
  replied boolean,
  campaign_status text,
  enrichment_complete boolean,
  state text,
  next_action text
)
language sql
stable
security invoker
set search_path = public
as $$
  with input as (
    select
      nullif(btrim(p_linkedin_url), '') as requested_url,
      public.normalize_linkedin_url(p_linkedin_url) as url_key
  ),
  target as (
    select p.id, p.linkedin_url
    from public.prospects p
    join input i on p.linkedin_url_key = i.url_key
    limit 1
  ),
  campaign as (
    select
      coalesce(
        nullif(e.payload ->> 'campaign_status', ''),
        case e.event_type
          when 'LGM_LEAD_ADDED' then 'IN_OUTREACH'
          when 'LGM_CAMPAIGN_FINISHED' then 'FINISHED'
        end
      ) as campaign_status
    from public.events e
    join target t on t.id = e.prospect_id
    where e.event_type in ('LGM_LEAD_ADDED', 'LGM_CAMPAIGN_FINISHED')
    order by e.occurred_at desc, e.created_at desc, e.id desc
    limit 1
  ),
  facts as (
    select
      coalesce(t.linkedin_url, i.requested_url) as linkedin_url,
      t.id as prospect_id,
      coalesce(
        bool_or(e.event_type = 'LINKEDIN_CONNECTION_FOUND')
          filter (where e.id is not null),
        false
      ) as connected,
      max(e.occurred_at)
        filter (where e.event_type = 'LINKEDIN_INVITE_SENT') as invite_sent_at,
      max(e.occurred_at)
        filter (
          where e.event_type in (
            'LINKEDIN_MESSAGE_OUTBOUND',
            'LGM_EMAIL_SENT',
            'LGM_LINKEDIN_STEP'
          )
        ) as last_outbound_at,
      max(e.occurred_at)
        filter (
          where e.event_type in (
            'LINKEDIN_MESSAGE_INBOUND',
            'LGM_REPLY'
          )
        ) as last_inbound_at,
      coalesce(
        bool_or(e.event_type in ('LINKEDIN_MESSAGE_INBOUND', 'LGM_REPLY'))
          filter (where e.id is not null),
        false
      ) as replied,
      coalesce(
        bool_or(e.event_type = 'CLAY_ENRICHED')
          filter (where e.id is not null),
        false
      ) as enrichment_complete,
      (select c.campaign_status from campaign c) as campaign_status
    from input i
    left join target t on true
    left join public.events e on e.prospect_id = t.id
    group by i.requested_url, t.id, t.linkedin_url
  ),
  derived as (
    select
      f.*,
      case
        when f.replied then 'REPLIED'
        when f.connected then 'CONNECTED'
        when upper(coalesce(f.campaign_status, '')) in ('FINISHED', 'COMPLETED')
          then 'NO_RESPONSE'
        when f.campaign_status is not null then 'IN_OUTREACH'
        when f.invite_sent_at is not null then 'INVITE_SENT_NOT_CONNECTED'
        when f.enrichment_complete then 'READY'
        when f.prospect_id is not null then 'NEEDS_ENRICHMENT'
        else 'NEW'
      end as derived_state
    from facts f
  )
  select
    d.linkedin_url,
    d.connected,
    d.invite_sent_at,
    d.last_outbound_at,
    d.last_inbound_at,
    d.replied,
    d.campaign_status,
    d.enrichment_complete,
    d.derived_state as state,
    case d.derived_state
      when 'REPLIED' then 'HUMAN_FOLLOWUP'
      when 'IN_OUTREACH' then 'WAIT'
      when 'INVITE_SENT_NOT_CONNECTED' then 'WAIT'
      when 'NEEDS_ENRICHMENT' then 'ENRICH'
      when 'NEW' then 'ENRICH'
      when 'READY' then 'ELIGIBILITY_CHECK'
      else 'REVIEW'
    end as next_action
  from derived d;
$$;

comment on table public.events is
  'Normalized provider evidence. Keep raw provider payload in payload; derive current outreach state instead of mutating event history.';

comment on function public.outreach_state(text) is
  'Derives current outreach state from normalized evidence. INVITE_SENT_NOT_CONNECTED means an outgoing invitation exists and no current connection evidence exists; it is not authoritative pending/rejected/withdrawn/expired status.';

revoke all on function public.normalize_linkedin_url(text) from public, anon, authenticated;
revoke all on function public.outreach_state(text) from public, anon, authenticated;
grant execute on function public.normalize_linkedin_url(text) to service_role;
grant execute on function public.outreach_state(text) to service_role;
