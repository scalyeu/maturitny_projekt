"""
Offline testy importu z World Athletics (pytest).

Sieť sa nikdy nevolá – world_athletics._http_post/_http_get sa podmenia.
Vzorové dáta sú skutočná odpoveď WA pre Jána Volka (09/2026), aby parser
videl všetky zvláštnosti: '10.13=' (vyrovnaný), nelegálny vietor, Short Track,
štafety, halové '(i)' miesta.
"""
from datetime import date

import pytest
import requests

import world_athletics as wa


# -----------------------------
# Vzorové dáta
# -----------------------------
VOLKO_PBS = [
    {'discipline': '50 Metres', 'mark': '5.79', 'date': '04 FEB 2025', 'venue': 'Atletická hala, Ostrava (CZE) (i)',
     'indoor': False, 'notLegal': False, 'resultScore': 1113, 'wind': None, 'records': ['NR']},
    {'discipline': '60 Metres', 'mark': '6.55', 'date': '21 FEB 2020', 'venue': 'Gallur, Madrid (ESP) (i)',
     'indoor': False, 'notLegal': False, 'resultScore': 1181, 'wind': None, 'records': ['NR']},
    {'discipline': '100 Metres', 'mark': '10.13', 'date': '29 JUN 2018', 'venue': 'x-bionic sphere, Šamorín (SVK)',
     'indoor': False, 'notLegal': False, 'resultScore': 1162, 'wind': '+1.1', 'records': ['NR']},
    {'discipline': '100 Metres', 'mark': '10.13=', 'date': '16 AUG 2022', 'venue': 'Olympiastadion, München (GER)',
     'indoor': False, 'notLegal': False, 'resultScore': 1162, 'wind': '+0.3', 'records': ['=NR']},
    {'discipline': '100 Metres', 'mark': '10.07', 'date': '29 JUN 2018', 'venue': 'x-bionic sphere, Šamorín (SVK)',
     'indoor': False, 'notLegal': True, 'resultScore': 1166, 'wind': '+2.8', 'records': []},
    {'discipline': '200 Metres', 'mark': '20.24', 'date': '08 JUL 2018', 'venue': 'Trnava (SVK)',
     'indoor': False, 'notLegal': False, 'resultScore': 1182, 'wind': '+1.6', 'records': ['NR']},
    {'discipline': '200 Metres Short Track', 'mark': '20.97', 'date': '02 FEB 2023', 'venue': 'Atletická hala, Ostrava (CZE) (i)',
     'indoor': False, 'notLegal': False, 'resultScore': 1138, 'wind': None, 'records': []},
    {'discipline': '400 Metres', 'mark': '49.06', 'date': '09 SEP 2017', 'venue': 'Dubnica nad Váhom (SVK)',
     'indoor': False, 'notLegal': False, 'resultScore': 915, 'wind': None, 'records': []},
    {'discipline': '4x100 Metres Relay', 'mark': '39.05', 'date': '18 AUG 2018', 'venue': 'Zlín (CZE)',
     'indoor': False, 'notLegal': False, 'resultScore': 1146, 'wind': None, 'records': []},
    {'discipline': 'Sprint Medley 1000m', 'mark': '1:58.77', 'date': '08 JUN 2013', 'venue': 'Hradec Králové (CZE)',
     'indoor': False, 'notLegal': False, 'resultScore': 0, 'wind': None, 'records': []},
    # umelé riadky na okrajové prípady
    {'discipline': 'Long Jump', 'mark': '7.66', 'date': '01 MAY 2019', 'venue': 'X', 'notLegal': False, 'wind': '+0.5', 'records': []},
    {'discipline': 'Decathlon', 'mark': '7764', 'date': '01 MAY 2019', 'venue': 'X', 'notLegal': False, 'wind': None, 'records': []},
    {'discipline': '800 Metres', 'mark': 'DNF', 'date': '01 MAY 2019', 'venue': 'X', 'notLegal': False, 'wind': None, 'records': []},
    {'discipline': '1500 Metres', 'mark': '3:41.20', 'date': '2019', 'venue': 'X', 'notLegal': False, 'wind': None, 'records': []},
    {'discipline': 'Marathon', 'mark': '2:05:11', 'date': '03 MAR 2024', 'venue': 'Tokyo (JPN)', 'notLegal': False, 'wind': None, 'records': []},
]

COMPETITOR = {
    'basicData': {'givenName': 'Ján', 'familyName': 'VOLKO', 'birthDate': '02 NOV 1996',
                  'countryCode': 'SVK', 'countryFullName': 'Slovak Republic'},
    'personalBests': {'results': VOLKO_PBS},
}

SEARCH_HITS = [
    {'aaAthleteId': '14511687', 'givenName': 'Ján', 'familyName': 'VOLKO', 'birthDate': '02 NOV 1996',
     'gender': 'Men', 'country': 'SVK', 'disciplines': '200 Metres, 100 Metres, 60 Metres', 'urlSlug': 'jan-volko'},
    {'aaAthleteId': '14346194', 'givenName': 'Ján', 'familyName': 'VOLNÝ', 'birthDate': '1959',
     'gender': 'Men', 'country': 'TCH', 'disciplines': 'High Jump', 'urlSlug': 'jan-volny'},
    {'aaAthleteId': 'abc', 'givenName': 'Zlý', 'familyName': 'ID', 'birthDate': None,
     'gender': 'Men', 'country': 'XXX', 'disciplines': '', 'urlSlug': 'x'},
]


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=''):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.headers = {}

    def json(self):
        if self._json is None:
            raise ValueError('not json')
        return self._json


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Každý test má vlastnú prázdnu cache, aby si testy neposielali odpovede."""
    monkeypatch.setattr(wa, 'CACHE_DIR', str(tmp_path / 'cache'))


def _fake_post(json_data=None, status=200, text='', calls=None):
    def post(url, payload, headers, timeout):
        if calls is not None:
            calls.append({'url': url, 'payload': payload, 'headers': headers, 'timeout': timeout})
        return FakeResponse(status, json_data, text)
    return post


# -----------------------------
# parse_mark_to_seconds
# -----------------------------
@pytest.mark.parametrize('mark, expected', [
    ('10.23', 10.23),
    ('1:45.67', 105.67),
    ('2:05:11', 7511.0),
    ('2:05:11.4', 7511.4),
    ('10.13=', 10.13),
    ('10.4h', 10.4),
    ('1:45.67A', 105.67),
    (' 49.06 ', 49.06),
    ('7764', 7764.0),          # body viacboja – filtruje disciplína, nie parser
    ('DNF', None),
    ('DQ', None),
    ('NM', None),
    ('', None),
    (None, None),
    ('abc', None),
    ('10.2.3', None),
])
def test_parse_mark_to_seconds(mark, expected):
    assert wa.parse_mark_to_seconds(mark) == expected


def test_mark_suffix():
    assert wa.mark_suffix('10.13=') == '='
    assert wa.mark_suffix('10.4h') == 'h'
    assert wa.mark_suffix('1:45.67A') == 'A'
    assert wa.mark_suffix('10.13') == ''
    assert wa.mark_suffix('DNF') == ''


# -----------------------------
# Dátumy
# -----------------------------
def test_parse_wa_date_explicit_month_map():
    assert wa.parse_wa_date('04 SEP 2024') == date(2024, 9, 4)
    assert wa.parse_wa_date('02 nov 1996') == date(1996, 11, 2)
    assert wa.parse_wa_date('1998') is None
    assert wa.parse_wa_date(None) is None
    assert wa.parse_wa_date('31 FEB 2024') is None
    assert wa.parse_wa_date('04 XYZ 2024') is None


def test_format_wa_date():
    assert wa.format_wa_date('02 NOV 1996') == '2. 11. 1996'
    assert wa.format_wa_date('1998') == '1998'
    assert wa.format_wa_date(None) == '—'


def test_parse_wind():
    assert wa.parse_wind('+1.1') == 1.1
    assert wa.parse_wind('-0.8') == -0.8
    assert wa.parse_wind('0.0') == 0.0
    assert wa.parse_wind(None) is None
    assert wa.parse_wind('') is None
    assert wa.parse_wind('n/a') is None


# -----------------------------
# Disciplíny
# -----------------------------
@pytest.mark.parametrize('name, code, indoor', [
    ('100 Metres', '100m', False),
    ('60 Metres', '60m', False),
    ('1500 Metres', '1500m', False),
    ('110 Metres Hurdles', '110mH', False),
    ('400 Metres Hurdles', '400mH', False),
    ('3000 Metres Steeplechase', '3000mSC', False),
    ('200 Metres Short Track', '200m (hala)', True),
    ('60 Metres Hurdles Short Track', '60mH (hala)', True),
    ('10 Kilometres', '10km', False),
    ('20 Kilometres Race Walk', '20km chôdza', False),
    ('Marathon', 'Maratón', False),
    ('Half Marathon', 'Polmaratón', False),
    ('One Mile', 'Míľa', False),
    ('Long Jump', 'Long Jump', False),
    ('Sprint Medley 1000m', 'Sprint Medley 1000m', False),
])
def test_normalise_discipline(name, code, indoor):
    assert wa.normalise_discipline(name) == (code, indoor)


def test_is_running_discipline():
    assert wa.is_running_discipline('100 Metres')
    assert wa.is_running_discipline('400 Metres Hurdles')
    assert wa.is_running_discipline('Marathon')
    assert wa.is_running_discipline('20 Kilometres Race Walk')
    assert not wa.is_running_discipline('Long Jump')
    assert not wa.is_running_discipline('Shot Put')
    assert not wa.is_running_discipline('Pole Vault')
    assert not wa.is_running_discipline('Decathlon')
    assert not wa.is_running_discipline('4x100 Metres Relay')
    assert not wa.is_running_discipline('Sprint Medley 1000m')
    assert not wa.is_running_discipline('')


# -----------------------------
# extract_wa_id
# -----------------------------
@pytest.mark.parametrize('text, expected', [
    ('14511687', '14511687'),
    ('  14511687 ', '14511687'),
    ('https://worldathletics.org/athletes/slovakia/jan-volko-14511687', '14511687'),
    ('worldathletics.org/athletes/_/_-14511687', '14511687'),
    ('https://worldathletics.org/athletes/sweden/armand-duplantis-14679502?x=1#top', '14679502'),
    ('Ján Volko', None),
    ('', None),
    (None, None),
    ('1234567890123', None),                       # 13 číslic – nad limit
    ('https://evil.example/athletes/x-14511687', None),
    ('14511687; DROP TABLE', None),
])
def test_extract_wa_id(text, expected):
    assert wa.extract_wa_id(text) == expected


# -----------------------------
# parse_personal_bests
# -----------------------------
def test_parse_personal_bests_filters_and_normalises():
    rows, skipped = wa.parse_personal_bests(VOLKO_PBS)
    by = {(r['discipline'], r['mark_str']): r for r in rows}

    # bežecké s platným časom
    assert ('100m', '10.13') in by and by[('100m', '10.13')]['result_s'] == 10.13
    assert by[('100m', '10.13')]['date'] == date(2018, 6, 29)
    assert by[('100m', '10.13')]['wind'] == 1.1
    assert by[('100m', '10.13')]['venue'] == 'x-bionic sphere, Šamorín (SVK)'
    assert 'NR' in by[('100m', '10.13')]['note']
    # vyrovnaný rekord ostáva ako ďalší výsledok, prípona ide do poznámky
    assert by[('100m', '10.13=')]['result_s'] == 10.13
    assert 'vyrovnaný' in by[('100m', '10.13=')]['note']
    assert '10.13=' in by[('100m', '10.13=')]['note']
    # Short Track = hala
    assert by[('200m (hala)', '20.97')]['indoor'] is True
    # '(i)' v mieste = hala aj bez Short Track
    assert by[('60m', '6.55')]['indoor'] is True and 'hala' in by[('60m', '6.55')]['note']
    assert by[('100m', '10.13')]['indoor'] is False
    assert by[('Maratón', '2:05:11')]['result_s'] == 7511.0

    # preskočené
    reasons = {(s['discipline'], s['mark_str']): s['reason'] for s in skipped}
    assert ('100 Metres', '10.07') in reasons and 'vietor' in reasons[('100 Metres', '10.07')]
    assert ('4x100 Metres Relay', '39.05') in reasons
    assert ('Sprint Medley 1000m', '1:58.77') in reasons
    assert ('Long Jump', '7.66') in reasons
    assert ('Decathlon', '7764') in reasons
    assert ('800 Metres', 'DNF') in reasons and 'čas' in reasons[('800 Metres', 'DNF')]
    assert ('1500 Metres', '3:41.20') in reasons and 'dátum' in reasons[('1500 Metres', '3:41.20')]

    assert len(rows) == 8
    assert len(rows) + len(skipped) == len(VOLKO_PBS)
    # nič nelegálne neprešlo
    assert all(r['result_s'] > 0 for r in rows)


def test_parse_personal_bests_tolerates_garbage():
    rows, skipped = wa.parse_personal_bests(None)
    assert rows == [] and skipped == []
    rows, skipped = wa.parse_personal_bests([None, 'x', {}, {'discipline': None, 'mark': None}])
    assert rows == []


# -----------------------------
# search_athletes / fetch_* s podmeneným HTTP
# -----------------------------
def test_search_athletes_offline(monkeypatch):
    calls = []
    monkeypatch.setattr(wa, '_http_post', _fake_post({'data': {'searchCompetitors': SEARCH_HITS}}, calls=calls))
    hits = wa.search_athletes('Ján Volko')

    assert calls[0]['url'] == wa.DEFAULT_ENDPOINT
    assert calls[0]['headers']['x-api-key'] == wa.DEFAULT_API_KEY
    assert calls[0]['payload']['variables'] == {'query': 'Ján Volko'}
    assert calls[0]['timeout'] == wa.TIMEOUT

    assert [h['wa_id'] for h in hits] == ['14511687', '14346194']   # nečíselné id sa vyhodí
    assert hits[0]['name'] == 'Ján VOLKO'
    assert hits[0]['country'] == 'SVK'
    assert hits[0]['birth_date'] == date(1996, 11, 2)
    assert hits[0]['birth_date_text'] == '2. 11. 1996'
    assert hits[0]['disciplines'] == '200m, 100m, 60m'
    assert hits[1]['birth_date_text'] == '1959'

    # druhé volanie ide z cache – HTTP sa už nevolá
    wa.search_athletes('ján volko')
    assert len(calls) == 1


def test_search_athletes_short_query():
    with pytest.raises(wa.WAError):
        wa.search_athletes('a')


def test_fetch_personal_bests_offline(monkeypatch):
    calls = []
    monkeypatch.setattr(wa, '_http_post', _fake_post({'data': {'getSingleCompetitor': COMPETITOR}}, calls=calls))
    pbs = wa.fetch_personal_bests('14511687')
    assert calls[0]['payload']['variables'] == {'id': 14511687}
    assert len(pbs) == 8
    assert {'discipline', 'mark_str', 'result_s', 'date', 'venue', 'wind', 'indoor'} <= set(pbs[0])

    profile = wa.fetch_profile('14511687')
    assert profile['name'] == 'Ján VOLKO'
    assert profile['country'] == 'SVK'
    assert profile['birth_date_text'] == '2. 11. 1996'
    assert profile['profile_url'] == 'https://worldathletics.org/athletes/_/_-14511687'
    assert len(calls) == 1   # cache


def test_fetch_rejects_bad_id_before_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('sieť sa nemala volať')
    monkeypatch.setattr(wa, '_http_post', boom)
    monkeypatch.setattr(wa, '_http_get', boom)
    for bad in ('abc', '', None, '1; DROP', '1234567890123', 'https://worldathletics.org/athletes/x/y-1'[:-2]):
        with pytest.raises(wa.WAError):
            wa.fetch_personal_bests(bad)


def test_unknown_id_is_not_found(monkeypatch):
    monkeypatch.setattr(wa, '_http_post', _fake_post({'data': {'getSingleCompetitor': None}}))
    monkeypatch.setattr(wa, '_http_get', lambda *a, **k: FakeResponse(404, None, ''))
    with pytest.raises(wa.WAError) as e:
        wa.fetch_personal_bests('1')
    assert str(e.value) == wa.MSG_NOT_FOUND


def test_network_error_is_slovak(monkeypatch):
    def post(*a, **k):
        raise requests.ConnectionError('boom')
    def get(*a, **k):
        raise requests.Timeout('boom')
    monkeypatch.setattr(wa, '_http_post', post)
    monkeypatch.setattr(wa, '_http_get', get)
    with pytest.raises(wa.WAError) as e:
        wa.search_athletes('Volko')
    assert 'World Athletics' in str(e.value) and 'ručne' in str(e.value)
    with pytest.raises(wa.WAError):
        wa.fetch_personal_bests('14511687')


def test_graphql_errors_surface_message(monkeypatch):
    monkeypatch.setattr(wa, '_http_post', _fake_post({'errors': [{'message': "Field 'x' undefined"}], 'data': None}))
    monkeypatch.setattr(wa, '_http_get', lambda *a, **k: FakeResponse(404, None, ''))
    with pytest.raises(wa.WAError) as e:
        wa.search_athletes('Volko')
    assert 'nečakanú odpoveď' in str(e.value)


def test_503_rediscovers_key_and_retries(monkeypatch):
    """Zlý kľúč → 503 HTML; modul nájde nový kľúč v bundli a zopakuje dotaz s ním."""
    posts = []
    def post(url, payload, headers, timeout):
        posts.append(headers['x-api-key'])
        if headers['x-api-key'] == 'da2-newkeynewkeynewkeynewkey12':
            return FakeResponse(200, {'data': {'searchCompetitors': SEARCH_HITS}})
        return FakeResponse(503, None, '<html>503</html>')
    def get(url, timeout):
        if url.endswith('/athletes'):
            return FakeResponse(200, None, '<script src="/_next/static/chunks/abc.js"></script>')
        return FakeResponse(200, None, 'x;graphql:{endpoint:"https://new.edge.aws.worldathletics.org/graphql",region:"eu",apiKey:"da2-newkeynewkeynewkeynewkey12"};')
    monkeypatch.setattr(wa, '_http_post', post)
    monkeypatch.setattr(wa, '_http_get', get)

    hits = wa.search_athletes('Volko')
    assert len(hits) == 2
    assert posts == [wa.DEFAULT_API_KEY, 'da2-newkeynewkeynewkeynewkey12']
    # nový kľúč sa uložil do cache a použije sa hneď pri ďalšom dotaze
    assert wa._credentials() == ('https://new.edge.aws.worldathletics.org/graphql', 'da2-newkeynewkeynewkeynewkey12')


def test_503_without_new_key_fails_in_slovak(monkeypatch):
    monkeypatch.setattr(wa, '_http_post', _fake_post(None, status=503, text='<html>'))
    monkeypatch.setattr(wa, '_http_get', lambda url, timeout: FakeResponse(200, None, '<html>bez kľúča</html>'))
    with pytest.raises(wa.WAError) as e:
        wa.search_athletes('Volko')
    assert str(e.value) == wa.MSG_KEY


def test_html_fallback_when_graphql_down(monkeypatch):
    """GraphQL nedostupné, ale profilová stránka s __NEXT_DATA__ funguje."""
    import json
    def post(*a, **k):
        raise requests.ConnectionError('down')
    def get(url, timeout):
        assert url == 'https://worldathletics.org/athletes/_/_-14511687'
        assert timeout == wa.HTML_TIMEOUT
        page = json.dumps({'props': {'pageProps': {'competitor': COMPETITOR}}})
        return FakeResponse(200, None, f'<html><script id="__NEXT_DATA__" type="application/json">{page}</script></html>')
    monkeypatch.setattr(wa, '_http_post', post)
    monkeypatch.setattr(wa, '_http_get', get)
    pbs = wa.fetch_personal_bests('14511687')
    assert len(pbs) == 8


def test_cache_expires(monkeypatch, tmp_path):
    import os, time
    calls = []
    monkeypatch.setattr(wa, '_http_post', _fake_post({'data': {'getSingleCompetitor': COMPETITOR}}, calls=calls))
    wa.fetch_personal_bests('14511687')
    path = wa._cache_path('competitor_14511687')
    assert os.path.exists(path)
    old = time.time() - wa.CACHE_TTL_S - 10
    os.utime(path, (old, old))
    wa.fetch_personal_bests('14511687')
    assert len(calls) == 2
    # use_cache=False (obnovenie) obíde cache vždy
    wa.fetch_personal_bests('14511687', use_cache=False)
    assert len(calls) == 3
