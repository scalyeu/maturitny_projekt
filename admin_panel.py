"""
Blueprint 'admin_panel' – správa používateľov (len administrátor).

Zatiaľ len kostra (kontraktné endpointy), obsah dopĺňa ďalšia fáza.
"""
from flask import Blueprint, render_template

from models import User
from auth import roles_required

bp = Blueprint('admin_panel', __name__, url_prefix='/admin')


@bp.route('/pouzivatelia')
@roles_required('admin')
def users():
    users_list = User.query.order_by(User.role, User.username).all()
    return render_template('admin/index.html', eyebrow='Administrátor', title='Používatelia',
                           users=users_list)
