"""
Blueprint 'trener' – správa zverencov, výsledky z pretekov a osobné rekordy.

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template, g

from models import db, User
from auth import roles_required, require_visible_athlete, visible_athletes

bp = Blueprint('trener', __name__, url_prefix='/trener')


@bp.route('/zverenci')
@roles_required('trener', 'admin')
def athletes():
    athletes_list = [u for u in visible_athletes(g.user) if u.id != g.user.id]
    return render_template('trener/index.html',
                           eyebrow='Tréner', title='Zverenci', athletes=athletes_list)


@bp.route('/zverenci/<int:athlete_id>')
@roles_required('trener', 'admin')
def athlete_detail(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    return render_template('trener/index.html',
                           eyebrow='Tréner · zverenec', title=athlete.display_name,
                           athlete=athlete, athletes=None)
