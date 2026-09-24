\set ON_ERROR_STOP on

insert into public.prospects (linkedin_url, attributes)
values
  ('https://www.linkedin.com/in/db-acceptance-target', '{"fixture":"db_acceptance"}'::jsonb),
  ('https://www.linkedin.com/in/db-acceptance-unrelated', '{"fixture":"db_acceptance"}'::jsonb),
  ('https://www.linkedin.com/in/db-acceptance-legacy-duplicate', '{"fixture":"db_acceptance"}'::jsonb);

insert into public.events (
  prospect_id,
  source,
  event_type,
  external_key,
  occurred_at,
  payload
)
select
  p.id,
  'LINKEDIN_MDP',
  'LINKEDIN_CONNECTION_FOUND',
  null,
  v.occurred_at,
  '{"fixture":"legacy_duplicate"}'::jsonb
from public.prospects p
cross join (
  values
    ('2026-09-20T00:00:00Z'::timestamptz),
    ('2026-09-21T00:00:00Z'::timestamptz),
    ('2026-09-22T00:00:00Z'::timestamptz)
) as v(occurred_at)
where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-legacy-duplicate';

insert into public.events (
  prospect_id,
  source,
  event_type,
  external_key,
  occurred_at,
  payload
)
select
  p.id,
  'TEST_FIXTURE',
  'CLAY_ENRICHED',
  'db-acceptance:unrelated:enriched',
  '2026-09-20T00:00:00Z'::timestamptz,
  '{"fixture":"unrelated_baseline"}'::jsonb
from public.prospects p
where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-unrelated';
