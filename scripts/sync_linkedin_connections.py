'''Nightly LinkedIn connections ingestion into Supabase events.

Thin runner over the public linkedin-mdp package: fetch the CONNECTIONS
snapshot through LinkedInClient, match rows to prospects by canonical
LinkedIn URL, and record LINKEDIN_CONNECTION_FOUND evidence with
idempotent event keys. Output is aggregate counts only, never PII.
'''
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from linkedin_mdp_mcp.client import LinkedInMDPClient as LinkedInClient
except ImportError:
    LinkedInClient = None

SOURCE = 'LINKEDIN_MDP'
EVENT_TYPE = 'LINKEDIN_CONNECTION_FOUND'
SNAPSHOT_DOMAIN = 'CONNECTIONS'
PAGE_SIZE = 1000
MAX_PAGES = 50
EXISTING_KEY_BATCH = 200
REQUIRED_ENV = (
    'LINKEDIN_ACCESS_TOKEN',
    'SUPABASE_URL',
    'SUPABASE_SERVICE_ROLE_KEY',
)
_CONNECTED_ON_FORMATS = (
    '%d %b %Y',
    '%d %B %Y',
    '%b %d, %Y',
    '%B %d, %Y',
    '%Y-%m-%d',
)


class SyncError(RuntimeError):
    '''Safe failure message; never carries tokens, keys, or row data.'''


def normalize_linkedin_url(value):
    '''Mirror public.normalize_linkedin_url so keys match prospects.'''
    if not isinstance(value, str):
        return ''
    text = re.sub(r'[?#].*$', '', value.strip())
    text = re.sub(
        r'^https?://([A-Za-z0-9-]+\.)?linkedin\.com',
        'https://www.linkedin.com',
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r'/+$', '', text)


def connection_key(canonical_url):
    '''Return the deterministic event key for one observed connection.'''
    return 'connection:' + canonical_url


def parse_connection_date(value, observed_at):
    '''Return (occurred_at, semantics) without raising on bad input.'''
    if isinstance(value, str) and value.strip():
        for fmt in _CONNECTED_ON_FORMATS:
            try:
                day = datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
            occurred = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            return occurred.isoformat(), 'event_date_day_precision'
    return observed_at, 'observed_at'


def plan_connection_events(rows, prospect_by_key, observed_at):
    '''Map snapshot rows to idempotent events plus aggregate counts.'''
    events = []
    seen = set()
    matched = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        canonical = normalize_linkedin_url(row.get('URL'))
        prospect_id = prospect_by_key.get(canonical) if canonical else None
        if not prospect_id:
            continue
        key = connection_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        matched += 1
        occurred_at, semantics = parse_connection_date(
            row.get('Connected On'), observed_at)
        events.append({
            'source': SOURCE,
            'event_type': EVENT_TYPE,
            'prospect_id': prospect_id,
            'external_key': key,
            'occurred_at': occurred_at,
            'payload': {'timestamp_semantics': semantics},
        })
    return {
        'events': events,
        'fetched': len(rows),
        'matched': matched,
        'unmatched': len(rows) - matched,
    }


class SupabaseClient:
    '''Minimal service-role REST client; credentials stay in headers.'''

    def __init__(self, base_url, service_key, transport=None):
        try:
            import httpx
        except ImportError as exc:
            raise SyncError('httpx is not installed') from exc
        self._httpx = httpx
        token = (service_key or '').strip()
        if not token:
            raise SyncError('SUPABASE_SERVICE_ROLE_KEY is empty')
        headers = {
            'apikey': token,
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        self._http = httpx.AsyncClient(
            timeout=30.0, transport=transport, headers=headers)
        self._root = self._checked_root(base_url)

    @staticmethod
    def _checked_root(base_url):
        '''Allow only https hosts so the key never travels elsewhere.'''
        parsed = urlparse((base_url or '').strip())
        if parsed.scheme != 'https' or not parsed.netloc:
            raise SyncError('SUPABASE_URL must be an https URL')
        return parsed.geturl().rstrip('/') + '/'

    async def aclose(self):
        await self._http.aclose()

    async def _get(self, path, params, what):
        try:
            response = await self._http.get(self._root + path, params=params)
        except self._httpx.HTTPError as exc:
            raise SyncError('supabase ' + what + ' failed: network failure') from exc
        if not response.is_success:
            raise SyncError(
                'supabase ' + what + ' failed: status ' + str(response.status_code))
        try:
            payload = response.json()
        except ValueError as exc:
            raise SyncError('supabase ' + what + ' failed: invalid JSON') from exc
        if not isinstance(payload, list):
            raise SyncError('supabase ' + what + ' failed: unexpected shape')
        return payload

    async def prospect_ids_by_linkedin_key(self):
        '''Return {canonical_url: prospect_id} for every prospect row.'''
        mapping = {}
        offset = 0
        while True:
            page = await self._get('rest/v1/prospects', {
                'select': 'id,linkedin_url_key',
                'order': 'created_at.asc',
                'limit': str(PAGE_SIZE),
                'offset': str(offset),
            }, 'prospect lookup')
            for row in page:
                if isinstance(row, dict) and row.get('linkedin_url_key') and row.get('id'):
                    mapping.setdefault(row['linkedin_url_key'], row['id'])
            if len(page) < PAGE_SIZE:
                return mapping
            offset += PAGE_SIZE

    async def existing_event_keys(self, keys):
        '''Return the subset of event keys already stored.'''
        found = set()
        unique = [key for key in dict.fromkeys(keys) if key]
        for start in range(0, len(unique), EXISTING_KEY_BATCH):
            batch = unique[start:start + EXISTING_KEY_BATCH]
            page = await self._get('rest/v1/events', {
                'select': 'external_key',
                'source': 'eq.' + SOURCE,
                'event_type': 'eq.' + EVENT_TYPE,
                'external_key': 'in.(' + ','.join(batch) + ')',
            }, 'event lookup')
            for row in page:
                if isinstance(row, dict) and row.get('external_key'):
                    found.add(row['external_key'])
        return found

    async def insert_events(self, events):
        '''Insert events ignoring duplicates; return the inserted count.'''
        if not events:
            return 0
        try:
            response = await self._http.post(
                self._root + 'rest/v1/events?on_conflict=source,external_key',
                headers={'Prefer': 'resolution=ignore-duplicates,return=representation'},
                json=list(events),
            )
        except self._httpx.HTTPError as exc:
            raise SyncError('supabase event insert failed: network failure') from exc
        if response.status_code not in (200, 201):
            raise SyncError(
                'supabase event insert failed: status ' + str(response.status_code))
        try:
            payload = response.json()
        except ValueError as exc:
            raise SyncError('supabase event insert failed: invalid JSON') from exc
        if not isinstance(payload, list):
            raise SyncError('supabase event insert failed: unexpected shape')
        return len(payload)


async def reconcile_connections(events, supabase):
    '''Insert missing events; return (inserted, already_present).'''
    keys = [event['external_key'] for event in events]
    existing = await supabase.existing_event_keys(keys) if keys else set()
    pending = [event for event in events if event['external_key'] not in existing]
    inserted = await supabase.insert_events(pending)
    return inserted, len(events) - inserted


def _build_linkedin_client(token, factory):
    if factory is not None:
        return factory(token)
    if LinkedInClient is None:
        raise SyncError('linkedin-mdp package is not installed')
    return LinkedInClient(token)


async def run_sync(token, base_url, service_key, transport=None,
                   linkedin_factory=None, observed_at=None):
    '''Execute one sync; return counts. Raise SyncError on any failure.'''
    if observed_at is None:
        observed_at = datetime.now(timezone.utc).isoformat()
    supabase = SupabaseClient(base_url, service_key, transport=transport)
    client = _build_linkedin_client(token, linkedin_factory)
    try:
        try:
            snapshot = await client.snapshot(SNAPSHOT_DOMAIN, max_pages=MAX_PAGES)
        except Exception as exc:
            raise SyncError('linkedin snapshot fetch failed') from exc
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list):
            raise SyncError('linkedin snapshot has an unexpected shape')
        if snapshot.get('truncated'):
            raise SyncError('linkedin snapshot is truncated; refusing partial ingestion')
        rows = snapshot['rows']
        prospect_by_key = await supabase.prospect_ids_by_linkedin_key()
        plan = plan_connection_events(rows, prospect_by_key, observed_at)
        inserted, already_present = await reconcile_connections(plan['events'], supabase)
        return {
            'fetched': plan['fetched'],
            'matched': plan['matched'],
            'inserted': inserted,
            'already_present': already_present,
        }
    finally:
        await supabase.aclose()
        aclose = getattr(client, 'aclose', None)
        if callable(aclose):
            await aclose()


def main(argv=None, transport=None, linkedin_factory=None, env=None):
    '''Entry point: fail closed on missing env; counts to stdout only.'''
    _ = argv
    source = env if env is not None else os.environ
    missing = [name for name in REQUIRED_ENV if not (source.get(name) or '').strip()]
    if missing:
        print('error: missing required environment: ' + ', '.join(missing)
              + ' (set LINKEDIN_ACCESS_TOKEN, SUPABASE_URL,'
              + ' SUPABASE_SERVICE_ROLE_KEY)',
              file=sys.stderr)
        return 2
    try:
        counts = asyncio.run(run_sync(
            source['LINKEDIN_ACCESS_TOKEN'].strip(),
            source['SUPABASE_URL'].strip(),
            source['SUPABASE_SERVICE_ROLE_KEY'].strip(),
            transport=transport,
            linkedin_factory=linkedin_factory,
        ))
    except SyncError as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 1
    print('ingest: fetched=%d matched=%d inserted=%d already_present=%d' % (
        counts['fetched'], counts['matched'],
        counts['inserted'], counts['already_present']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
