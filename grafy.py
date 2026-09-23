"""
Blueprint 'grafy' – vývoj výkonnosti v grafoch (Chart.js) + JSON API.

Stránka /grafy dostane všetky dáta už zo servera (|tojson), rovnaké čísla vracia
aj /grafy/api/<metric>, aby sa dali použiť inde (napr. detail zverenca).
Každý dotaz je obmedzený na zverenca, ktorého smie prihlásený vidieť
(require_visible_athlete) – zverenec vidí len seba.
"""
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, g, jsonify, abort
from sqlalchemy import func

from models import db, RaceResult, TrainingLog, PlannedTraining, VideoAnalysis
from auth import login_required, require_visible_athlete, visible_athletes

bp = Blueprint('grafy', __name__, url_prefix='/grafy')

# Farby sérií – jediné validované na tmavom podklade (spec 1.3).
LIME, BLUE, MAGENTA = '#879c26', '#3987e5', '#d55181'

# Bežné poradie disciplín v ponuke; neznáme (voľný text) idú abecedne za ne.
DISCIPLINE_ORDER = ('60m', '100m', '200m', '300m', '400m', '600m', '800m', '1000m', '1500m',
                    '3000m', '5000m', '10000m', '60mH', '100mH', '110mH', '300mH', '400mH')

VOLUME_WEEKS = 12
PLAN_WEEKS = 8
METRICS = ('vysledky', 'rekordy', 'objem', 'plan', 'technika')


# -----------------------------
# Pomocné
# -----------------------------
def fmt_time(seconds):
    """Sekundy → text: 11.23 → '11.23', 125.4 → '2:05.40'. Rovnaká logika je v grafy.js."""
    if seconds is None:
        return '—'
    s = float(seconds)
    if s < 60:
        return f'{s:.2f}'
    m, sec = divmod(s, 60)
    return f'{int(m)}:{sec:05.2f}'


def _week_start(d):
    """Pondelok ISO týždňa, do ktorého deň patrí."""
    return d - timedelta(days=d.weekday())


def _week_label(monday):
    return monday.strftime('%d.%m.')


def _discipline_key(name):
    # Známe disciplíny v pevnom poradí, zvyšok abecedne za nimi.
    try:
        return (0, DISCIPLINE_ORDER.index(name), '')
    except ValueError:
        return (1, 0, name.lower())


def _target_athlete():
    """Zverenec z ?athlete_id= (tréner/admin), inak prihlásený. Cudzí → 403."""
    athlete_id = request.args.get('athlete_id', type=int)
    if athlete_id is None or athlete_id == g.user.id:
        return g.user
    return require_visible_athlete(athlete_id)


def _disciplines(athlete):
    """Disciplíny, v ktorých má zverenec výsledky (najprv známe, potom ostatné)."""
    rows = db.session.query(RaceResult.discipline) \
        .filter(RaceResult.user_id == athlete.id).distinct().all()
    return sorted((r[0] for r in rows if r[0]), key=_discipline_key)


def _pick_discipline(athlete, requested):
    """Disciplína zo selectu; ak chýba alebo ju zverenec nemá, prvá jeho."""
    disciplines = _disciplines(athlete)
    if requested and requested in disciplines:
        return requested, disciplines
    return (disciplines[0] if disciplines else None), disciplines


# -----------------------------
# Výpočty jednotlivých metrík
# -----------------------------
def results_series(athlete, discipline):
    """Výsledky v disciplíne podľa dátumu + index osobného rekordu (najmenší čas)."""
    if not discipline:
        return {'labels': [], 'values': [], 'meta': {'discipline': None, 'rows': [], 'pb_index': None}}
    rows = RaceResult.query.filter_by(user_id=athlete.id, discipline=discipline) \
        .order_by(RaceResult.date.asc(), RaceResult.id.asc()).all()
    values = [r.result_s for r in rows]
    pb_index = values.index(min(values)) if values else None
    return {
        'labels': [r.date.strftime('%d.%m.%Y') for r in rows],
        'values': values,
        'meta': {
            'discipline': discipline,
            'pb_index': pb_index,
            'pb_text': fmt_time(values[pb_index]) if values else None,
            'rows': [{
                'date': r.date.strftime('%d.%m.%Y'),
                'time': fmt_time(r.result_s),
                'competition': r.competition or '',
                'place': r.place or '',
                'wind': r.wind,
                'source': r.source,
                'is_pb': i == pb_index,
            } for i, r in enumerate(rows)],
        },
    }


def pb_series(athlete, discipline):
    """Tabuľka PB pre všetky disciplíny + 'posun PB' (bežiace minimum) vo vybranej."""
    # PB na disciplínu: min(result_s) a dátum, keď padol (prvý výskyt minima).
    pbs = []
    for disc in _disciplines(athlete):
        best = RaceResult.query.filter_by(user_id=athlete.id, discipline=disc) \
            .order_by(RaceResult.result_s.asc(), RaceResult.date.asc()).first()
        count = db.session.query(func.count(RaceResult.id)) \
            .filter_by(user_id=athlete.id, discipline=disc).scalar()
        pbs.append({
            'discipline': disc,
            'time': fmt_time(best.result_s),
            'result_s': best.result_s,
            'date': best.date.strftime('%d.%m.%Y'),
            'competition': best.competition or '',
            'count': count,
        })

    labels, values, rows = [], [], []
    if discipline:
        running = None
        for r in RaceResult.query.filter_by(user_id=athlete.id, discipline=discipline) \
                .order_by(RaceResult.date.asc(), RaceResult.id.asc()).all():
            improved = running is None or r.result_s < running
            running = r.result_s if improved else running
            labels.append(r.date.strftime('%d.%m.%Y'))
            values.append(running)
            rows.append({'date': labels[-1], 'result': fmt_time(r.result_s),
                         'pb': fmt_time(running), 'improved': improved})
    return {'labels': labels, 'values': values,
            'meta': {'discipline': discipline, 'pbs': pbs, 'rows': rows}}


def volume_series(athlete, weeks=VOLUME_WEEKS):
    """Súčet km z denníka za posledných N ISO týždňov (vrátane aktuálneho, aj prázdne)."""
    this_monday = _week_start(date.today())
    mondays = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    km = OrderedDict((m, 0.0) for m in mondays)
    sessions = Counter()
    logs = TrainingLog.query.filter(TrainingLog.user_id == athlete.id,
                                    TrainingLog.date >= mondays[0]).all()
    for t in logs:
        # Stĺpec je Date, ale default je utcnow – čerstvo vložený riadok môže niesť datetime.
        d = t.date.date() if isinstance(t.date, datetime) else t.date
        m = _week_start(d)
        if m in km:
            km[m] += float(t.distance_km or 0)
            sessions[m] += 1
    return {
        'labels': [_week_label(m) for m in mondays],
        'values': [round(v, 1) for v in km.values()],
        'meta': {
            'unit': 'km',
            'rows': [{'week': f'T{m.isocalendar()[1]}', 'from': _week_label(m),
                      'to': _week_label(m + timedelta(days=6)),
                      'km': round(km[m], 1), 'sessions': sessions[m]} for m in mondays],
            'total_km': round(sum(km.values()), 1),
        },
    }


def plan_series(athlete, weeks=PLAN_WEEKS):
    """Naplánované vs. splnené tréningy po týždňoch (posledných N vrátane aktuálneho)."""
    this_monday = _week_start(date.today())
    mondays = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    planned = OrderedDict((m, 0) for m in mondays)
    done = OrderedDict((m, 0) for m in mondays)
    rows = PlannedTraining.query.filter(PlannedTraining.athlete_id == athlete.id,
                                        PlannedTraining.date >= mondays[0],
                                        PlannedTraining.date <= this_monday + timedelta(days=6)).all()
    for p in rows:
        m = _week_start(p.date)
        if m in planned:
            planned[m] += 1
            if p.completed:
                done[m] += 1
    total_p, total_d = sum(planned.values()), sum(done.values())
    return {
        'labels': [_week_label(m) for m in mondays],
        # Dve série – 'values' drží prvú (naplánované), druhá je v meta, aby tvar API ostal rovnaký.
        'values': list(planned.values()),
        'meta': {
            'completed': list(done.values()),
            'series': ['Naplánované', 'Splnené'],
            'rows': [{'week': f'T{m.isocalendar()[1]}', 'from': _week_label(m),
                      'planned': planned[m], 'completed': done[m],
                      'pct': round(100 * done[m] / planned[m]) if planned[m] else None}
                     for m in mondays],
            'total_planned': total_p, 'total_completed': total_d,
            'pct': round(100 * total_d / total_p) if total_p else None,
        },
    }


def technique_series(athlete):
    """Kontakt so zemou a kadencia z uložených analýz videa – dve samostatné osi = dva grafy."""
    rows = VideoAnalysis.query.filter_by(user_id=athlete.id) \
        .order_by(VideoAnalysis.created_at.asc()).all()
    return {
        'labels': [v.created_at.strftime('%d.%m.%Y') for v in rows],
        'values': [round(v.contact_ms_mean, 1) if v.contact_ms_mean is not None else None for v in rows],
        'meta': {
            'unit': 'ms',
            'cadence': [round(v.cadence_spm, 1) if v.cadence_spm is not None else None for v in rows],
            'rows': [{
                'date': v.created_at.strftime('%d.%m.%Y %H:%M'),
                'label': v.label or v.original_name or '',
                'contact': v.contact_ms_mean, 'cadence': v.cadence_spm,
                'asymmetry': v.asymmetry_pct, 'steps': v.steps_detected,
                'fps': v.fps,
            } for v in rows],
        },
    }


def _metric(metric, athlete, discipline):
    if metric == 'vysledky':
        return results_series(athlete, discipline)
    if metric == 'rekordy':
        return pb_series(athlete, discipline)
    if metric == 'objem':
        return volume_series(athlete)
    if metric == 'plan':
        return plan_series(athlete)
    if metric == 'technika':
        return technique_series(athlete)
    abort(404)


# -----------------------------
# Routy
# -----------------------------
@bp.route('/')
@login_required
def index():
    athlete = _target_athlete()
    discipline, disciplines = _pick_discipline(athlete, request.args.get('discipline'))
    athletes = visible_athletes(g.user) if (g.user.is_coach or g.user.is_admin) else None

    data = {m: _metric(m, athlete, discipline) for m in METRICS}
    return render_template('grafy/index.html',
                           eyebrow='Výkonnosť', title='Grafy',
                           athlete=athlete, athletes=athletes,
                           discipline=discipline, disciplines=disciplines,
                           data=data)


@bp.route('/api/<metric>')
@login_required
def api(metric):
    """{labels, values, meta} pre jednu metriku; ?athlete_id= a ?discipline= ako na stránke."""
    if metric not in METRICS:
        return jsonify({'error': 'Neznáma metrika.'}), 404
    athlete = _target_athlete()
    discipline, _ = _pick_discipline(athlete, request.args.get('discipline'))
    payload = _metric(metric, athlete, discipline)
    payload['meta']['athlete_id'] = athlete.id
    return jsonify(payload)


@bp.before_request
def _api_needs_json_401():
    """Neprihlásený klient API má dostať 401 JSON, nie presmerovanie na login
    (auth._wants_json pozná len cesty /api/..., naša je /grafy/api/...)."""
    if request.path.startswith('/grafy/api/') and g.get('user') is None:
        return jsonify({'error': 'Najskôr sa prihlás.'}), 401


@bp.errorhandler(403)
def _forbidden(_e):
    # /grafy/api/* nezačína na /api/, takže globálny handler by vrátil HTML – tu chceme JSON.
    if request.path.startswith('/grafy/api/'):
        return jsonify({'error': 'Na tohto zverenca nemáš oprávnenie.'}), 403
    return render_template('403.html'), 403
