drop index if exists public.events_source_external_key_uidx;

create unique index events_source_external_key_uidx
  on public.events (source, external_key);
