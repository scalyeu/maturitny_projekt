"""
Blueprint 'planovanie' – kalendár: tréner plánuje tréningy, zverenec ich označuje za splnené.

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template

from auth import login_required

bp = Blueprint('planovanie', __name__, url_prefix='/plan')


@bp.route('/')
@login_required
def index():
    return render_template('planovanie/index.html', eyebrow='Kalendár', title='Plán tréningov')
