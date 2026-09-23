"""
Blueprint 'admin_panel' – správa používateľov a prehľad systému (len administrátor).

Routy:
  /admin/pouzivatelia                       zoznam účtov, hľadanie, filter podľa roly
  /admin/pouzivatelia/novy                  nový účet
  /admin/pouzivatelia/<id>/upravit          meno, rola, tréner, WA id, vynútená zmena hesla
  /admin/pouzivatelia/<id>/reset-hesla      POST – nové heslo, používateľ si ho musí zmeniť
  /admin/pouzivatelia/<id>/zmazat           GET potvrdenie, POST zmazanie účtu aj jeho dát
  /admin/prehlad                            počty, posledné registrácie, disk, verzie, .env

Pravidlá, ktoré chránia systém pred zamknutím: posledného admina nemožno
zmazať ani preradiť, sám seba admin nezmaže a vlastnú rolu si nezníži.
"""
import os
import sys
import platform
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, abort, current_app)
from sqlalchemy import func, or_

from models import (db, User, RaceResult, PlannedTraining, BiometricLog, TrainingLog,
                    StravaToken, ChatMessage, UserProfile, VideoAnalysis, HurdleAnalysis,
                    ROLES, ROLE_LABELS)
from auth import roles_required, USERNAME_RE, PASSWORD_MIN

bp = Blueprint('admin_panel', __name__, url_prefix='/admin')

# Poradie rolí v zoznamoch a filtroch (admin navrchu).
ROLE_ORDER = ('admin', 'trener', 'zverenec')


# -----------------------------
# Pomocné
# -----------------------------
def _admin_count():
    return User.query.filter_by(role='admin').count()


def _coaches():
    """Tréneri pre <select> pri priraďovaní zverenca."""
    return User.query.filter_by(role='trener').order_by(User.username).all()


def _count_by_user(model, column=None):
    """{user_id: počet riadkov} – jeden GROUP BY namiesto dotazu na každý riadok tabuľky."""
    column = column if column is not None else model.user_id
    rows = db.session.query(column, func.count(model.id)).group_by(column).all()
    return {uid: n for uid, n in rows if uid is not None}


def _user_data_counts(user):
    """Koľko riadkov v jednotlivých tabuľkách patrí používateľovi – pre potvrdenie zmazania a úpravu."""
    return {
        'trainings': TrainingLog.query.filter_by(user_id=user.id).count(),
        'results': RaceResult.query.filter_by(user_id=user.id).count(),
        'videos': VideoAnalysis.query.filter_by(user_id=user.id).count(),
        'hurdles': HurdleAnalysis.query.filter_by(user_id=user.id).count(),
        'biometrics': BiometricLog.query.filter_by(user_id=user.id).count(),
        'chat': ChatMessage.query.filter_by(user_id=user.id).count(),
        'planned_as_athlete': PlannedTraining.query.filter_by(athlete_id=user.id).count(),
        'planned_as_coach': PlannedTraining.query.filter_by(coach_id=user.id).count(),
        'strava': StravaToken.query.filter_by(user_id=user.id).count(),
        'profile': UserProfile.query.filter_by(user_id=user.id).count(),
        'athletes': User.query.filter_by(coach_id=user.id).count(),
    }


def _video_dirs():
    """
    Kde ležia nahraté videá. Hlavný adresár je instance/videos (app.VIDEO_DIR);
    blueprint nesmie importovať app, preto cestu skladá rovnako z instance_path.
    Starší adresár static/uploads/videos sa kontroluje tiež – mohol ostať po migrácii.
    """
    dirs = [os.path.join(current_app.instance_path, 'videos')]
    legacy = os.path.join(current_app.root_path, 'static', 'uploads', 'videos')
    if os.path.isdir(legacy):
        dirs.append(legacy)
    return dirs


def _media_names(row):
    """Súbory patriace k analýze: video, prekryv a kľúčové snímky z result_json."""
    import json
    names = [row.stored_name, row.overlay_name]
    try:
        for k in (json.loads(row.result_json or '{}').get('keyframes') or []):
            names.append(k.get('file'))
    except Exception:
        pass
    return [os.path.basename(n) for n in names if n]


def _remove_media_files(names):
    """Zmaže súbory až po úspešnom commite – pri chybe DB by inak zmizli videá, ktoré v DB ostali."""
    removed = 0
    for name in names:
        for d in _video_dirs():
            path = os.path.join(d, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return removed


def _validate_username(username, exclude_id=None):
    """Rovnaké pravidlá ako auth.register; vráti text chyby alebo None."""
    if not USERNAME_RE.match(username or ''):
        return 'Používateľské meno musí mať 3–32 znakov: malé písmená, číslice, bodka, pomlčka alebo podčiarkovník.'
    q = User.query.filter_by(username=username)
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    if q.first() is not None:
        return 'Toto používateľské meno je už obsadené.'
    return None


def _clean_wa_id(raw):
    """World Athletics id sú číslice; prázdne = žiadne. Vráti (hodnota, chyba)."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    if not raw.isdigit() or len(raw) > 32:
        return None, 'World Athletics ID musí byť číslo (max. 32 číslic).'
    return raw, None


def _coach_from_form(role, exclude_id=None):
    """Tréner z formulára – len pre zverenca a len existujúci účet s rolou tréner."""
    if role != 'zverenec':
        return None, None
    coach_id = request.form.get('coach_id', type=int)
    if not coach_id:
        return None, None
    if exclude_id is not None and coach_id == exclude_id:
        return None, 'Zverenec nemôže byť sám sebe trénerom.'
    coach = db.session.get(User, coach_id)
    if coach is None or not coach.is_coach:
        return None, 'Vybraný tréner neexistuje alebo nemá rolu tréner.'
    return coach, None


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ('B', 'kB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit in ('B', 'kB') else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def _dir_usage(path):
    """(počet súborov, bajty) – len prvá úroveň, videá sa neukladajú do podadresárov."""
    files, total = 0, 0
    try:
        for name in os.listdir(path):
            p = os.path.join(path, name)
            if os.path.isfile(p):
                files += 1
                total += os.path.getsize(p)
    except OSError:
        pass
    return files, total


def _db_file_info():
    """Cesta a veľkosť SQLite súboru; pri inej databáze len URI bez hesla."""
    try:
        url = db.engine.url
    except Exception:
        return {'path': '—', 'size': None}
    if url.get_backend_name() != 'sqlite' or not url.database:
        return {'path': url.render_as_string(hide_password=True), 'size': None}
    path = url.database
    if not os.path.isabs(path):
        path = os.path.join(current_app.instance_path, path)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    return {'path': path, 'size': size}


def _mediapipe_status():
    """Import video_analysis ťahá OpenCV a MediaPipe – app.py ho už načítal, takže je to lacné."""
    try:
        import video_analysis as va
        ok, msg = va.dependencies_ok()
        return {'ok': bool(ok), 'text': msg}
    except Exception as e:  # noqa: BLE001 – chýbajúca knižnica nemá zhodiť prehľad
        return {'ok': False, 'text': f'Modul sa nedá načítať: {e}'}


def _env_flag(name):
    """Len áno/nie – hodnoty kľúčov sa nikdy nevypisujú."""
    val = os.getenv(name, '')
    return bool(val) and not val.startswith('gsk_...')


# -----------------------------
# Zoznam
# -----------------------------
@bp.route('/pouzivatelia')
@roles_required('admin')
def users():
    q = (request.args.get('q') or '').strip()
    role = (request.args.get('rola') or '').strip()
    if role not in ROLES:
        role = ''

    query = User.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(User.username.ilike(like), User.full_name.ilike(like)))
    if role:
        query = query.filter(User.role == role)
    users_list = query.order_by(User.username).all()
    # Admin navrchu, potom tréneri, potom zverenci – v rámci roly podľa mena.
    users_list.sort(key=lambda u: (ROLE_ORDER.index(u.role) if u.role in ROLE_ORDER else 9, u.username))

    counts = {
        'trainings': _count_by_user(TrainingLog),
        'results': _count_by_user(RaceResult),
        'videos': _count_by_user(VideoAnalysis),
        'hurdles': _count_by_user(HurdleAnalysis),
        'athletes': _count_by_user(User, User.coach_id),
    }
    pending = {uid: n for uid, n in db.session.query(User.coach_id, func.count(User.id))
               .filter(User.coach_id.isnot(None), User.coach_confirmed.is_(False))
               .group_by(User.coach_id).all()}
    role_counts = dict(db.session.query(User.role, func.count(User.id)).group_by(User.role).all())

    return render_template('admin/users.html',
                           users=users_list, q=q, role=role, counts=counts, pending=pending,
                           role_counts=role_counts, total=User.query.count(),
                           admin_count=_admin_count(), ROLE_ORDER=ROLE_ORDER)


# -----------------------------
# Nový účet
# -----------------------------
@bp.route('/pouzivatelia/novy', methods=['GET', 'POST'])
@roles_required('admin')
def new_user():
    form = {'username': '', 'full_name': '', 'role': 'zverenec', 'coach_id': '',
            'world_athletics_id': '', 'must_change_password': True}

    if request.method == 'POST':
        form['username'] = (request.form.get('username') or '').strip().lower()
        form['full_name'] = (request.form.get('full_name') or '').strip()[:120]
        form['role'] = (request.form.get('role') or '').strip()
        form['coach_id'] = request.form.get('coach_id') or ''
        form['world_athletics_id'] = (request.form.get('world_athletics_id') or '').strip()
        form['must_change_password'] = bool(request.form.get('must_change_password'))
        password = request.form.get('password') or ''

        errors = []
        err = _validate_username(form['username'])
        if err:
            errors.append(err)
        if len(password) < PASSWORD_MIN:
            errors.append(f'Heslo musí mať aspoň {PASSWORD_MIN} znakov.')
        if form['role'] not in ROLES:
            errors.append('Neplatná rola.')
        coach, err = _coach_from_form(form['role'])
        if err:
            errors.append(err)
        wa_id, err = _clean_wa_id(form['world_athletics_id'])
        if err:
            errors.append(err)

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/form.html', form=form, user=None,
                                   coaches=_coaches(), roles=ROLE_ORDER), 400

        user = User(username=form['username'], role=form['role'],
                    full_name=form['full_name'] or None,
                    world_athletics_id=wa_id,
                    must_change_password=form['must_change_password'])
        user.set_password(password)
        if coach is not None:
            # Priradenie adminom platí ako potvrdený súhlas – zverenec ho nemusí prijímať.
            user.coach_id = coach.id
            user.coach_confirmed = True
        db.session.add(user)
        db.session.commit()
        flash(f'Účet „{user.username}“ ({user.role_label}) bol vytvorený.', 'success')
        return redirect(url_for('admin_panel.users'))

    return render_template('admin/form.html', form=form, user=None,
                           coaches=_coaches(), roles=ROLE_ORDER)


# -----------------------------
# Úprava
# -----------------------------
@bp.route('/pouzivatelia/<int:user_id>/upravit', methods=['GET', 'POST'])
@roles_required('admin')
def edit_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = {'username': user.username, 'full_name': user.full_name or '', 'role': user.role,
            'coach_id': str(user.coach_id or ''), 'world_athletics_id': user.world_athletics_id or '',
            'must_change_password': user.must_change_password}

    if request.method == 'POST':
        form['full_name'] = (request.form.get('full_name') or '').strip()[:120]
        form['role'] = (request.form.get('role') or '').strip()
        form['coach_id'] = request.form.get('coach_id') or ''
        form['world_athletics_id'] = (request.form.get('world_athletics_id') or '').strip()
        form['must_change_password'] = bool(request.form.get('must_change_password'))

        errors = []
        if form['role'] not in ROLES:
            errors.append('Neplatná rola.')
        elif user.is_admin and form['role'] != 'admin':
            if user.id == g.user.id:
                errors.append('Vlastnú rolu administrátora si nemôžeš odobrať – urob to z iného admin účtu.')
            elif _admin_count() <= 1:
                errors.append('Toto je posledný administrátor – najprv povýš iný účet na admina.')
        coach, err = _coach_from_form(form['role'], exclude_id=user.id)
        if err:
            errors.append(err)
        wa_id, err = _clean_wa_id(form['world_athletics_id'])
        if err:
            errors.append(err)

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/form.html', form=form, user=user, coaches=_coaches(),
                                   roles=ROLE_ORDER, data=_user_data_counts(user)), 400

        notes = []
        # Bývalý tréner už nemá vidieť údaje zverencov – odpojíme ich (aj čakajúce žiadosti).
        if user.is_coach and form['role'] != 'trener':
            detached = User.query.filter_by(coach_id=user.id).all()
            for a in detached:
                a.coach_id = None
                a.coach_confirmed = False
            if detached:
                notes.append(f'odpojených zverencov: {len(detached)}')

        user.full_name = form['full_name'] or None
        user.role = form['role']
        user.world_athletics_id = wa_id
        user.must_change_password = form['must_change_password']

        if user.role == 'zverenec':
            if coach is not None:
                if user.coach_id != coach.id or not user.coach_confirmed:
                    notes.append(f'tréner: {coach.username} (potvrdené)')
                user.coach_id = coach.id
                user.coach_confirmed = True     # priradenie adminom = súhlas
            else:
                if user.coach_id is not None:
                    notes.append('tréner odpojený')
                user.coach_id = None
                user.coach_confirmed = False
        else:
            # Tréner ani admin nemajú vlastného trénera.
            user.coach_id = None
            user.coach_confirmed = False

        db.session.commit()
        msg = f'Účet „{user.username}“ bol upravený.'
        if notes:
            msg += ' ' + ', '.join(notes).capitalize() + '.'
        flash(msg, 'success')
        return redirect(url_for('admin_panel.users'))

    return render_template('admin/form.html', form=form, user=user, coaches=_coaches(),
                           roles=ROLE_ORDER, data=_user_data_counts(user))


# -----------------------------
# Reset hesla
# -----------------------------
@bp.route('/pouzivatelia/<int:user_id>/reset-hesla', methods=['POST'])
@roles_required('admin')
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    new = request.form.get('new_password') or ''
    if len(new) < PASSWORD_MIN:
        flash(f'Nové heslo musí mať aspoň {PASSWORD_MIN} znakov.', 'danger')
        return redirect(url_for('admin_panel.edit_user', user_id=user.id) + '#reset-hesla')

    user.set_password(new)
    # Dočasné heslo pozná admin – používateľ si ho pri prvom prihlásení musí zmeniť.
    user.must_change_password = True
    db.session.commit()
    flash(f'Heslo pre „{user.username}“ bolo nastavené. Pri ďalšom prihlásení si ho musí zmeniť.', 'success')
    if user.id == g.user.id:
        # Admin si resetol vlastné heslo – gate v auth ho hneď pošle na zmenu.
        return redirect(url_for('auth.change_password'))
    return redirect(url_for('admin_panel.users'))


# -----------------------------
# Zmazanie
# -----------------------------
def _delete_blockers(user):
    """Prečo sa účet nedá zmazať (None = dá sa)."""
    if user.id == g.user.id:
        return 'Vlastný účet si nemôžeš zmazať.'
    if user.is_admin and _admin_count() <= 1:
        return 'Toto je posledný administrátor – nedá sa zmazať.'
    attached = User.query.filter_by(coach_id=user.id).count()
    if attached:
        return (f'Tréner má ešte {attached} zverencov. Najprv ich odpoj alebo preraď '
                f'k inému trénerovi v úprave zverenca.')
    return None


@bp.route('/pouzivatelia/<int:user_id>/zmazat', methods=['GET', 'POST'])
@roles_required('admin')
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    blocker = _delete_blockers(user)
    data = _user_data_counts(user)

    if request.method == 'GET':
        # Potvrdzovacia stránka bez JS – ukáže, čo všetko so sebou účet vezme.
        return render_template('admin/delete.html', user=user, data=data, blocker=blocker)

    if blocker:
        flash(blocker, 'danger')
        return redirect(url_for('admin_panel.users'))
    if request.form.get('confirm') != user.username:
        flash('Na potvrdenie treba opísať používateľské meno.', 'danger')
        return redirect(url_for('admin_panel.delete_user', user_id=user.id))

    username = user.username
    media = []
    counts = {}

    # Súbory videí zmažeme až po commite – najprv si zapamätáme názvy.
    for model, key in ((VideoAnalysis, 'videos'), (HurdleAnalysis, 'hurdles')):
        rows = model.query.filter_by(user_id=user.id).all()
        for r in rows:
            media.extend(_media_names(r))
            db.session.delete(r)
        counts[key] = len(rows)

    # Plán ako tréner (iní zverenci) aj ako zverenec – pred TrainingLog kvôli FK training_log_id.
    counts['planned_as_coach'] = PlannedTraining.query.filter(
        PlannedTraining.coach_id == user.id, PlannedTraining.athlete_id != user.id).delete(synchronize_session=False)
    counts['planned_as_athlete'] = PlannedTraining.query.filter_by(athlete_id=user.id).delete(synchronize_session=False)
    counts['trainings'] = TrainingLog.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    counts['biometrics'] = BiometricLog.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    counts['chat'] = ChatMessage.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    counts['profile'] = UserProfile.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    counts['results'] = RaceResult.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    counts['strava'] = StravaToken.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    # Výsledky iných atlétov, ktoré tento používateľ zapísal, ostávajú – len bez autora.
    RaceResult.query.filter_by(created_by=user.id).update({'created_by': None}, synchronize_session=False)

    db.session.delete(user)
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001 – jedna transakcia: buď všetko, alebo nič
        db.session.rollback()
        flash(f'Zmazanie zlyhalo, nič sa nezmenilo: {e}', 'danger')
        return redirect(url_for('admin_panel.users'))

    files_removed = _remove_media_files(media)

    parts = []
    labels = (('trainings', 'tréningy'), ('results', 'výsledky'), ('videos', 'videá'),
              ('hurdles', 'prekážky'), ('planned_as_athlete', 'plán'),
              ('planned_as_coach', 'plán pre zverencov'), ('biometrics', 'biometrika'),
              ('chat', 'AI správy'), ('profile', 'profil'), ('strava', 'Strava'))
    for key, label in labels:
        if counts.get(key):
            parts.append(f'{label} {counts[key]}')
    if files_removed:
        parts.append(f'súbory videí {files_removed}')
    msg = f'Účet „{username}“ bol zmazaný.'
    if parts:
        msg += ' Odstránené: ' + ', '.join(parts) + '.'
    flash(msg, 'success')
    return redirect(url_for('admin_panel.users'))


# -----------------------------
# Prehľad systému
# -----------------------------
@bp.route('/prehlad')
@roles_required('admin')
def overview():
    role_counts = dict(db.session.query(User.role, func.count(User.id)).group_by(User.role).all())
    totals = {
        'users': User.query.count(),
        'results': RaceResult.query.count(),
        'planned': PlannedTraining.query.count(),
        'planned_done': PlannedTraining.query.filter_by(completed=True).count(),
        'trainings': TrainingLog.query.count(),
        'videos': VideoAnalysis.query.count(),
        'hurdles': HurdleAnalysis.query.count(),
        'biometrics': BiometricLog.query.count(),
        'chat': ChatMessage.query.count(),
        'strava': StravaToken.query.filter(StravaToken.user_id.isnot(None)).count(),
        'pending_links': User.query.filter(User.coach_id.isnot(None), User.coach_confirmed.is_(False)).count(),
        'must_change': User.query.filter_by(must_change_password=True).count(),
    }
    last_users = User.query.order_by(User.created_at.desc(), User.id.desc()).limit(8).all()

    # Disk: hlavný adresár videí + prípadný starý adresár.
    storage = []
    for d in _video_dirs():
        n, size = _dir_usage(d)
        storage.append({'path': d, 'files': n, 'size': size, 'size_text': _fmt_bytes(size)})
    dbinfo = _db_file_info()
    dbinfo['size_text'] = _fmt_bytes(dbinfo['size']) if dbinfo['size'] is not None else '—'

    try:
        from importlib.metadata import version as _pkg_version
        flask_version = _pkg_version('flask')
        sqlalchemy_version = _pkg_version('sqlalchemy')
    except Exception:  # noqa: BLE001
        flask_version = sqlalchemy_version = '—'

    environment = {
        'python': platform.python_version(),
        'executable': sys.executable,
        'flask': flask_version,
        'sqlalchemy': sqlalchemy_version,
        'platform': f'{platform.system()} {platform.release()}',
        'debug': bool(current_app.debug),
        'mediapipe': _mediapipe_status(),
        'env': [
            ('GROQ_API_KEY', 'AI tréner (Groq)', _env_flag('GROQ_API_KEY')),
            ('STRAVA_CLIENT_ID', 'Strava – client id', _env_flag('STRAVA_CLIENT_ID')),
            ('STRAVA_CLIENT_SECRET', 'Strava – client secret', _env_flag('STRAVA_CLIENT_SECRET')),
            ('SECRET_KEY', 'Tajný kľúč session (.env)', _env_flag('SECRET_KEY')),
        ],
    }

    return render_template('admin/overview.html',
                           role_counts=role_counts, totals=totals, last_users=last_users,
                           storage=storage, dbinfo=dbinfo, environment=environment,
                           ROLE_ORDER=ROLE_ORDER, now=datetime.now())
