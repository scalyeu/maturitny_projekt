"""
Blueprint 'planovanie' – kalendár tréningov (url_prefix /plan).

Tréner naplánuje zverencovi tréning na konkrétny deň, zverenec ho označí za splnený.
Pri splnení sa zverencovi vytvorí (alebo prepojí) skutočný zápis v denníku
(TrainingLog), takže jeden kalendár ukazuje plán aj to, čo sa naozaj odbehlo.

Prístup: zverenec vidí a plní len svoje plány; tréner plánuje len svojim
(potvrdeným) zverencom a upravuje len vlastné plány; admin všetko.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, abort, jsonify)

from models import db, PlannedTraining, TrainingLog
from auth import (login_required, roles_required, athlete_ids_visible_to,
                  visible_athletes, require_visible_athlete)

bp = Blueprint('planovanie', __name__, url_prefix='/plan')

WEEKDAYS_SK = ['Pondelok', 'Utorok', 'Streda', 'Štvrtok', 'Piatok', 'Sobota', 'Nedeľa']
WEEKDAYS_SHORT_SK = ['Po', 'Ut', 'St', 'Št', 'Pi', 'So', 'Ne']
MONTHS_SK = ['Január', 'Február', 'Marec', 'Apríl', 'Máj', 'Jún',
             'Júl', 'August', 'September', 'Október', 'November', 'December']

# Prehľad splnenia: uplynulých 30 dní a najbližších 7 dní (vrátane dneška).
OVERVIEW_PAST_DAYS = 30
OVERVIEW_UPCOMING_DAYS = 7


# -----------------------------
# Dátumy
# -----------------------------
def _parse_iso_date(text):
    """'2026-09-23' → date; čokoľvek iné v URL je 404, nie 500."""
    try:
        return date.fromisoformat((text or '').strip())
    except ValueError:
        abort(404)


def _month_from_args():
    """?y=&m= s predvoleným aktuálnym mesiacom; nezmyselné hodnoty → 404."""
    today = date.today()
    year = request.args.get('y', type=int)
    month = request.args.get('m', type=int)
    year = today.year if year is None else year
    month = today.month if month is None else month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        abort(404)
    return year, month


def _month_range(year, month):
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first, nxt - timedelta(days=1)


def _shift_month(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _month_weeks(year, month):
    """Týždne mesiaca (Po–Ne) ako zoznamy dátumov; okrajové dni patria susedným mesiacom."""
    first, last = _month_range(year, month)
    start = first - timedelta(days=first.weekday())
    weeks = []
    while start <= last:
        weeks.append([start + timedelta(days=i) for i in range(7)])
        start += timedelta(days=7)
    return weeks


# -----------------------------
# Kto komu plánuje
# -----------------------------
def _is_planner():
    return g.user.is_coach or g.user.is_admin


def _athlete_choices():
    """Zverenci do <select> trénera/admina – sám sebe tréner neplánuje."""
    if not _is_planner():
        return []
    return [u for u in visible_athletes(g.user) if u.id != g.user.id]


def _target_athlete(choices=None):
    """
    Koho kalendár sa zobrazuje. Zverenec vždy svoj (cudzie athlete_id skončí 403).
    Tréner/admin podľa ?athlete_id=, inak prvý zverenec zo zoznamu; None = nemá zverencov.
    """
    requested = request.values.get('athlete_id', type=int)
    if not _is_planner():
        if requested is not None and requested != g.user.id:
            abort(403)
        return g.user
    if requested is None:
        choices = _athlete_choices() if choices is None else choices
        return choices[0] if choices else None
    athlete = require_visible_athlete(requested)   # cudzí zverenec → 403
    if athlete.id == g.user.id:
        abort(404)
    return athlete


def _load_plan(plan_id):
    plan = db.session.get(PlannedTraining, plan_id)
    if plan is None:
        abort(404)
    return plan


def _can_manage(plan, visible=None):
    """Upraviť/zmazať: tréner len vlastné plány u svojich (stále potvrdených) zverencov; admin všetko."""
    if g.user.is_admin:
        return True
    if not g.user.is_coach or plan.coach_id != g.user.id:
        return False
    visible = athlete_ids_visible_to(g.user) if visible is None else visible
    return plan.athlete_id in visible


def _can_complete(plan, visible=None):
    """Splniť/zrušiť splnenie: zverenec len svoje, tréner len u svojich zverencov, admin všetko."""
    if g.user.is_admin:
        return True
    if not g.user.is_coach:
        return plan.athlete_id == g.user.id
    visible = athlete_ids_visible_to(g.user) if visible is None else visible
    return plan.athlete_id in visible


def _status(plan, today):
    """done | missed (termín uplynul, nesplnené) | pending."""
    if plan.completed:
        return 'done'
    return 'missed' if plan.date < today else 'pending'


def _day_url(day, athlete):
    """Späť na deň; tréner/admin potrebuje v URL aj zverenca, zverenec nie."""
    if athlete.id == g.user.id:
        return url_for('planovanie.day', iso=day.isoformat())
    return url_for('planovanie.day', iso=day.isoformat(), athlete_id=athlete.id)


def _plans_between(athlete_ids, start, end):
    if not athlete_ids:
        return []
    return PlannedTraining.query.filter(
        PlannedTraining.athlete_id.in_(athlete_ids),
        PlannedTraining.date >= start, PlannedTraining.date <= end,
    ).order_by(PlannedTraining.date, PlannedTraining.id).all()


# -----------------------------
# Šablóny
# -----------------------------
@bp.context_processor
def _inject_names():
    return {'WEEKDAYS_SK': WEEKDAYS_SK, 'WEEKDAYS_SHORT_SK': WEEKDAYS_SHORT_SK,
            'MONTHS_SK': MONTHS_SK}


@bp.app_template_filter('plan_duration')
def plan_duration(log):
    """TrainingLog → '12:34.50' alebo '45.00 s' – rovnaký zápis ako v denníku; bez času ''."""
    if log is None or (log.duration_min is None and log.duration_sec is None):
        return ''
    minutes = int(log.duration_min or 0)
    seconds = float(log.duration_sec or 0)
    return f'{minutes}:{seconds:05.2f}' if minutes else f'{seconds:.2f} s'


@bp.app_template_filter('plan_date')
def plan_date(day):
    """date → '23. 9. 2026'."""
    return f'{day.day}. {day.month}. {day.year}' if day else ''


# -----------------------------
# Kalendár
# -----------------------------
@bp.route('/')
@login_required
def index():
    year, month = _month_from_args()
    choices = _athlete_choices()
    athlete = _target_athlete(choices)
    today = date.today()
    first, last = _month_range(year, month)

    days = {}
    stats = {'planned': 0, 'done': 0, 'missed': 0, 'logs': 0}
    if athlete is not None:
        for p in _plans_between([athlete.id], first, last):
            days.setdefault(p.date, {'plans': [], 'logs': []})['plans'].append(p)
            stats['planned'] += 1
            status = _status(p, today)
            if status == 'done':
                stats['done'] += 1
            elif status == 'missed':
                stats['missed'] += 1
        logs = TrainingLog.query.filter(
            TrainingLog.user_id == athlete.id,
            TrainingLog.date >= first, TrainingLog.date <= last,
        ).order_by(TrainingLog.date, TrainingLog.id).all()
        for log in logs:
            days.setdefault(log.date, {'plans': [], 'logs': []})['logs'].append(log)
            stats['logs'] += 1

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, +1)
    # Do odkazov ide athlete_id len keď sa pozerá tréner/admin (url_for None vynechá).
    aid = athlete.id if (athlete is not None and athlete.id != g.user.id) else None
    return render_template(
        'planovanie/index.html',
        year=year, month=month, month_name=MONTHS_SK[month - 1],
        weeks=_month_weeks(year, month), days=days, today=today,
        athlete=athlete, choices=choices, aid=aid, is_planner=_is_planner(),
        prev_y=prev_y, prev_m=prev_m, next_y=next_y, next_m=next_m, stats=stats,
    )


@bp.route('/api/kalendar')
@login_required
def api_calendar():
    """JSON {date: [{id, title, completed}]} pre daný mesiac a zverenca."""
    today = date.today()
    year = request.args.get('y', type=int)
    month = request.args.get('m', type=int)
    year = today.year if year is None else year
    month = today.month if month is None else month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        return jsonify({'error': 'Neplatný mesiac.'}), 400

    requested = request.args.get('athlete_id', type=int)
    if not _is_planner():
        if requested is not None and requested != g.user.id:
            return jsonify({'error': 'Na toto nemáš oprávnenie.'}), 403
        athlete_id = g.user.id
    elif requested is None:
        choices = _athlete_choices()
        athlete_id = choices[0].id if choices else None
    else:
        # Rovnaké pravidlo ako v HTML pohľade, len s JSON odpoveďou namiesto stránky 403.
        if requested not in athlete_ids_visible_to(g.user):
            return jsonify({'error': 'Na toto nemáš oprávnenie.'}), 403
        if requested == g.user.id:
            return jsonify({'error': 'Zverenec neexistuje.'}), 404
        athlete_id = requested

    result = {}
    if athlete_id is not None:
        first, last = _month_range(year, month)
        for p in _plans_between([athlete_id], first, last):
            result.setdefault(p.date.isoformat(), []).append(
                {'id': p.id, 'title': p.title, 'completed': bool(p.completed)})
    return jsonify(result)


# -----------------------------
# Deň
# -----------------------------
@bp.route('/den/<iso>')
@login_required
def day(iso):
    the_day = _parse_iso_date(iso)
    choices = _athlete_choices()
    athlete = _target_athlete(choices)
    if athlete is None:
        flash('Najskôr si pridaj zverenca – potom mu môžeš plánovať tréningy.', 'info')
        return redirect(url_for('planovanie.index'))

    plans = PlannedTraining.query.filter_by(athlete_id=athlete.id, date=the_day) \
        .order_by(PlannedTraining.id).all()
    logs = TrainingLog.query.filter_by(user_id=athlete.id, date=the_day) \
        .order_by(TrainingLog.id).all()

    visible = athlete_ids_visible_to(g.user)
    perms = {p.id: {'manage': _can_manage(p, visible), 'complete': _can_complete(p, visible)}
             for p in plans}
    linked = {p.training_log_id: p for p in plans if p.training_log_id}
    # Na prepojenie sa ponúkajú len zápisy z toho dňa, ktoré ešte nepatria inému plánu.
    free_logs = [log for log in logs if log.id not in linked]
    today = date.today()
    aid = athlete.id if athlete.id != g.user.id else None
    return render_template(
        'planovanie/day.html',
        day=the_day, weekday=WEEKDAYS_SK[the_day.weekday()], today=today,
        prev_day=the_day - timedelta(days=1), next_day=the_day + timedelta(days=1),
        athlete=athlete, aid=aid, is_planner=_is_planner(), choices=choices,
        plans=plans, logs=logs, perms=perms, linked=linked, free_logs=free_logs,
        statuses={p.id: _status(p, today) for p in plans},
    )


@bp.route('/den/<iso>/pridat', methods=['POST'])
@roles_required('trener', 'admin')
def add(iso):
    the_day = _parse_iso_date(iso)
    athlete_id = request.form.get('athlete_id', type=int)
    if athlete_id is None:
        abort(400)
    athlete = require_visible_athlete(athlete_id)
    if athlete.id == g.user.id:
        abort(404)

    title = (request.form.get('title') or '').strip()[:160]
    description = (request.form.get('description') or '').strip() or None
    if not title:
        flash('Zadaj názov tréningu.', 'danger')
        return redirect(_day_url(the_day, athlete))

    # coach_id je vždy prihlásený používateľ – nikdy z formulára.
    plan = PlannedTraining(athlete_id=athlete.id, coach_id=g.user.id, date=the_day,
                           title=title, description=description)
    db.session.add(plan)
    db.session.commit()
    flash(f'Tréning „{title}“ naplánovaný na {plan_date(the_day)}.', 'success')
    return redirect(_day_url(the_day, athlete))


@bp.route('/<int:plan_id>/upravit', methods=['POST'])
@login_required
def edit(plan_id):
    plan = _load_plan(plan_id)
    if not _can_manage(plan):
        abort(403)

    title = (request.form.get('title') or '').strip()[:160]
    description = (request.form.get('description') or '').strip() or None
    new_day = plan.date
    raw_date = (request.form.get('date') or '').strip()
    if raw_date:
        try:
            new_day = date.fromisoformat(raw_date)
        except ValueError:
            flash('Neplatný dátum.', 'danger')
            return redirect(_day_url(plan.date, plan.athlete))
    if not title:
        flash('Zadaj názov tréningu.', 'danger')
        return redirect(_day_url(plan.date, plan.athlete))

    plan.title = title
    plan.description = description
    plan.date = new_day
    db.session.commit()
    flash('Tréning upravený.', 'success')
    return redirect(_day_url(new_day, plan.athlete))


@bp.route('/<int:plan_id>/zmazat', methods=['POST'])
@login_required
def delete(plan_id):
    plan = _load_plan(plan_id)
    if not _can_manage(plan):
        abort(403)
    the_day, athlete, title = plan.date, plan.athlete, plan.title
    # Zápis v denníku (TrainingLog) zostáva – patrí zverencovi, nie plánu.
    db.session.delete(plan)
    db.session.commit()
    flash(f'Tréning „{title}“ zmazaný.', 'success')
    return redirect(_day_url(the_day, athlete))


# -----------------------------
# Splnenie
# -----------------------------
@bp.route('/<int:plan_id>/splnit', methods=['POST'])
@login_required
def complete(plan_id):
    plan = _load_plan(plan_id)
    if not _can_complete(plan):
        abort(403)
    athlete = plan.athlete
    back = _day_url(plan.date, athlete)
    if plan.completed:
        flash('Tréning je už označený ako splnený.', 'info')
        return redirect(back)

    note = (request.form.get('note') or '').strip() or None

    if request.form.get('mode') == 'link':
        log_id = request.form.get('training_log_id', type=int)
        log = db.session.get(TrainingLog, log_id) if log_id else None
        if log is None:
            abort(404)
        # Zápis musí patriť tomu istému zverencovi a byť z toho dňa – id z formulára samo nestačí.
        if log.user_id != plan.athlete_id or log.date != plan.date:
            abort(403)
        taken = PlannedTraining.query.filter(PlannedTraining.training_log_id == log.id,
                                             PlannedTraining.id != plan.id).first()
        if taken is not None:
            flash('Tento zápis je už prepojený s iným plánovaným tréningom.', 'danger')
            return redirect(back)
    else:
        training_type = (request.form.get('training_type') or '').strip()[:100] or plan.title[:100]
        distance_km = request.form.get('distance_km', type=float)
        dur_min = request.form.get('dur_min', type=int)
        dur_sec = request.form.get('dur_sec', type=float)
        if distance_km is not None and distance_km < 0:
            distance_km = None
        if dur_min is not None and dur_min < 0:
            dur_min = None
        if dur_sec is not None and not (0 <= dur_sec < 60):
            dur_sec = None
        # Zápis ide do denníka ZVERENCA (aj keď plní tréner), s dátumom plánu.
        log = TrainingLog(user_id=plan.athlete_id, date=plan.date, training_type=training_type,
                          distance_km=distance_km, duration_min=dur_min, duration_sec=dur_sec,
                          notes=note)
        db.session.add(log)
        db.session.flush()

    plan.completed = True
    plan.completed_at = datetime.utcnow()
    plan.athlete_note = note
    plan.training_log_id = log.id
    db.session.commit()
    flash(f'Tréning „{plan.title}“ je splnený.', 'success')
    return redirect(back)


@bp.route('/<int:plan_id>/nesplnene', methods=['POST'])
@login_required
def uncomplete(plan_id):
    plan = _load_plan(plan_id)
    if not _can_complete(plan):
        abort(403)
    # Zápis v denníku ostáva (je to reálny tréning), ruší sa len väzba a príznak.
    plan.completed = False
    plan.completed_at = None
    plan.training_log_id = None
    db.session.commit()
    flash(f'Tréning „{plan.title}“ je označený ako nesplnený.', 'success')
    return redirect(_day_url(plan.date, plan.athlete))


# -----------------------------
# Prehľad splnenia
# -----------------------------
@bp.route('/prehlad')
@login_required
def overview():
    today = date.today()
    since = today - timedelta(days=OVERVIEW_PAST_DAYS)
    until = today + timedelta(days=OVERVIEW_UPCOMING_DAYS - 1)

    athletes = _athlete_choices() if _is_planner() else [g.user]
    plans = _plans_between([a.id for a in athletes], since, until)

    rows = []
    totals = {'planned': 0, 'done': 0, 'missed': 0}
    for a in athletes:
        # Percentá len z tréningov, ktorých termín už nastal (dnešok vrátane), ako na prehľade trénera.
        due = [p for p in plans if p.athlete_id == a.id and p.date <= today]
        done = sum(1 for p in due if p.completed)
        missed = sum(1 for p in due if not p.completed and p.date < today)
        rows.append({'athlete': a, 'planned': len(due), 'done': done, 'missed': missed,
                     'pct': round(100 * done / len(due)) if due else None})
        totals['planned'] += len(due)
        totals['done'] += done
        totals['missed'] += missed
    totals['pct'] = round(100 * totals['done'] / totals['planned']) if totals['planned'] else None

    upcoming = [p for p in plans if p.date >= today]
    missed_list = [p for p in plans if not p.completed and p.date < today]
    return render_template(
        'planovanie/overview.html',
        today=today, since=since, until=until, rows=rows, totals=totals,
        upcoming=upcoming, missed_list=missed_list, is_planner=_is_planner(),
        past_days=OVERVIEW_PAST_DAYS, upcoming_days=OVERVIEW_UPCOMING_DAYS,
    )
