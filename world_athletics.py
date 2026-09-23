"""
Blueprint 'wa' – import osobných rekordov zo stránky World Athletics.

Zatiaľ len kostra (kontraktný endpoint), servisné funkcie dopĺňa ďalšia fáza.
"""
from flask import Blueprint, redirect, url_for, flash

from auth import roles_required, require_visible_athlete

bp = Blueprint('wa', __name__, url_prefix='/wa')


@bp.route('/import/<int:athlete_id>', methods=['POST'])
@roles_required('trener', 'admin')
def import_athlete(athlete_id):
    athlete = require_visible_athlete(athlete_id)
    flash('Import z World Athletics sa pripravuje.', 'info')
    return redirect(url_for('trener.athlete_detail', athlete_id=athlete.id))
