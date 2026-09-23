update public.events e
set external_key = 'connection:' || p.linkedin_url_key
from public.prospects p
where e.prospect_id = p.id
  and e.source = 'LINKEDIN_MDP'
  and e.event_type = 'LINKEDIN_CONNECTION_FOUND'
  and e.external_key is distinct from ('connection:' || p.linkedin_url_key);

drop index if exists public.events_source_external_key_uidx;

create unique index events_source_external_key_uidx
  on public.events (source, external_key);
