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
      coalesce(
        bool_or(e.event_type = 'LINKEDIN_INVITE_SENT')
          filter (where e.id is not null),
        false
      ) as invite_exists,
      max(e.occurred_at)
        filter (
          where e.event_type = 'LINKEDIN_INVITE_SENT'
            and coalesce(e.payload ->> 'timestamp_semantics', 'actual') <> 'observed_at'
        ) as invite_sent_at,
      max(e.occurred_at)
        filter (
          where e.event_type in (
            'LINKEDIN_MESSAGE_OUTBOUND',
            'LGM_EMAIL_SENT',
            'LGM_LINKEDIN_STEP'
          )
            and coalesce(e.payload ->> 'timestamp_semantics', 'actual') <> 'observed_at'
        ) as last_outbound_at,
      max(e.occurred_at)
        filter (
          where e.event_type in (
            'LINKEDIN_MESSAGE_INBOUND',
            'LGM_REPLY'
          )
            and coalesce(e.payload ->> 'timestamp_semantics', 'actual') <> 'observed_at'
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
        when f.invite_exists then 'INVITE_SENT_NOT_CONNECTED'
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

comment on function public.outreach_state(text) is
  'Derives current outreach state from normalized evidence. Observed-status timestamps can establish evidence without being exposed as exact sent/reply timestamps.';
