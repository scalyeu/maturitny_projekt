"""
Blueprint 'zverenec' – moje výsledky a osobné rekordy (len na čítanie).

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template, g, request

from auth import login_required, require_visible_athlete

bp = Blueprint('zverenec', __name__, url_prefix='/zverenec')


@bp.route('/vysledky')
@login_required
def results():
    # Tréner / admin si môže pozrieť výsledky zverenca cez ?athlete_id=; zverenec vidí len seba.
    athlete = g.user
    athlete_id = request.args.get('athlete_id', type=int)
    if athlete_id and athlete_id != g.user.id:
        athlete = require_visible_athlete(athlete_id)
    return render_template('zverenec/index.html',
                           eyebrow='Zverenec', title='Výsledky a osobné rekordy',
                           athlete=athlete)
