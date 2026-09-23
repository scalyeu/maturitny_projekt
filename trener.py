"""
Blueprint 'trener' – správa zverencov, výsledky z pretekov a osobné rekordy.

Tréner tu:
  - vidí zoznam svojich (potvrdených) zverencov a čakajúce žiadosti,
  - pridá existujúceho zverenca podľa používateľského mena (vzťah musí
    zverenec potvrdiť – kým tak neurobí, tréner jeho údaje nevidí),
  - vytvorí zverencovi nový účet (súhlas je implicitný, heslo nastavil tréner),
  - v detaile zverenca spravuje výsledky z pretekov; osobné rekordy sa počítajú.

Pomocné funkcie (format_result, parse_time_fields, personal_bests, …) používa
aj blueprint 'zverenec', aby obe strany zobrazovali časy rovnako.
"""
from datetime import date, datetime

from flask import (Blueprint, render_template, g, request, redirect, url_for,
                   flash, abort, current_app)
from sqlalchemy import func

from models import db, User, RaceResult, PlannedTraining, VideoAnalysis, HurdleAnalysis
from auth import (roles_required, require_visible_athlete, visible_athletes,
                  athlete_ids_visible_to, USERNAME_RE, PASSWORD_MIN)

bp = Blueprint('trener', __name__, url_prefix='/trener')

# Bežné dráhové disciplíny vo výbere; poradie určuje aj poradie osobných rekordov.
DISCIPLINES = (
    '60m', '100m', '200m', '400m', '800m', '1500m', '3000m', '5000m', '10000m',
    '60mH', '100mH', '110mH', '400mH', '3000mSC', '4x100m', '4x400m',
)
OTHER_DISCIPLINE = 'iná'

SOURCE_LABELS = {'manual': 'Ručne', 'world_athletics': 'World Athletics', 'stopky': 'Stopky'}

# Po tomto čase (24 h) formulár výsledok odmietne – chráni pred preklepom v minútach.
MAX_RESULT_S = 24 * 3600


# -----------------------------
# Spoločné pomôcky (importuje ich aj zverenec.py)
# -----------------------------
def format_result(seconds):
    """11.23 → '11.23', 125.4 → '2:05.40', 3725.5 → '1:02:05.50'. Neplatná hodnota → '—'."""
    try:
        s = round(float(seconds), 2)
    except (TypeError, ValueError):
        return '—'
    if s < 0:
        return '—'
    if s < 60:
        return f'{s:.2f}'
    minutes, sec = divmod(s, 60)
    minutes = int(minutes)
    if minutes < 60:
        return f'{minutes}:{sec:05.2f}'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{sec:05.2f}'


@bp.app_template_filter('cas')
def cas_filter(seconds):
    return format_result(seconds)


@bp.app_template_filter('zdroj')
def zdroj_filter(source):
    return SOURCE_LABELS.get(source or 'manual', source or 'Ručne')


def _int_field(form, name, lo, hi, label, errors):
    """Prečíta celé číslo z formulára; prázdne pole = 0. Chyby pridá do `errors`."""
    raw = (form.get(name) or '').strip()
    if raw == '':
        return 0
    try:
        value = int(raw)
    except ValueError:
        errors.append(f'{label}: zadaj celé číslo.')
        return 0
    if value < lo or value > hi:
        errors.append(f'{label}: povolený rozsah {lo}–{hi}.')
        return 0
    return value


def parse_time_fields(form):
    """
    Minúty / sekundy / stotiny z troch polí (ako v denníku tréningov) → sekundy.
    Vracia (seconds, errors). Nulový alebo záporný čas je chyba.
    """
    errors = []
    minutes = _int_field(form, 't_min', 0, 1439, 'Minúty', errors)
    seconds = _int_field(form, 't_sec', 0, 59, 'Sekundy', errors)
    hundredths = _int_field(form, 't_hun', 0, 99, 'Stotiny', errors)
    if errors:
        return None, errors
    total = minutes * 60 + seconds + hundredths / 100.0
    if total <= 0:
        return None, ['Zadaj výsledný čas – musí byť väčší než nula.']
    if total > MAX_RESULT_S:
        return None, ['Čas je nereálne veľký.']
    return round(total, 2), []


def parse_result_form(form):
    """
    Spoločná validácia formulára „Pridať výsledok“ pre trénera aj zverenca.
    Vracia (dict polí pre RaceResult, zoznam chýb). Pri chybách je dict None.
    """
    errors = []

    discipline = (form.get('discipline') or '').strip()
    if discipline == OTHER_DISCIPLINE:
        discipline = (form.get('discipline_other') or '').strip()
    if not discipline:
        errors.append('Vyber disciplínu (alebo ju vypíš).')
    elif len(discipline) > 40:
        errors.append('Názov disciplíny je príliš dlhý (max. 40 znakov).')

    result_s, time_errors = parse_time_fields(form)
    errors.extend(time_errors)

    race_date = None
    raw_date = (form.get('date') or '').strip()
    if not raw_date:
        errors.append('Zadaj dátum pretekov.')
    else:
        try:
            race_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            errors.append('Dátum má nesprávny formát.')
        else:
            if race_date > date.today():
                errors.append('Dátum pretekov nemôže byť v budúcnosti.')

    wind = None
    raw_wind = (form.get('wind') or '').strip().replace(',', '.')
    if raw_wind:
        try:
            wind = float(raw_wind)
        except ValueError:
            errors.append('Vietor musí byť číslo (napr. 1.2 alebo -0.5).')
        else:
            if abs(wind) > 20:
                errors.append('Vietor mimo rozumného rozsahu.')

    competition = (form.get('competition') or '').strip()[:160] or None
    place = (form.get('place') or '').strip()[:20] or None
    note = (form.get('note') or '').strip() or None

    if errors:
        return None, errors
    return {
        'discipline': discipline, 'result_s': result_s, 'date': race_date,
        'competition': competition, 'place': place, 'wind': wind, 'note': note,
    }, []


def discipline_order(name):
    """Známe disciplíny v pevnom poradí, ostatné abecedne za nimi."""
    try:
        return (0, DISCIPLINES.index(name), '')
    except ValueError:
        return (1, 0, name.lower())


def personal_bests(results):
    """Najlepší (najmenší) výsledok na disciplínu; pri zhode skorší dátum. Vracia zoznam RaceResult."""
    best = {}
    for r in results:
        cur = best.get(r.discipline)
        if cur is None or r.result_s < cur.result_s or (r.result_s == cur.result_s and r.date < cur.date):
            best[r.discipline] = r
    return sorted(best.values(), key=lambda r: discipline_order(r.discipline))


def results_for(athlete_id):
    """Výsledky jedného zverenca, najnovšie prvé."""
    return (RaceResult.query.filter_by(user_id=athlete_id)
            .order_by(RaceResult.date.desc(), RaceResult.created_at.desc()).all())


def result_page_context(athlete):
    """Údaje, ktoré potrebuje tabuľka výsledkov aj formulár – pre trénera aj zverenca."""
    results = results_for(athlete.id)
    return {
        'athlete': athlete,
        'results': results,
        'pbs': personal_bests(results),
        'disciplines': DISCIPLINES,
        'other_discipline': OTHER_DISCIPLINE,
        'today': date.today().isoformat(),
    }


def visible_result_or_abort(result_id):
    """Načíta výsledok a overí, že patrí niekomu, koho údaje smie g.user vidieť."""
    row = db.session.get(RaceResult, result_id)
    if row is None:
        abort(404)
    if row.user_id not in athlete_ids_visible_to(g.user):
        abort(403)
    return row


def _wa_search_available():
    # WA blueprint môže mať vyhľadávaciu stránku; kým nie je, odkaz sa nezobrazí a stránka nepadne.
    return 'wa.search_page' in current_app.view_functions


# -----------------------------
# Zoznam zverencov
# -----------------------------
def _athlete_stats(athletes):
    """Počet výsledkov, posledný výsledok a najbližší plánovaný tréning pre každého zverenca."""
    ids = [a.id for a in athletes]
    stats = {a.id: {'count': 0, 'last': None, 'next': None} for a in athletes}
    if not ids:
        return stats

    for uid, cnt in (db.session.query(RaceResult.user_id, func.count(RaceResult.id))
                     .filter(RaceResult.user_id.in_(ids)).group_by(RaceResult.user_id).all()):
        stats[uid]['count'] = cnt

    for r in (RaceResult.query.filter(RaceResult.user_id.in_(ids))
              .order_by(RaceResult.date.desc(), RaceResult.created_at.desc()).all()):
        if stats[r.user_id]['last'] is None:
            stats[r.user_id]['last'] = r

    for p in (PlannedTraining.query
              .filter(PlannedTraining.athlete_id.in_(ids), PlannedTraining.date >= date.today())
              .order_by(PlannedTraining.date.asc()).all()):
        if stats[p.athlete_id]['next'] is None:
            stats[p.athlete_id]['next'] = p
    return stats


def _athlete_groups():
    """
    Tréner: jedna skupina (jeho potvrdení zverenci).
    Admin: všetci zverenci zoskupení podľa trénera (bez trénera na konci).
    """
    if g.user.is_admin:
        athletes = (User.query.filter_by(role='zverenec')
                    .order_by(User.coach_id, User.username).all())
        by_coach = {}
        for a in athletes:
            # Nepotvrdený vzťah admin vidí ako čakajúci, nie ako zverenca daného trénera.
            key = a.coach_id if a.coach_confirmed else None
            by_coach.setdefault(key, []).append(a)
        coach_ids = [k for k in by_coach if k is not None]
        coaches = {u.id: u for u in User.query.filter(User.id.in_(coach_ids)).all()} if coach_ids else {}
        groups = [(coaches.get(cid), by_coach[cid]) for cid in sorted(coach_ids, key=lambda c: coaches[c].username if c in coaches else '')]
        if None in by_coach:
            groups.append((None, by_coach[None]))
        return groups
    mine = [u for u in visible_athletes(g.user) if u.id != g.user.id]
    return [(g.user, mine)]


@bp.route('/zverenci')
@roles_required('trener', 'admin')
def athletes():
    groups = _athlete_groups()
    all_athletes = [a for _coach, members in groups for a in members]
    pending = (User.query.filter_by(coach_id=g.user.id, coach_confirmed=False)
               .order_by(User.username).all())
    return render_template('trener/zverenci.html',
                           groups=groups, stats=_athlete_stats(all_athletes),
                           pending=pending, athlete_count=len(all_athletes))


@bp.route('/zverenci/pridat', methods=['POST'])
@roles_required('trener', 'admin')
def attach_athlete():
    username = (request.form.get('username') or '').strip().lower()
    athlete = User.query.filter_by(username=username).first() if username else None

    if athlete is None:
        flash('Používateľ s týmto menom neexistuje. Zverenec sa musí najskôr zaregistrovať.', 'danger')
    elif athlete.id == g.user.id:
        flash('Sám seba ako zverenca pridať nemôžeš.', 'danger')
    elif not athlete.is_athlete:
        flash(f'Účet „{athlete.username}“ nie je zverenec ({athlete.role_label}).', 'danger')
    elif athlete.coach_id == g.user.id:
        if athlete.coach_confirmed:
            flash(f'{athlete.display_name} už je tvoj zverenec.', 'info')
        else:
            flash(f'{athlete.display_name} už má od teba žiadosť – čaká na potvrdenie.', 'info')
    elif athlete.coach_id is not None:
        flash(f'{athlete.display_name} už má trénera. Najskôr sa musí od neho odpojiť.', 'danger')
    else:
        # Súhlas dáva zverenec – kým žiadosť nepotvrdí, tréner jeho údaje nevidí.
        athlete.coach_id = g.user.id
        athlete.coach_confirmed = False
        db.session.commit()
        flash(f'Žiadosť odoslaná. {athlete.display_name} musí vzťah potvrdiť vo svojej sekcii Výsledky.', 'success')
    return redirect(url_for('trener.athletes'))


@bp.route('/zverenci/vytvorit', methods=['POST'])
@roles_required('trener', 'admin')
def create_athlete():
    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    full_name = (request.form.get('full_name') or '').strip()[:120]

    errors = []
    if not USERNAME_RE.match(username):
        errors.append('Používateľské meno musí mať 3–32 znakov: malé písmená, číslice, bodka, pomlčka alebo podčiarkovník.')
    if len(password) < PASSWORD_MIN:
        errors.append(f'Heslo musí mať aspoň {PASSWORD_MIN} znakov.')
    if not errors and User.query.filter_by(username=username).first():
        errors.append('Toto používateľské meno je už obsadené.')
    if errors:
        for e in errors:
            flash(e, 'danger')
        return redirect(url_for('trener.athletes'))

    # Účet zakladá tréner a heslo pozná – súhlas so vzťahom je implicitný.
    athlete = User(username=username, role='zverenec', full_name=full_name or None,
                   coach_id=g.user.id, coach_confirmed=True, must_change_password=True)
    athlete.set_password(password)
    db.session.add(athlete)
    db.session.commit()
    flash(f'Účet „{athlete.username}“ vytvorený. Zverenec si pri prvom prihlásení zmení heslo.', 'success')
    return redirect(url_for('trener.athlete_detail', athlete_id=athlete.id))


@bp.route('/zverenci/<int:athlete_id>/odpojit', methods=['POST'])
@roles_required('trener', 'admin')
def detach_athlete(athlete_id):
    athlete = db.session.get(User, athlete_id)
    if athlete is None:
        abort(404)
    # Odpojiť smie len tréner, ktorému zverenec patrí (aj čakajúcu žiadosť), alebo admin.
    if athlete.coach_id != g.user.id and not g.user.is_admin:
        abort(403)
    if athlete.coach_id is None:
        flash(f'{athlete.display_name} nemá trénera.', 'info')
    else:
        athlete.coach_id = None
        athlete.coach_confirmed = False
        db.session.commit()
        flash(f'{athlete.display_name} bol odpojený. Jeho údaje už nevidíš.', 'success')
    return redirect(url_for('trener.athletes'))


# -----------------------------
# Detail zverenca
# -----------------------------
@bp.route('/zverenci/<int:athlete_id>')
@roles_required('trener', 'admin')
def athlete_detail(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    ctx = result_page_context(athlete)
    ctx.update({
        'videos': (VideoAnalysis.query.filter_by(user_id=athlete.id)
                   .order_by(VideoAnalysis.created_at.desc()).limit(8).all()),
        'hurdles': (HurdleAnalysis.query.filter_by(user_id=athlete.id)
                    .order_by(HurdleAnalysis.created_at.desc()).limit(8).all()),
        'next_training': (PlannedTraining.query
                          .filter(PlannedTraining.athlete_id == athlete.id,
                                  PlannedTraining.date >= date.today())
                          .order_by(PlannedTraining.date.asc()).first()),
        'wa_search': _wa_search_available(),
        'can_detach': athlete.coach_id == g.user.id or g.user.is_admin,
        'is_self': athlete.id == g.user.id,
    })
    return render_template('trener/detail.html', **ctx)


@bp.route('/zverenci/<int:athlete_id>/vysledky', methods=['POST'])
@roles_required('trener', 'admin')
def add_result(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    data, errors = parse_result_form(request.form)
    if errors:
        for e in errors:
            flash(e, 'danger')
    else:
        db.session.add(RaceResult(user_id=athlete.id, created_by=g.user.id, source='manual', **data))
        db.session.commit()
        flash(f'Výsledok {data["discipline"]} {format_result(data["result_s"])} uložený.', 'success')
    return redirect(url_for('trener.athlete_detail', athlete_id=athlete.id) + '#vysledky')


@bp.route('/vysledky/<int:result_id>/zmazat', methods=['POST'])
@roles_required('trener', 'admin')
def delete_result(result_id):
    row = visible_result_or_abort(result_id)
    athlete_id = row.user_id
    db.session.delete(row)
    db.session.commit()
    flash('Výsledok bol zmazaný.', 'success')
    return redirect(url_for('trener.athlete_detail', athlete_id=athlete_id) + '#vysledky')
