"""
Blueprint 'stopky' – stopky s kamerou a virtuálnou fotobunkou.

Stránka je verejná: meranie beží celé v prehliadači (static/js/stopky.js),
server nič nepočíta. Fotobunka vie bežať aj nad videom zo záznamu (súbor
zo zariadenia alebo video z už nahraných analýz) – vtedy ide čas z časovej
stopy videa. Prihlásenému používateľovi navyše uloží nameraný čas ako
RaceResult(source='stopky') a ukáže posledné merania, ktoré smie vidieť.
Tréner ukladá čas vybranému zverencovi – cudzí zverenec skončí 403, nie potichu
nahradený vlastným účtom.
"""
import math
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from models import db, RaceResult, VideoAnalysis, HurdleAnalysis
from auth import login_required, visible_athletes, require_visible_athlete, scope_query

bp = Blueprint('stopky', __name__, url_prefix='/stopky')

# Ponuka disciplín vo formulári; 'iné' odkryje voľné textové pole.
DISCIPLINES = ('60m', '100m', '200m', '300m', '400m', '600m', '800m', '1500m',
               '60mH', '100mH', '110mH', '300mH', '400mH', 'iné')
OTHER_DISCIPLINE = 'iné'

# Stopky nikto nenechá bežať dlhšie – chráni DB pred nezmyslami z upraveného formulára.
MAX_RESULT_S = 6 * 3600
MIN_RESULT_S = 0.01
NOTE_MAX = 500
STOP_MODES = {
    'fotobunka': 'Stopky s kamerou – zastavené fotobunkou',
    'rucne': 'Stopky s kamerou – zastavené ručne',
    'zaznam': 'Stopky zo záznamu videa – zastavené fotobunkou',
    'zaznam-rucne': 'Stopky zo záznamu videa – zastavené ručne',
}
PHOTOCELL_MODES = ('fotobunka', 'zaznam')
SAVED_VIDEOS_LIMIT = 20


def _parse_seconds(raw):
    """'12.34' alebo '12,34' → float; None ak to nie je rozumný čas."""
    try:
        value = float((raw or '').strip().replace(',', '.'))
    except ValueError:
        return None
    if not math.isfinite(value) or not (MIN_RESULT_S <= value <= MAX_RESULT_S):
        return None
    return round(value, 3)


def _parse_laps(raw):
    """Medzičasy z JS ('12.34,25.10') → zoznam floatov; nečitateľné hodnoty sa preskočia."""
    laps = []
    for part in (raw or '').split(','):
        value = _parse_seconds(part)
        if value is not None:
            laps.append(value)
    return laps[:50]


def _format_seconds(value):
    """Rovnaký formát ako RaceResult.result_text, len pre voľné číslo."""
    if value < 60:
        return f'{value:.2f}'
    minutes, seconds = divmod(value, 60)
    return f'{int(minutes)}:{seconds:05.2f}'


def _build_note(user_note, laps, stop_mode, precision_ms):
    """Poznámka používateľa + riadok o tom, ako sa čas nameral (poctivosť o presnosti)."""
    lines = []
    if user_note:
        lines.append(user_note)
    if laps:
        lines.append('Medzičasy: ' + ', '.join(_format_seconds(v) for v in laps))
    detail = STOP_MODES.get(stop_mode)
    if detail:
        if stop_mode in PHOTOCELL_MODES and precision_ms:
            detail += f' (±{precision_ms} ms)'
        lines.append(detail)
    note = '\n'.join(lines).strip()
    return note or None


def _recent_results(limit=10):
    """Posledné merania zo stopiek viditeľné pre prihláseného (admin všetky)."""
    if g.user is None:
        return []
    query = RaceResult.query.filter(RaceResult.source == 'stopky')
    query = scope_query(query, RaceResult)
    return query.order_by(RaceResult.created_at.desc(), RaceResult.id.desc()).limit(limit).all()


def _saved_videos(limit=SAVED_VIDEOS_LIMIT):
    """
    Videá z analýz (šprint aj prekážky), ktoré smie prihlásený vidieť – ponuka
    pre fotobunku zo záznamu. Podáva ich chránená routa /media/<súbor>.
    """
    if g.user is None:
        return []
    items = []
    for model, kind in ((VideoAnalysis, 'šprint'), (HurdleAnalysis, 'prekážky')):
        rows = scope_query(model.query.filter(model.stored_name.isnot(None)), model) \
            .order_by(model.created_at.desc()).limit(limit).all()
        for r in rows:
            items.append({
                'url': f'/media/{r.stored_name}',
                'label': r.label or r.original_name or r.stored_name,
                'kind': kind,
                'created_at': r.created_at,
            })
    items.sort(key=lambda x: x['created_at'], reverse=True)
    return items[:limit]


def _selectable_athletes():
    """Zoznam pre <select> zverenca – len tréner/admin s aspoň jedným zverencom."""
    if g.user is None or not (g.user.is_coach or g.user.is_admin):
        return []
    athletes = visible_athletes(g.user)
    return athletes if len(athletes) > 1 else []


@bp.route('/', strict_slashes=False)
def page():
    return render_template('stopky/index.html',
                           eyebrow='Časomiera', title='Stopky s fotobunkou',
                           disciplines=DISCIPLINES, other_discipline=OTHER_DISCIPLINE,
                           athletes=_selectable_athletes(),
                           saved_videos=_saved_videos(),
                           recent=_recent_results())


@bp.route('/ulozit', methods=['POST'])
@login_required
def save():
    result_s = _parse_seconds(request.form.get('result_s'))
    if result_s is None:
        flash('Nameraný čas sa nedá uložiť – najskôr niečo odmeraj.', 'danger')
        return redirect(url_for('stopky.page'))

    discipline = (request.form.get('discipline') or '').strip()
    if discipline == OTHER_DISCIPLINE:
        discipline = (request.form.get('discipline_custom') or '').strip()[:40]
        if not discipline:
            flash('Pri disciplíne „iné“ napíš, čo sa meralo.', 'danger')
            return redirect(url_for('stopky.page'))
    elif discipline not in DISCIPLINES:
        flash('Vyber disciplínu zo zoznamu.', 'danger')
        return redirect(url_for('stopky.page'))

    # Tréner vyberá zverenca; cudzí (alebo nepotvrdený) zverenec → 403 z require_visible_athlete.
    raw_athlete = (request.form.get('athlete_id') or '').strip()
    athlete = require_visible_athlete(raw_athlete) if raw_athlete else g.user

    stop_mode = (request.form.get('stop_mode') or '').strip()
    precision_ms = request.form.get('precision_ms', type=int)
    if precision_ms is not None and not (1 <= precision_ms <= 1000):
        precision_ms = None
    user_note = (request.form.get('note') or '').strip()[:NOTE_MAX]

    row = RaceResult(user_id=athlete.id, discipline=discipline, result_s=result_s,
                     date=date.today(), source='stopky', created_by=g.user.id,
                     note=_build_note(user_note, _parse_laps(request.form.get('laps')),
                                      stop_mode, precision_ms))
    db.session.add(row)
    db.session.commit()

    who = 'tebe' if athlete.id == g.user.id else f'zverencovi {athlete.display_name}'
    flash(f'Čas {row.result_text} ({discipline}) uložený {who}.', 'success')
    return redirect(url_for('stopky.page'))
