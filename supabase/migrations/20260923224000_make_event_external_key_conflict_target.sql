with ranked_connection_events as (
  select
    e.id,
    'connection:' || p.linkedin_url_key as canonical_external_key,
    row_number() over (
      partition by e.source, ('connection:' || p.linkedin_url_key)
      order by
        case
          when e.external_key = ('connection:' || p.linkedin_url_key) then 0
          else 1
        end,
        e.occurred_at,
        e.created_at,
        e.id
    ) as canonical_rank
  from public.events e
  join public.prospects p on p.id = e.prospect_id
  where e.source = 'LINKEDIN_MDP'
    and e.event_type = 'LINKEDIN_CONNECTION_FOUND'
    and p.linkedin_url_key is not null
)
update public.events e
set external_key = case
  when ranked.canonical_rank = 1 then ranked.canonical_external_key
  else null
end
from ranked_connection_events ranked
where e.id = ranked.id
  and e.external_key is distinct from (
    case
      when ranked.canonical_rank = 1 then ranked.canonical_external_key
      else null
    end
  );

drop index if exists public.events_source_external_key_uidx;

create unique index events_source_external_key_uidx
  on public.events (source, external_key);
