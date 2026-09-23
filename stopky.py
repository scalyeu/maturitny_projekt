"""
Blueprint 'stopky' – stopky s kamerou a virtuálnou fotobunkou (verejné, ukladanie len prihláseným).

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template

bp = Blueprint('stopky', __name__, url_prefix='/stopky')


@bp.route('/')
def page():
    return render_template('stopky/index.html', eyebrow='Časomiera', title='Stopky s fotobunkou')
