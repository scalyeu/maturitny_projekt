"""
Blueprint 'wa' – import osobných rekordov zo stránky World Athletics.

Servisná časť (bez Flasku) sa dá použiť aj samostatne a testovať offline:
  - search_athletes(query)          meno → zoznam kandidátov s WA id
  - fetch_personal_bests(wa_id)     WA id → osobné rekordy v bežeckých disciplínach
  - parse_mark_to_seconds(mark)     '1:45.67' → 105.67, 'DNF' → None
  - normalise_discipline(name)      '400 Metres Hurdles' → '400mH'
  - extract_wa_id(text)             URL profilu alebo číslo → '14511687'

World Athletics nemá verejné API. Stránka ale volá GraphQL endpoint (AWS AppSync)
s kľúčom, ktorý posiela každému návštevníkovi v JS bundli – ten istý používame
aj tu. Kľúč sa občas zmení, preto sa pri HTTP 503 raz znova vyhľadá v bundli.
Ak GraphQL zlyhá úplne, profil sa prečíta z HTML stránky (<script id="__NEXT_DATA__">).
Každé zlyhanie siete skončí ako WAError so slovenskou správou – stránky nikdy nepadnú.
"""
import os
import re
import json
import time
import hashlib
from datetime import date

import requests
from flask import Blueprint, render_template, redirect, url_for, flash, request, g

from models import db, User, RaceResult
from auth import roles_required, require_visible_athlete

bp = Blueprint('wa', __name__, url_prefix='/wa')

# -----------------------------
# Konštanty
# -----------------------------
WA_BASE = 'https://worldathletics.org'
# Verejné hodnoty z JS bundlu worldathletics.org (overené 09/2026). Nie sú tajné,
# slúžia ako záloha, kým sa nepodarí nájsť aktuálne v bundli.
DEFAULT_ENDPOINT = 'https://graphql-prod-4895.edge.aws.worldathletics.org/graphql'
DEFAULT_API_KEY = 'da2-q7toieeiobcjxbov4abq3abk5u'
# Profil sa dá otvoriť s ľubovoľnou krajinou/slugom – rozhoduje len číslo na konci.
PROFILE_URL = WA_BASE + '/athletes/_/_-{wa_id}'

TIMEOUT = 15
HTML_TIMEOUT = 10
USER_AGENT = 'AtletCoach/1.0 (maturitny projekt; import osobnych rekordov; python-requests)'
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
CACHE_TTL_S = 24 * 3600
MAX_SEARCH_HITS = 15

WA_ID_RE = re.compile(r'^\d{1,12}$')

SEARCH_QUERY = '''query SearchCompetitors($query: String) {
  searchCompetitors(query: $query) {
    aaAthleteId givenName familyName birthDate gender country disciplines urlSlug
  }
}'''

COMPETITOR_QUERY = '''query GetSingleCompetitor($id: Int) {
  getSingleCompetitor(id: $id) {
    basicData { givenName familyName birthDate countryCode countryFullName }
    personalBests {
      results { discipline mark date venue indoor notLegal resultScore wind records }
    }
  }
}'''

# Anglické mesiace z WA dátumov ('04 SEP 2024'). strptime('%b') závisí od locale,
# na slovenskom systéme by 'SEP' nespoznal.
MONTHS = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}

MSG_NETWORK = 'World Athletics je nedostupné (sieť alebo časový limit) – skús neskôr alebo zadaj rekordy ručne.'
MSG_KEY = 'World Athletics zmenilo prístupový kľúč – import teraz nefunguje, zadaj rekordy ručne.'
MSG_NOT_FOUND = 'Atlét s týmto ID sa na World Athletics nenašiel – skontroluj URL profilu.'
MSG_SCHEMA = 'World Athletics vrátilo nečakanú odpoveď (zmena štruktúry dát) – import teraz nefunguje.'
MSG_BAD_ID = 'Neplatné World Athletics ID – zadaj číslo alebo URL profilu (worldathletics.org/athletes/…).'


class WAError(Exception):
    """Chyba pri komunikácii s World Athletics; text je určený používateľovi (slovensky)."""


# -----------------------------
# Cache (cache/wa_*.json, 24 h)
# -----------------------------
def _cache_path(key):
    return os.path.join(CACHE_DIR, f'wa_{key}.json')


def _cache_get(key, max_age=CACHE_TTL_S):
    path = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(path) > max_age:
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cache_set(key, data):
    # Cache je len zrýchlenie – keď sa nedá zapísať, import funguje ďalej.
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


# -----------------------------
# HTTP (jediné miesto, ktoré volá requests – testy ho podmenia)
# -----------------------------
def _http_get(url, timeout):
    return requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout, allow_redirects=False)


def _http_post(url, payload, headers, timeout):
    return requests.post(url, json=payload, headers=headers, timeout=timeout, allow_redirects=False)


def discover_credentials():
    """
    Nájde GraphQL endpoint a x-api-key v JS bundli stránky /athletes.
    Názov chunku sa mení pri každom nasadení, preto sa prehľadajú všetky.
    """
    try:
        html = _http_get(WA_BASE + '/athletes', TIMEOUT).text
        chunks = sorted(set(re.findall(r'/_next/static/chunks/[^"]+\.js', html)))
        for path in chunks:
            js = _http_get(WA_BASE + path, TIMEOUT).text
            m = re.search(r'graphql:\{endpoint:"(https://[^"]+)"[^}]*?apiKey:"(da2-[a-z0-9]{26})"', js)
            if m:
                creds = {'endpoint': m.group(1), 'api_key': m.group(2)}
                _cache_set('credentials', creds)
                return creds
    except requests.RequestException:
        pass
    return None


def _credentials():
    cached = _cache_get('credentials', max_age=7 * 24 * 3600)
    if cached and cached.get('endpoint', '').startswith('https://') and cached.get('api_key'):
        return cached['endpoint'], cached['api_key']
    return DEFAULT_ENDPOINT, DEFAULT_API_KEY


def _gql(query, variables, _retry=True):
    """POST GraphQL dotaz; pri 503 (zlý kľúč) raz znova nájde kľúč a zopakuje."""
    endpoint, api_key = _credentials()
    headers = {'User-Agent': USER_AGENT, 'Content-Type': 'application/json', 'x-api-key': api_key}
    try:
        resp = _http_post(endpoint, {'query': query, 'variables': variables}, headers, TIMEOUT)
    except requests.RequestException:
        raise WAError(MSG_NETWORK)

    if resp.status_code == 503:
        # Presne toto vráti CloudFront pri neplatnom kľúči – HTML stránka, nie JSON.
        if _retry and discover_credentials():
            return _gql(query, variables, _retry=False)
        raise WAError(MSG_KEY)
    if resp.status_code != 200:
        raise WAError(f'{MSG_NETWORK} (HTTP {resp.status_code})')

    try:
        data = resp.json()
    except ValueError:
        raise WAError(MSG_SCHEMA)
    if not isinstance(data, dict):
        raise WAError(MSG_SCHEMA)
    if data.get('errors'):
        detail = (data['errors'][0] or {}).get('message', '')
        raise WAError(f'{MSG_SCHEMA} Podrobnosť: {detail[:120]}')
    if 'data' not in data:
        raise WAError(MSG_SCHEMA)
    return data['data']


# -----------------------------
# Parsovanie
# -----------------------------
def extract_wa_id(text):
    """
    Číslo alebo URL profilu → WA id (string číslic), inak None.
      '14511687'                                       → '14511687'
      'https://worldathletics.org/athletes/slovakia/jan-volko-14511687' → '14511687'
    Nikdy nevráti nič iné než ^\\d{1,12}$ – hodnota ide do URL/požiadavky.
    """
    if not text:
        return None
    text = text.strip()
    if WA_ID_RE.match(text):
        return text
    m = re.search(r'worldathletics\.org/athletes/[^\s?#]*?-(\d{1,12})(?:[/?#]|$)', text)
    if m:
        return m.group(1)
    return None


def parse_mark_to_seconds(mark):
    """
    Výkon ako text → sekundy.
      '10.23' → 10.23   '1:45.67' → 105.67   '2:05:11' → 7511.0
      '10.13=' (vyrovnaný), '10.4h' (ručný čas), '1:45.67A' (nadm. výška) → číslo bez prípony
      'DNF', 'DQ', 'NM', '' → None
    """
    if mark is None:
        return None
    m = re.match(r'^(\d+(?::\d{1,2}){0,2}(?:\.\d+)?)\s*([A-Za-z=*+#]*)$', str(mark).strip())
    if not m:
        return None
    total = 0.0
    for part in m.group(1).split(':'):
        total = total * 60 + float(part)
    return round(total, 2)


def mark_suffix(mark):
    """Prípona za číslom ('=', 'A', 'h', …) alebo ''."""
    if mark is None:
        return ''
    m = re.match(r'^\d+(?::\d{1,2}){0,2}(?:\.\d+)?\s*([A-Za-z=*+#]*)$', str(mark).strip())
    return m.group(1) if m else ''


def parse_wa_date(text):
    """'04 SEP 2024' → date(2024, 9, 4); '1998', None alebo nezmysel → None."""
    if not text:
        return None
    m = re.match(r'^\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*$', str(text))
    if not m:
        return None
    month = MONTHS.get(m.group(2).upper())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def format_wa_date(text):
    """Dátum narodenia na zobrazenie: '02 NOV 1996' → '2. 11. 1996', '1998' ostane, None → '—'."""
    d = parse_wa_date(text)
    if d:
        return f'{d.day}. {d.month}. {d.year}'
    return text or '—'


def parse_wind(text):
    """'+1.1' → 1.1, '-0.8' → -0.8, None/'' → None."""
    if text in (None, ''):
        return None
    try:
        return float(str(text).replace(',', '.'))
    except ValueError:
        return None


_NOT_RUNNING_RE = re.compile(r'jump|throw|put|vault|athlon|relay|medley|shot|discus|hammer|javelin|combined',
                             re.IGNORECASE)
_RUNNING_RE = re.compile(r'metres|kilometres|marathon|mile|steeplechase|hurdles|walk|\d+m\b', re.IGNORECASE)


def is_running_discipline(name):
    """Len časové individuálne disciplíny – skoky/vrhy/viacboje/štafety idú bokom."""
    if not name:
        return False
    if _NOT_RUNNING_RE.search(name):
        return False
    return bool(_RUNNING_RE.search(name))


def normalise_discipline(name):
    """
    Názov WA → kód disciplíny v aplikácii; neznáme názvy ostávajú ako sú.
      '100 Metres' → '100m'   '400 Metres Hurdles' → '400mH'   '3000 Metres Steeplechase' → '3000mSC'
      '200 Metres Short Track' → '200m (hala)'   'Marathon' → 'Maratón'   '10 Kilometres' → '10km'
    Vracia (kód, indoor) – 'Short Track' je halová verzia disciplíny.
    """
    if not name:
        return name, False
    raw = ' '.join(str(name).split())
    indoor = False
    base = raw
    if base.lower().endswith(' short track'):
        base = base[:-len(' short track')].strip()
        indoor = True

    code = None
    m = re.match(r'^(\d+) Metres$', base, re.IGNORECASE)
    if m:
        code = f'{m.group(1)}m'
    m = m or re.match(r'^(\d+) Metres Hurdles$', base, re.IGNORECASE)
    if code is None and m:
        code = f'{m.group(1)}mH'
    m = m or re.match(r'^(\d+) Metres Steeplechase$', base, re.IGNORECASE)
    if code is None and m:
        code = f'{m.group(1)}mSC'
    m = m or re.match(r'^(\d+) Kilometres$', base, re.IGNORECASE)
    if code is None and m:
        code = f'{m.group(1)}km'
    m = m or re.match(r'^(\d+) (Kilometres|Metres) (Race )?Walk$', base, re.IGNORECASE)
    if code is None and m:
        unit = 'km' if m.group(2).lower().startswith('kilo') else 'm'
        code = f'{m.group(1)}{unit} chôdza'
    if code is None:
        fixed = {'marathon': 'Maratón', 'half marathon': 'Polmaratón', 'one mile': 'Míľa',
                 'mile': 'Míľa', 'one hour': '1 hodina'}
        code = fixed.get(base.lower())
    if code is None:
        code = raw
        indoor = False   # neznámy názov nechávame celý, aj so 'Short Track'
    elif indoor:
        code = f'{code} (hala)'
    return code[:40], indoor


def parse_personal_bests(results):
    """
    Surové riadky personalBests.results z WA → (rekordy, preskočené).
    Rekord: {discipline, mark_str, result_s, date, venue, wind, indoor, note, records}
    Preskočený: {discipline, mark_str, reason}
    """
    rows, skipped = [], []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        disc_raw = r.get('discipline') or ''
        mark = r.get('mark') or ''
        venue = r.get('venue') or ''

        if not is_running_discipline(disc_raw):
            skipped.append({'discipline': disc_raw, 'mark_str': mark, 'reason': 'nie je bežecká disciplína (skok, vrh, viacboj alebo štafeta)'})
            continue
        if r.get('notLegal'):
            # Nelegálny vietor by sa stal „osobným rekordom“ (PB = min času) – radšej vynechať.
            skipped.append({'discipline': disc_raw, 'mark_str': mark,
                            'reason': f"nelegálny vietor ({r.get('wind') or '?'} m/s)"})
            continue
        seconds = parse_mark_to_seconds(mark)
        if seconds is None or seconds <= 0:
            skipped.append({'discipline': disc_raw, 'mark_str': mark, 'reason': 'výkon sa nedá prečítať ako čas'})
            continue
        d = parse_wa_date(r.get('date'))
        if d is None:
            skipped.append({'discipline': disc_raw, 'mark_str': mark, 'reason': 'chýba úplný dátum'})
            continue

        code, indoor = normalise_discipline(disc_raw)
        indoor = indoor or '(i)' in venue
        suffix = mark_suffix(mark)
        records = [x for x in (r.get('records') or []) if x]

        note_parts = [f'World Athletics: {disc_raw} {mark}']
        if '=' in suffix:
            note_parts.append('vyrovnaný rekord')
        if 'h' in suffix:
            note_parts.append('ručný čas')
        if 'A' in suffix:
            note_parts.append('nadmorská výška')
        if indoor and '(hala)' not in code:
            note_parts.append('hala')
        if records:
            note_parts.append(', '.join(records))

        rows.append({
            'discipline': code,
            'discipline_wa': disc_raw,
            'mark_str': mark,
            'result_s': seconds,
            'date': d,
            'venue': venue[:160],
            'wind': parse_wind(r.get('wind')),
            'indoor': indoor,
            'records': records,
            'note': ' · '.join(note_parts),
        })
    return rows, skipped


# -----------------------------
# Servisné funkcie
# -----------------------------
def search_athletes(query, use_cache=True):
    """Meno → zoznam {wa_id, name, country, birth_date, birth_date_text, disciplines}. Fuzzy, bez stránkovania."""
    query = ' '.join((query or '').split())
    if len(query) < 2:
        raise WAError('Zadaj aspoň dva znaky mena.')

    key = 'search_' + hashlib.sha1(query.lower().encode('utf-8')).hexdigest()[:16]
    data = _cache_get(key) if use_cache else None
    if data is None:
        data = _gql(SEARCH_QUERY, {'query': query})
        _cache_set(key, data)

    hits = (data or {}).get('searchCompetitors')
    if hits is None:
        raise WAError(MSG_SCHEMA)

    out = []
    for h in hits[:MAX_SEARCH_HITS]:
        wa_id = str(h.get('aaAthleteId') or '')
        if not WA_ID_RE.match(wa_id):
            continue
        name = ' '.join(x for x in (h.get('givenName'), h.get('familyName')) if x)
        discs = [normalise_discipline(x.strip())[0] for x in (h.get('disciplines') or '').split(',') if x.strip()]
        out.append({
            'wa_id': wa_id,
            'name': name or '—',
            'country': h.get('country') or '—',
            'birth_date': parse_wa_date(h.get('birthDate')),
            'birth_date_text': format_wa_date(h.get('birthDate')),
            'disciplines': ', '.join(discs) or '—',
        })
    return out


def _competitor_from_html(wa_id):
    """Záloha bez kľúča: profilová stránka nesie PB v <script id="__NEXT_DATA__">."""
    try:
        resp = _http_get(PROFILE_URL.format(wa_id=wa_id), HTML_TIMEOUT)
    except requests.RequestException:
        raise WAError(MSG_NETWORK)
    if resp.status_code in (404, 500):
        raise WAError(MSG_NOT_FOUND)
    if resp.status_code != 200:
        raise WAError(f'{MSG_NETWORK} (HTTP {resp.status_code})')
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.S)
    if not m:
        raise WAError(MSG_SCHEMA)
    try:
        comp = json.loads(m.group(1))['props']['pageProps']['competitor']
    except (ValueError, KeyError, TypeError):
        raise WAError(MSG_SCHEMA)
    if not comp:
        raise WAError(MSG_NOT_FOUND)
    return comp


def fetch_competitor(wa_id, use_cache=True):
    """Surový profil (basicData + personalBests) – GraphQL, pri zlyhaní HTML záloha."""
    wa_id = str(wa_id or '').strip()
    if not WA_ID_RE.match(wa_id):
        raise WAError(MSG_BAD_ID)

    key = f'competitor_{wa_id}'
    comp = _cache_get(key) if use_cache else None
    if comp is None:
        try:
            data = _gql(COMPETITOR_QUERY, {'id': int(wa_id)})
            comp = (data or {}).get('getSingleCompetitor')
            if comp is None:
                # WA vráti null pre neznáme id – to nie je sieťová chyba, záloha nepomôže.
                raise WAError(MSG_NOT_FOUND)
        except WAError as e:
            if str(e) == MSG_NOT_FOUND:
                raise
            try:
                comp = _competitor_from_html(wa_id)
            except WAError:
                raise e
        _cache_set(key, comp)
    return comp


def fetch_profile(wa_id, use_cache=True):
    """{'wa_id', 'name', 'country', 'birth_date_text', 'pbs': [...], 'skipped': [...]}"""
    comp = fetch_competitor(wa_id, use_cache=use_cache)
    basic = comp.get('basicData') or {}
    results = ((comp.get('personalBests') or {}).get('results')) or []
    pbs, skipped = parse_personal_bests(results)
    return {
        'wa_id': str(wa_id).strip(),
        'name': ' '.join(x for x in (basic.get('givenName'), basic.get('familyName')) if x) or '—',
        'country': basic.get('countryCode') or '—',
        'country_full': basic.get('countryFullName') or '',
        'birth_date_text': format_wa_date(basic.get('birthDate')),
        'profile_url': PROFILE_URL.format(wa_id=str(wa_id).strip()),
        'pbs': pbs,
        'skipped': skipped,
    }


def fetch_personal_bests(wa_id, use_cache=True):
    """WA id → osobné rekordy v bežeckých disciplínach (list dict, pozri parse_personal_bests)."""
    return fetch_profile(wa_id, use_cache=use_cache)['pbs']


# -----------------------------
# Práca s databázou
# -----------------------------
def _result_key(discipline, d, seconds):
    return (discipline, d, round(float(seconds), 2))


def _existing_keys(athlete_id):
    """Množina (disciplína, dátum, čas) už uložených výsledkov – na odhalenie duplicít."""
    rows = db.session.query(RaceResult.discipline, RaceResult.date, RaceResult.result_s) \
                     .filter(RaceResult.user_id == athlete_id).all()
    return {_result_key(r[0], r[1], r[2]) for r in rows}


def _mark_existing(pbs, existing):
    for row in pbs:
        row['exists'] = _result_key(row['discipline'], row['date'], row['result_s']) in existing
    return pbs


def _import_pbs(athlete, profile):
    """Uloží nové rekordy ako RaceResult(source='world_athletics'); vráti počet nových."""
    existing = _existing_keys(athlete.id)
    new_count = 0
    for row in profile['pbs']:
        key = _result_key(row['discipline'], row['date'], row['result_s'])
        if key in existing:
            continue
        db.session.add(RaceResult(
            user_id=athlete.id,
            discipline=row['discipline'],
            result_s=row['result_s'],
            date=row['date'],
            competition=row['venue'] or None,
            wind=row['wind'],
            source='world_athletics',
            note=row['note'],
            created_by=g.user.id,
        ))
        existing.add(key)
        new_count += 1
    athlete.world_athletics_id = profile['wa_id']
    db.session.commit()
    return new_count


def _resolve_wa_id(raw):
    """Z formulára (číslo, URL alebo text) → WA id, alebo None. Nič iné než číslice nejde ďalej."""
    return extract_wa_id(raw)


# -----------------------------
# Routy
# -----------------------------
def _render_preview(athlete, wa_id, use_cache=True):
    try:
        profile = fetch_profile(wa_id, use_cache=use_cache)
    except WAError as e:
        flash(str(e), 'danger')
        return render_template('wa/search.html', athlete=athlete, query=wa_id, results=None,
                               failed=True), 502
    _mark_existing(profile['pbs'], _existing_keys(athlete.id))
    new_count = sum(1 for r in profile['pbs'] if not r['exists'])
    return render_template('wa/preview.html', athlete=athlete, profile=profile, new_count=new_count)


@bp.route('/hladat/<int:athlete_id>')
@roles_required('trener', 'admin')
def search_page(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    query = ' '.join((request.args.get('q') or '').split())[:120]

    # Prázdne pole predvyplníme menom zverenca, ale hľadáme až keď tréner klikne.
    if 'q' not in request.args:
        return render_template('wa/search.html', athlete=athlete,
                               query=athlete.full_name or '', results=None, failed=False)

    # Vložené ID alebo URL profilu – hľadanie preskočíme a ideme rovno na náhľad.
    wa_id = _resolve_wa_id(query)
    if wa_id:
        return _render_preview(athlete, wa_id)

    try:
        results = search_athletes(query)
    except WAError as e:
        flash(str(e), 'danger')
        return render_template('wa/search.html', athlete=athlete, query=query, results=None,
                               failed=True), 502
    return render_template('wa/search.html', athlete=athlete, query=query, results=results, failed=False)


@bp.route('/nahlad/<int:athlete_id>', methods=['POST'])
@roles_required('trener', 'admin')
def preview(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    wa_id = _resolve_wa_id(request.form.get('wa_id'))
    if not wa_id:
        flash(MSG_BAD_ID, 'danger')
        return redirect(url_for('wa.search_page', athlete_id=athlete.id))
    return _render_preview(athlete, wa_id)


@bp.route('/import/<int:athlete_id>', methods=['POST'])
@roles_required('trener', 'admin')
def import_athlete(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    # Bez wa_id vo formulári (napr. tlačidlo z detailu zverenca) použijeme uložené id.
    wa_id = _resolve_wa_id(request.form.get('wa_id')) or extract_wa_id(athlete.world_athletics_id)
    if not wa_id:
        flash('Najskôr vyber atléta na World Athletics.', 'info')
        return redirect(url_for('wa.search_page', athlete_id=athlete.id))
    return _do_import(athlete, wa_id, use_cache=True)


@bp.route('/obnovit/<int:athlete_id>', methods=['POST'])
@roles_required('trener', 'admin')
def refresh(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    wa_id = extract_wa_id(athlete.world_athletics_id)
    if not wa_id:
        flash('Zverenec ešte nemá priradený World Athletics profil.', 'info')
        return redirect(url_for('wa.search_page', athlete_id=athlete.id))
    # Obnovenie má priniesť nové výsledky, preto obchádza 24-hodinovú cache.
    return _do_import(athlete, wa_id, use_cache=False)


def _do_import(athlete, wa_id, use_cache):
    try:
        profile = fetch_profile(wa_id, use_cache=use_cache)
    except WAError as e:
        flash(str(e), 'danger')
        return redirect(url_for('wa.search_page', athlete_id=athlete.id, q=wa_id))

    new_count = _import_pbs(athlete, profile)
    total = len(profile['pbs'])
    skipped = len(profile['skipped'])
    msg = f'Načítaných {total} rekordov ({new_count} nových)'
    if skipped:
        msg += f', {skipped} preskočených (skoky, vrhy, štafety, nelegálny vietor)'
    flash(msg + f' – World Athletics: {profile["name"]}.', 'success')
    return redirect(url_for('trener.athlete_detail', athlete_id=athlete.id))
