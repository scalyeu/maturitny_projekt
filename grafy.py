"""
Blueprint 'grafy' – vývoj výkonnosti v grafoch (Chart.js) + JSON API.

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template

from auth import login_required

bp = Blueprint('grafy', __name__, url_prefix='/grafy')


@bp.route('/')
@login_required
def index():
    return render_template('grafy/index.html', eyebrow='Výkonnosť', title='Grafy')
