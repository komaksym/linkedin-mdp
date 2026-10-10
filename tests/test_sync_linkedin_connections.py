'''Tests for the nightly LinkedIn connections sync.'''
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts' / 'sync_linkedin_connections.py'

ROWS = [
    {'URL': 'https://www.linkedin.com/in/ada-example?trk=contact-info',
     'Connected On': '12 Jan 2024',
     'First Name': 'Ada', 'Last Name': 'Example',
     'Email Address': 'ada@example.test'},
    {'URL': 'http://linkedin.com/in/bo-example/',
     'Connected On': '',
     'First Name': 'Bo', 'Last Name': 'Example'},
    {'URL': '',
     'Connected On': '03 Mar 2024',
     'First Name': 'No', 'Last Name': 'Url'},
]

PROSPECTS = [
    {'id': 'p-ada', 'linkedin_url_key': 'https://www.linkedin.com/in/ada-example'},
    {'id': 'p-bo', 'linkedin_url_key': 'https://www.linkedin.com/in/bo-example'},
]

ENV = {
    'LINKEDIN_ACCESS_TOKEN': 'token',
    'SUPABASE_URL': 'https://demo.supabase.co',
    'SUPABASE_SERVICE_ROLE_KEY': 'service-key',
}


def _load():
    spec = importlib.util.spec_from_file_location('sync_mod', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeLinkedIn:
    '''Stand-in for LinkedInClient returning a fixed snapshot.'''

    def __init__(self, rows, truncated=False):
        self._rows = rows
        self._truncated = truncated
        self.domains = []

    async def snapshot(self, domain, max_pages=10):
        self.domains.append((domain, max_pages))
        return {'domain': domain, 'rows': list(self._rows),
                'page_count': 1, 'truncated': self._truncated}

    async def aclose(self):
        pass


class FakeBackend:
    '''In-memory Supabase double with conflict-ignore insert semantics.'''

    def __init__(self):
        self.events = {}
        self.requests = []
        self.inserts = 0

    def transport(self):
        def handler(request):
            self.requests.append((request.method, request.url.path))
            if request.method == 'GET' and request.url.path == '/rest/v1/prospects':
                params = dict(request.url.params)
                offset = int(params.get('offset', '0'))
                limit = int(params.get('limit', '1000'))
                return httpx.Response(200, json=PROSPECTS[offset:offset + limit])
            if request.method == 'GET' and request.url.path == '/rest/v1/events':
                wanted = self._wanted_keys(str(request.url.params.get('external_key', '')))
                found = [{'external_key': key} for key in wanted if key in self.events]
                return httpx.Response(200, json=found)
            if request.method == 'POST' and request.url.path == '/rest/v1/events':
                rows = json.loads(request.content.decode('utf-8'))
                fresh = []
                for row in rows:
                    if row['external_key'] not in self.events:
                        self.events[row['external_key']] = row
                        fresh.append(row)
                self.inserts += len(fresh)
                return httpx.Response(201, json=fresh)
            return httpx.Response(404, json={'message': 'unexpected request'})
        return httpx.MockTransport(handler)

    @staticmethod
    def _wanted_keys(raw):
        if not raw.startswith('in.(') or not raw.endswith(')'):
            return []
        inner = raw[4:-1]
        return inner.split(',') if inner else []


def _set_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def test_plan_counts_match_unmatched_and_event_keys():
    mod = _load()
    by_key = {row['linkedin_url_key']: row['id'] for row in PROSPECTS}
    plan = mod.plan_connection_events(
        ROWS + ['junk'], by_key, '2024-05-01T00:00:00+00:00')
    assert plan['fetched'] == 4
    assert plan['matched'] == 2
    assert plan['unmatched'] == 2
    keys = [event['external_key'] for event in plan['events']]
    assert keys == ['connection:https://www.linkedin.com/in/ada-example',
                    'connection:https://www.linkedin.com/in/bo-example']
    assert plan['events'][0]['prospect_id'] == 'p-ada'
    assert plan['events'][0]['occurred_at'].startswith('2024-01-12')
    assert plan['events'][0]['payload'] == {'timestamp_semantics': 'event_date_day_precision'}
    assert plan['events'][1]['payload'] == {'timestamp_semantics': 'observed_at'}


def test_first_run_inserts_and_prints_counts_only(monkeypatch, capsys):
    mod = _load()
    backend = FakeBackend()
    _set_env(monkeypatch)
    code = mod.main(transport=backend.transport(),
                    linkedin_factory=lambda token: FakeLinkedIn(ROWS))
    assert code == 0
    out = capsys.readouterr().out
    assert out == 'ingest: fetched=3 matched=2 inserted=2 already_present=0\n'
    assert 'Ada' not in out
    assert 'ada-example' not in out
    assert backend.inserts == 2


def test_duplicate_rerun_inserts_zero(monkeypatch, capsys):
    mod = _load()
    backend = FakeBackend()
    transport = backend.transport()
    _set_env(monkeypatch)
    factory = lambda token: FakeLinkedIn(ROWS)  # noqa: E731
    assert mod.main(transport=transport, linkedin_factory=factory) == 0
    capsys.readouterr()
    assert mod.main(transport=transport, linkedin_factory=factory) == 0
    out = capsys.readouterr().out
    assert out == 'ingest: fetched=3 matched=2 inserted=0 already_present=2\n'
    assert backend.inserts == 2


def test_missing_env_fails_closed_without_writes(monkeypatch, capsys):
    mod = _load()
    backend = FakeBackend()
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    code = mod.main(transport=backend.transport(),
                    linkedin_factory=lambda token: FakeLinkedIn(ROWS))
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'LINKEDIN_ACCESS_TOKEN' in captured.err
    assert backend.requests == []


def test_normalize_matches_sql_semantics():
    mod = _load()
    cases = {
        'https://www.linkedin.com/in/ada-example':
            'https://www.linkedin.com/in/ada-example',
        'http://linkedin.com/in/ada-example/':
            'https://www.linkedin.com/in/ada-example',
        'https://uk.linkedin.com/in/ada-example?trk=feed#frag':
            'https://www.linkedin.com/in/ada-example',
        'HTTPS://LINKEDIN.COM/in/ada-example///':
            'https://www.linkedin.com/in/ada-example',
        '  https://www.linkedin.com/in/ada-example/  ':
            'https://www.linkedin.com/in/ada-example',
        '': '',
        None: '',
    }
    for raw, expected in cases.items():
        assert mod.normalize_linkedin_url(raw) == expected
