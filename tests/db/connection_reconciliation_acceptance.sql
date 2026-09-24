\set ON_ERROR_STOP on

do $$
declare
  target_connected boolean;
  target_state text;
  unrelated_connected_before boolean;
  unrelated_state_before text;
  unrelated_connected_after boolean;
  unrelated_state_after text;
  legacy_total integer;
  legacy_canonical integer;
  legacy_null integer;
  replay_rows integer;
  index_definition text;
begin
  if exists (
    select 1
    from public.events e
    join public.prospects p on p.id = e.prospect_id
    where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-target'
      and e.event_type = 'LINKEDIN_CONNECTION_FOUND'
  ) then
    raise exception 'target prospect unexpectedly starts with connection evidence';
  end if;

  select connected, state
  into target_connected, target_state
  from public.outreach_state('https://www.linkedin.com/in/db-acceptance-target');

  if target_connected is distinct from false or target_state <> 'NEEDS_ENRICHMENT' then
    raise exception 'unexpected target baseline: connected=%, state=%', target_connected, target_state;
  end if;

  select connected, state
  into unrelated_connected_before, unrelated_state_before
  from public.outreach_state('https://www.linkedin.com/in/db-acceptance-unrelated');

  if unrelated_connected_before is distinct from false or unrelated_state_before <> 'READY' then
    raise exception 'unexpected unrelated baseline: connected=%, state=%',
      unrelated_connected_before, unrelated_state_before;
  end if;

  select
    count(*),
    count(*) filter (
      where e.external_key =
        'connection:https://www.linkedin.com/in/db-acceptance-legacy-duplicate'
    ),
    count(*) filter (where e.external_key is null)
  into legacy_total, legacy_canonical, legacy_null
  from public.events e
  join public.prospects p on p.id = e.prospect_id
  where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-legacy-duplicate'
    and e.source = 'LINKEDIN_MDP'
    and e.event_type = 'LINKEDIN_CONNECTION_FOUND';

  if legacy_total <> 3 or legacy_canonical <> 1 or legacy_null <> 2 then
    raise exception 'legacy duplicate normalization mismatch: total=%, canonical=%, null=%',
      legacy_total, legacy_canonical, legacy_null;
  end if;

  select indexdef
  into index_definition
  from pg_indexes
  where schemaname = 'public'
    and indexname = 'events_source_external_key_uidx';

  if index_definition is null or position(' WHERE ' in upper(index_definition)) <> 0 then
    raise exception 'events_source_external_key_uidx is not an ordinary unique index: %',
      index_definition;
  end if;

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
    'connection:https://www.linkedin.com/in/db-acceptance-target',
    '2026-09-24T00:00:00Z'::timestamptz,
    '{"fixture":"normalized_connection"}'::jsonb
  from public.prospects p
  where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-target'
  on conflict (source, external_key) do nothing;

  select connected, state
  into target_connected, target_state
  from public.outreach_state('https://www.linkedin.com/in/db-acceptance-target');

  if target_connected is distinct from true or target_state <> 'CONNECTED' then
    raise exception 'normalized connection evidence did not derive CONNECTED: connected=%, state=%',
      target_connected, target_state;
  end if;

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
    'connection:https://www.linkedin.com/in/db-acceptance-target',
    '2026-09-24T00:00:00Z'::timestamptz,
    '{"fixture":"normalized_connection"}'::jsonb
  from public.prospects p
  where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-target'
  on conflict (source, external_key) do nothing;

  get diagnostics replay_rows = row_count;

  if replay_rows <> 0 then
    raise exception 'identical source/external_key replay inserted % rows', replay_rows;
  end if;

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
    'LGM_REPLY',
    'db-acceptance:target:reply',
    '2026-09-24T01:00:00Z'::timestamptz,
    '{"fixture":"precedence"}'::jsonb
  from public.prospects p
  where p.linkedin_url_key = 'https://www.linkedin.com/in/db-acceptance-target';

  select connected, state
  into target_connected, target_state
  from public.outreach_state('https://www.linkedin.com/in/db-acceptance-target');

  if target_connected is distinct from true or target_state <> 'REPLIED' then
    raise exception 'existing state precedence was not preserved: connected=%, state=%',
      target_connected, target_state;
  end if;

  select connected, state
  into unrelated_connected_after, unrelated_state_after
  from public.outreach_state('https://www.linkedin.com/in/db-acceptance-unrelated');

  if unrelated_connected_after is distinct from unrelated_connected_before
    or unrelated_state_after is distinct from unrelated_state_before then
    raise exception 'unrelated prospect changed: before=(%, %), after=(%, %)',
      unrelated_connected_before,
      unrelated_state_before,
      unrelated_connected_after,
      unrelated_state_after;
  end if;
end
$$;
