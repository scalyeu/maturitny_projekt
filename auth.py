"""
Prihlasovanie, registrácia a prístupové práva.

Blueprint 'auth' + pomocné funkcie, ktoré používajú všetky ostatné moduly:
  - g.user                      prihlásený používateľ (None, ak nikto)
  - login_required              presmeruje na prihlásenie (pre /api/* vráti 401 JSON)
  - roles_required(*roles)      403, ak rola nie je povolená (admin prejde všade)
  - athlete_ids_visible_to()    množina id používateľov, ktorých údaje smie vidieť
  - require_visible_athlete()   načíta zverenca alebo skončí 403
"""
import time
import re
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, g, abort, jsonify)
from werkzeug.local import LocalProxy

from models import db, User, ROLE_LABELS

bp = Blueprint('auth', __name__)

USERNAME_RE = re.compile(r'^[a-z0-9_.-]{3,32}$')
PASSWORD_MIN = 6
# Role, ktoré si používateľ môže vybrať pri registrácii. 'admin' nikdy z formulára.
SELF_REGISTER_ROLES = ('zverenec', 'trener')

# `from auth import current_user` – v kóde funguje ako g.user (current_user.id …).
current_user = LocalProxy(lambda: g.get('user'))


# -----------------------------
# Načítanie používateľa
# -----------------------------
@bp.before_app_request
def load_user():
    g.user = None
    uid = session.get('user_id')
    if uid is not None:
        g.user = db.session.get(User, uid)
        if g.user is None:
            # Účet medzičasom zmizol (admin ho zmazal) – zahodíme starú session.
            session.clear()

    # Predvolený admin/admin sa nesmie používať – kým si heslo nezmení, nepustíme ho ďalej.
    if g.user is not None and g.user.must_change_password:
        allowed = {'auth.change_password', 'auth.logout', 'static'}
        if request.endpoint and request.endpoint not in allowed:
            if _wants_json():
                # JSON volania nemajú kam presmerovať – vrátia jasnú chybu namiesto tichého prechodu.
                return jsonify({'error': 'Najskôr si zmeň predvolené heslo.'}), 403
            flash('Najskôr si zmeň predvolené heslo.', 'warning')
            return redirect(url_for('auth.change_password'))


@bp.app_context_processor
def inject_user():
    return {'current_user': g.get('user'), 'ROLE_LABELS': ROLE_LABELS}


# -----------------------------
# Dekorátory
# -----------------------------
def _wants_json():
    # JSON cesty majú aj moduly (/grafy/api/…, /plan/api/…), nie len /api/… v app.py;
    # fetch() volania sa navyše hlásia cez Accept, takže im nemá zmysel vracať HTML prihlásenie.
    path = request.path
    if path.startswith('/api/') or path.endswith('/api') or '/api/' in path:
        return True
    accept = request.headers.get('Accept', '')
    return 'application/json' in accept and 'text/html' not in accept


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get('user') is None:
            if _wants_json():
                return jsonify({'error': 'Najskôr sa prihlás.'}), 401
            flash('Najskôr sa prihlás.', 'warning')
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    """Povolí prístup len daným rolám; admin prejde vždy."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = g.get('user')
            if user is None:
                if _wants_json():
                    return jsonify({'error': 'Najskôr sa prihlás.'}), 401
                flash('Najskôr sa prihlás.', 'warning')
                return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
            if not user.is_admin and user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# -----------------------------
# Viditeľnosť údajov
# -----------------------------
def athlete_ids_visible_to(user):
    """
    Koho údaje smie používateľ vidieť:
      zverenec → len seba; tréner → seba + svojich zverencov; admin → všetkých.
    Rola sa kontroluje, aby bývalý tréner (preradený na zverenca) nevidel starých zverencov.
    """
    if user is None:
        return set()
    if user.is_admin:
        return {row[0] for row in db.session.query(User.id).all()}
    ids = {user.id}
    if user.is_coach:
        ids |= {row[0] for row in db.session.query(User.id)
                .filter(User.coach_id == user.id, User.coach_confirmed.is_(True)).all()}
    return ids


def visible_athletes(user):
    """Zoznam User objektov pre <select> zverenca (tréner: seba + zverenci; admin: všetci zverenci)."""
    if user is None:
        return []
    if user.is_admin:
        others = User.query.filter(User.role == 'zverenec', User.id != user.id) \
                           .order_by(User.username).all()
        return [user] + others
    if user.is_coach:
        others = User.query.filter_by(coach_id=user.id, coach_confirmed=True).order_by(User.username).all()
        return [user] + others
    return [user]


def require_visible_athlete(athlete_id):
    """Vráti User s daným id, alebo skončí 403 (nie je viditeľný) / 404 (neexistuje)."""
    user = g.get('user')
    if user is None:
        abort(403)
    try:
        athlete_id = int(athlete_id)
    except (TypeError, ValueError):
        abort(404)
    if athlete_id not in athlete_ids_visible_to(user):
        abort(403)
    athlete = db.session.get(User, athlete_id)
    if athlete is None:
        abort(404)
    return athlete


def row_visible(row_user_id, user=None):
    """
    Smie `user` vidieť riadok s vlastníkom `row_user_id`?
    Staré riadky bez vlastníka (NULL) vidí len admin.
    """
    user = user if user is not None else g.get('user')
    if user is None:
        return False
    if row_user_id is None:
        return user.is_admin
    return row_user_id in athlete_ids_visible_to(user)


def scope_query(query, model, user=None):
    """Obmedzí dotaz na riadky viditeľné pre používateľa (admin bez filtra, vidí aj NULL)."""
    user = user if user is not None else g.get('user')
    if user is None:
        return query.filter(db.false())
    if user.is_admin:
        return query
    return query.filter(model.user_id.in_(athlete_ids_visible_to(user)))


# -----------------------------
# Pomocné
# -----------------------------
def _safe_next(target):
    """Len relatívne cesty v rámci appky – inak by /prihlasenie?next=https://… bolo open redirect."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/') or target.startswith('//'):
        return None
    return target


def _login_session(user):
    # Nová session pri prihlásení (žiadne zvyšky po predchádzajúcom používateľovi, ani Strava kľúče).
    session.clear()
    session['user_id'] = user.id
    session.permanent = False


def _default_admin_pending():
    """Ukáž nápovedu admin/admin len kým si predvolený admin heslo nezmenil."""
    return User.query.filter_by(username='admin', must_change_password=True).first() is not None


# -----------------------------
# Routy
# -----------------------------
# Neúspešné pokusy o prihlásenie: (meno, IP) -> časy pokusov za posledných LOGIN_WINDOW_S sekúnd.
# Beží v pamäti – po reštarte sa zabudne, na školský projekt to stačí a nepotrebuje ďalšiu tabuľku.
_failed_logins = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 60


def _login_throttled(key):
    now = time.time()
    attempts = [t for t in _failed_logins.get(key, []) if now - t < LOGIN_WINDOW_S]
    _failed_logins[key] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def _note_failed_login(key):
    _failed_logins.setdefault(key, []).append(time.time())



@bp.route('/prihlasenie', methods=['GET', 'POST'])
def login():
    if g.user is not None and request.method == 'GET':
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''
        key = (username, request.remote_addr or '?')
        if _login_throttled(key):
            flash('Príliš veľa neúspešných pokusov. Skús to znova o minútu.', 'danger')
            return render_template('auth/login.html', username=username,
                                   show_admin_hint=_default_admin_pending()), 429
        user = User.query.filter_by(username=username).first() if username else None
        if user is None or not user.check_password(password):
            _note_failed_login(key)
            flash('Nesprávne meno alebo heslo.', 'danger')
            return render_template('auth/login.html', username=username,
                                   show_admin_hint=_default_admin_pending()), 401

        _failed_logins.pop(key, None)
        _login_session(user)
        flash(f'Vitaj, {user.display_name}.', 'success')
        nxt = _safe_next(request.form.get('next') or request.args.get('next'))
        return redirect(nxt or url_for('index'))

    return render_template('auth/login.html', username='',
                           show_admin_hint=_default_admin_pending())


@bp.route('/odhlasenie')
def logout():
    session.clear()
    session.modified = True
    flash('Bol si odhlásený.', 'success')
    return redirect(url_for('index'))


@bp.route('/registracia', methods=['GET', 'POST'])
def register():
    if g.user is not None and request.method == 'GET':
        return redirect(url_for('index'))

    form = {'username': '', 'full_name': '', 'role': 'zverenec'}
    if request.method == 'POST':
        form['username'] = (request.form.get('username') or '').strip().lower()
        form['full_name'] = (request.form.get('full_name') or '').strip()[:120]
        form['role'] = (request.form.get('role') or 'zverenec').strip()
        password = request.form.get('password') or ''
        password2 = request.form.get('password2') or ''

        errors = []
        if not USERNAME_RE.match(form['username']):
            errors.append('Používateľské meno musí mať 3–32 znakov: malé písmená, číslice, bodka, pomlčka alebo podčiarkovník.')
        if len(password) < PASSWORD_MIN:
            errors.append(f'Heslo musí mať aspoň {PASSWORD_MIN} znakov.')
        if password != password2:
            errors.append('Heslá sa nezhodujú.')
        if form['role'] not in SELF_REGISTER_ROLES:
            errors.append('Neplatná rola.')
        if not errors and User.query.filter_by(username=form['username']).first():
            errors.append('Toto používateľské meno je už obsadené.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('auth/register.html', form=form, roles=SELF_REGISTER_ROLES), 400

        user = User(username=form['username'], role=form['role'],
                    full_name=form['full_name'] or None)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        _login_session(user)
        if user.is_athlete:
            flash(f'Účet vytvorený. Povedz trénerovi svoje používateľské meno „{user.username}“, aby ťa pridal medzi zverencov.', 'success')
        else:
            flash('Účet trénera vytvorený. Zverencov pridáš v sekcii Zverenci.', 'success')
        return redirect(url_for('index'))

    return render_template('auth/register.html', form=form, roles=SELF_REGISTER_ROLES)


@bp.route('/zmena-hesla', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password') or ''
        new = request.form.get('new_password') or ''
        new2 = request.form.get('new_password2') or ''

        errors = []
        if not g.user.check_password(current):
            errors.append('Súčasné heslo nie je správne.')
        if len(new) < PASSWORD_MIN:
            errors.append(f'Nové heslo musí mať aspoň {PASSWORD_MIN} znakov.')
        if new != new2:
            errors.append('Nové heslá sa nezhodujú.')
        if not errors and g.user.check_password(new):
            errors.append('Nové heslo musí byť iné než súčasné.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('auth/change_password.html'), 400

        g.user.set_password(new)
        g.user.must_change_password = False
        db.session.commit()
        flash('Heslo bolo zmenené.', 'success')
        return redirect(url_for('index'))

    return render_template('auth/change_password.html')


# -----------------------------
# Chybové stránky
# -----------------------------
@bp.app_errorhandler(403)
def forbidden(_e):
    if _wants_json():
        return jsonify({'error': 'Na toto nemáš oprávnenie.'}), 403
    return render_template('403.html'), 403
