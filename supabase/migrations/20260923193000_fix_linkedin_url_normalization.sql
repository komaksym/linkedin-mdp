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
        '^https?://([[:alnum:]-]+\.)?linkedin\.com',
        'https://www.linkedin.com',
        'i'
      ),
      '/+$',
      ''
    ),
    ''
  );
$$;

comment on function public.normalize_linkedin_url(text) is
  'Canonicalizes LinkedIn profile URLs for identity matching: canonical host, no query/fragment, no trailing slash.';
