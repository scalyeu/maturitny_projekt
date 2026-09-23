"""
Blueprint 'zverenec' – moje výsledky z pretekov a osobné rekordy.

Stránka je určená len zverencovi (tréner/admin výsledky spravujú v detaile
zverenca v blueprinte 'trener'). Okrem výsledkov tu zverenec rieši vzťah
s trénerom: potvrdí alebo odmietne žiadosť trénera a môže sa od trénera odpojiť.
Tréner údaje zverenca vidí až po potvrdení.

Formátovanie časov a validácia formulára sú zdieľané s trener.py, aby obe
stránky ukazovali rovnaké hodnoty.
"""
from functools import wraps

from flask import Blueprint, render_template, g, request, redirect, url_for, flash, abort

from models import db, User, RaceResult
from auth import login_required
from trener import format_result, parse_result_form, result_page_context  # noqa: F401  (format_result aj pre filter)

bp = Blueprint('zverenec', __name__, url_prefix='/zverenec')


def athlete_only(view):
    """Len pre rolu zverenec. Tréner/admin sa presmeruje tam, kde výsledky spravuje."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user.is_athlete:
            # Starý odkaz s ?athlete_id= vedie rovno na detail daného zverenca.
            athlete_id = request.args.get('athlete_id', type=int)
            if athlete_id:
                return redirect(url_for('trener.athlete_detail', athlete_id=athlete_id))
            flash('Výsledky zverencov spravuješ v ich detaile.', 'info')
            return redirect(url_for('trener.athletes'))
        return view(*args, **kwargs)
    return login_required(wrapped)


def _own_result_or_abort(result_id):
    """Výsledok smie zverenec mazať len ak je jeho – id z URL samo o sebe nestačí."""
    row = db.session.get(RaceResult, result_id)
    if row is None:
        abort(404)
    if row.user_id != g.user.id:
        abort(403)
    return row


# -----------------------------
# Výsledky a osobné rekordy
# -----------------------------
@bp.route('/vysledky')
@athlete_only
def results():
    ctx = result_page_context(g.user)
    ctx['coach'] = g.user.coach
    ctx['coach_pending'] = g.user.coach_id is not None and not g.user.coach_confirmed
    return render_template('zverenec/vysledky.html', **ctx)


@bp.route('/vysledky', methods=['POST'])
@athlete_only
def add_result():
    data, errors = parse_result_form(request.form)
    if errors:
        for e in errors:
            flash(e, 'danger')
    else:
        db.session.add(RaceResult(user_id=g.user.id, created_by=g.user.id, source='manual', **data))
        db.session.commit()
        flash(f'Výsledok {data["discipline"]} {format_result(data["result_s"])} uložený.', 'success')
    return redirect(url_for('zverenec.results') + '#vysledky')


@bp.route('/vysledky/<int:result_id>/zmazat', methods=['POST'])
@athlete_only
def delete_result(result_id):
    row = _own_result_or_abort(result_id)
    db.session.delete(row)
    db.session.commit()
    flash('Výsledok bol zmazaný.', 'success')
    return redirect(url_for('zverenec.results') + '#vysledky')


# -----------------------------
# Vzťah s trénerom (súhlas zverenca)
# -----------------------------
@bp.route('/trener/prijat', methods=['POST'])
@athlete_only
def accept_coach():
    if g.user.coach_id is None:
        flash('Nemáš žiadnu čakajúcu žiadosť od trénera.', 'info')
    elif g.user.coach_confirmed:
        flash('Vzťah s trénerom je už potvrdený.', 'info')
    else:
        g.user.coach_confirmed = True
        db.session.commit()
        coach = db.session.get(User, g.user.coach_id)
        flash(f'Tréner {coach.display_name if coach else ""} teraz vidí tvoje tréningy, výsledky a analýzy.', 'success')
    return redirect(url_for('zverenec.results'))


@bp.route('/trener/odmietnut', methods=['POST'])
@athlete_only
def reject_coach():
    if g.user.coach_id is None or g.user.coach_confirmed:
        flash('Nemáš žiadnu čakajúcu žiadosť od trénera.', 'info')
    else:
        g.user.coach_id = None
        g.user.coach_confirmed = False
        db.session.commit()
        flash('Žiadosť trénera bola odmietnutá.', 'success')
    return redirect(url_for('zverenec.results'))


@bp.route('/trener/odpojit', methods=['POST'])
@athlete_only
def leave_coach():
    if g.user.coach_id is None:
        flash('Nemáš trénera, od ktorého by si sa odpojil.', 'info')
    else:
        g.user.coach_id = None
        g.user.coach_confirmed = False
        db.session.commit()
        flash('Odpojil si sa od trénera. Tvoje údaje už nevidí.', 'success')
    return redirect(url_for('zverenec.results'))
