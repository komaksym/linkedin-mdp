alter table public.prospects
  drop constraint if exists prospects_linkedin_url_key_not_null;

alter table public.prospects
  alter column linkedin_url drop not null;

alter table public.prospects
  add column lead_id text;

alter table public.prospects
  add constraint prospects_lead_id_not_blank
    check (lead_id is null or btrim(lead_id) <> '');

create unique index prospects_lead_id_uidx
  on public.prospects (lead_id)
  where lead_id is not null;

comment on column public.prospects.lead_id is
  'Stable source lead identifier. During the PVF Outreach CRM migration this is the Google Sheet Lead ID.';

comment on column public.prospects.linkedin_url is
  'Canonical identity when a verified LinkedIn profile URL is available. May be null for unresolved/invalid legacy rows.';
