import os
import time
import json
import uuid
import threading
import webbrowser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from stravalib.client import Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-for-sprint-predictor-123')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Session sa vymaže keď zatvoríš prehliadač (nie permanent cookie)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)


@app.before_request
def make_session_non_permanent():
    session.permanent = False


# -----------------------------
# Database Models
# -----------------------------
class BiometricLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    hrv = db.Column(db.Float, nullable=False)
    recovery = db.Column(db.Integer, nullable=True)
    rhr = db.Column(db.Integer, nullable=True)


class TrainingLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    training_type = db.Column(db.String(100), nullable=False)
    distance_km = db.Column(db.Float, nullable=True)
    duration_min = db.Column(db.Float, nullable=True)
    duration_sec = db.Column(db.Float, nullable=True)
    intervals_data = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)


class StravaToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    athlete_id = db.Column(db.String(64), nullable=True, index=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    age = db.Column(db.Integer, nullable=True)
    sport = db.Column(db.String(100), nullable=True)
    goal = db.Column(db.String(200), nullable=True)
    height_cm = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class VideoAnalysis(db.Model):
    """Uložený výsledok analýzy techniky z videa."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    original_name = db.Column(db.String(255), nullable=True)
    stored_name = db.Column(db.String(255), nullable=True)
    overlay_name = db.Column(db.String(255), nullable=True)
    label = db.Column(db.String(120), nullable=True)

    fps = db.Column(db.Float, nullable=True)
    duration_s = db.Column(db.Float, nullable=True)
    athlete_height_cm = db.Column(db.Float, nullable=True)

    steps_detected = db.Column(db.Integer, nullable=True)
    contact_ms_mean = db.Column(db.Float, nullable=True)
    contact_ms_sd = db.Column(db.Float, nullable=True)
    flight_ms_mean = db.Column(db.Float, nullable=True)
    cadence_spm = db.Column(db.Float, nullable=True)
    duty_factor_pct = db.Column(db.Float, nullable=True)
    asymmetry_pct = db.Column(db.Float, nullable=True)
    speed_ms = db.Column(db.Float, nullable=True)
    stride_length_m = db.Column(db.Float, nullable=True)

    result_json = db.Column(db.Text, nullable=True)


class HurdleAnalysis(db.Model):
    """Uložený výsledok analýzy prekážkového behu z videa."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    original_name = db.Column(db.String(255), nullable=True)
    stored_name = db.Column(db.String(255), nullable=True)
    overlay_name = db.Column(db.String(255), nullable=True)
    label = db.Column(db.String(120), nullable=True)
    discipline = db.Column(db.String(20), nullable=True)

    fps = db.Column(db.Float, nullable=True)
    duration_s = db.Column(db.Float, nullable=True)
    athlete_height_cm = db.Column(db.Float, nullable=True)

    hurdles_detected = db.Column(db.Integer, nullable=True)
    interval_mean_s = db.Column(db.Float, nullable=True)
    interval_sd_s = db.Column(db.Float, nullable=True)
    steps_pattern = db.Column(db.String(120), nullable=True)
    flight_ms_mean = db.Column(db.Float, nullable=True)
    landing_contact_ms_mean = db.Column(db.Float, nullable=True)
    speed_between_ms_mean = db.Column(db.Float, nullable=True)

    result_json = db.Column(db.Text, nullable=True)


# -----------------------------
# Init
# -----------------------------
def _ensure_columns():
    """
    Jednoduchá migrácia: db.create_all() vie vytvoriť novú tabuľku, ale
    nevie pridať stĺpec do už existujúcej.  Toto doplní chýbajúce stĺpce,
    takže existujúca database.db zostane funkčná aj po aktualizácii.
    """
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    wanted = {'user_profile': [('height_cm', 'FLOAT')]}
    for table, cols in wanted.items():
        if table not in inspector.get_table_names():
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        for name, sql_type in cols:
            if name not in existing:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
                db.session.commit()


with app.app_context():
    db.create_all()
    try:
        _ensure_columns()
    except Exception as _e:
        print('Upozornenie: migrácia stĺpcov zlyhala:', _e)


# -----------------------------
# Strava Config
# -----------------------------
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = (
    os.getenv('STRAVA_REDIRECT_URI')
    or os.getenv('STRAVA_CALLBACK_URL')
    or 'http://localhost:5000/strava/callback'
)


# -----------------------------
# Helpers
# -----------------------------
def get_valid_access_token(athlete_id=None):
    if athlete_id is None:
        tok = session.get('strava_access_token')
        if tok:
            return tok
        raise ValueError("athlete_id required to refresh token")

    token_row = StravaToken.query.filter_by(athlete_id=str(athlete_id)).first()
    if not token_row:
        raise ValueError("no token found for athlete")

    now = int(time.time())
    if token_row.expires_at and token_row.expires_at - 30 > now:
        return token_row.access_token

    if not token_row.refresh_token:
        raise ValueError("no refresh token available")

    resp = requests.post(
        'https://www.strava.com/oauth/token',
        data={
            'client_id': STRAVA_CLIENT_ID,
            'client_secret': STRAVA_CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': token_row.refresh_token
        },
        timeout=10
    )

    if resp.status_code != 200:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"refresh failed: {resp.status_code} {body}")

    new = resp.json()
    token_row.access_token = new.get('access_token')
    token_row.refresh_token = new.get('refresh_token') or token_row.refresh_token
    token_row.expires_at = new.get('expires_at')
    db.session.commit()

    session['strava_access_token'] = token_row.access_token
    session['strava_refresh_token'] = token_row.refresh_token
    session['strava_expires_at'] = token_row.expires_at

    return token_row.access_token


def pace_from_speed(speed):
    if not speed:
        return "-"
    pace_sec = 1000 / float(speed)
    return f"{int(pace_sec // 60)}:{int(pace_sec % 60):02d} /km"


def speed_to_kmh(speed):
    if not speed:
        return "-"
    return f"{float(speed) * 3.6:.1f} km/h"


def swim_pace_from_speed(speed):
    if not speed:
        return "-"
    pace_sec = 100 / float(speed)
    return f"{int(pace_sec // 60)}:{int(pace_sec % 60):02d} /100m"


# -----------------------------
# PR Helpers (Osobáky)
# -----------------------------
def _seconds_to_time(s):
    """Converts seconds to H:MM:SS or MM:SS string."""
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _pace_str(seconds, km):
    """Returns pace string MM:SS /km."""
    if not seconds or not km:
        return "—"
    m, s = divmod(int(seconds / km), 60)
    return f"{m}:{s:02d}"


def get_strava_prs(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}

    # Distance-based ranges for longer races (total activity distance)
    dist_ranges = {
        '5k':       (4.8,  5.2),
        '10k':      (9.7,  10.3),
        'half':     (20.5, 21.5),
        'marathon': (41.5, 42.8),
    }

    # Short races detectable from summary total distance
    short_dist_ranges = {
        '400m': (0.35, 0.45),
        '800m': (0.75, 0.90),
    }

    # Strava best_effort name → our output key
    # NOTE: Strava names 800m as "1/2 mile", half as "Half-Marathon"
    best_effort_keys = {
        '400m':          '400m',
        '1/2 mile':      '800m',
        '5k':            '5k',
        '10k':           '10k',
        'Half-Marathon': 'half',
        'Marathon':      'marathon',
    }

    best = {k: None for k in ['400m', '800m', '5k', '10k', 'half', 'marathon']}

    # 10-minute filesystem cache
    try:
        from pathlib import Path
        import json as _json
        cache_dir = Path('cache')
        cache_dir.mkdir(exist_ok=True)
        athlete_hint = (access_token or '')[-8:]
        cache_file = cache_dir / f'prs_{athlete_hint}.json'
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 1800:
            try:
                with cache_file.open('r', encoding='utf-8') as cf:
                    cached = _json.load(cf)
                for k in best:
                    best[k] = cached.get(k)
                return best
            except Exception:
                pass
    except Exception:
        cache_file = None

    run_activity_ids = []

    page = 1
    while page <= 3:  # max 300 activities from summary
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=15
        )
        try:
            activities = resp.json()
        except Exception:
            break
        if not activities or not isinstance(activities, list):
            break

        for act in activities:
            if act.get("type") not in ("Run", "VirtualRun", "TrailRun"):
                continue

            dist_km = (act.get("distance") or 0) / 1000
            elapsed = act.get("moving_time") or 0
            if elapsed == 0:
                continue

            avg_hr = act.get("average_heartrate")
            cadence = act.get("average_cadence")

            def _make_entry(d_km):
                return {
                    "time":       _seconds_to_time(elapsed),
                    "pace":       _pace_str(elapsed, d_km),
                    "date":       (act.get("start_date_local") or "")[:10],
                    "avg_hr":     round(avg_hr) if avg_hr else None,
                    "elevation":  round(act.get("total_elevation_gain") or 0) or None,
                    "cadence":    round(cadence * 2) if cadence else None,
                    "strava_url": f"https://www.strava.com/activities/{act['id']}",
                    "_elapsed":   elapsed,
                }

            for key, (lo, hi) in dist_ranges.items():
                if lo <= dist_km <= hi:
                    if best[key] is None or elapsed < best[key]['_elapsed']:
                        best[key] = _make_entry(dist_km)

            # Catch short 400m/800m race activities directly from summary
            for key, (lo, hi) in short_dist_ranges.items():
                if lo <= dist_km <= hi:
                    if best[key] is None or elapsed < best[key]['_elapsed']:
                        best[key] = _make_entry(dist_km)

            run_activity_ids.append(act['id'])

        if len(activities) < 100:
            break
        page += 1

    # Fetch best_efforts concurrently for 400m/800m from activity details
    # Limit to 60 most recent runs; fetch in parallel to keep it fast
    def _fetch_detail(act_id):
        try:
            r = requests.get(
                f"https://www.strava.com/api/v3/activities/{act_id}",
                headers=headers,
                timeout=10
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    ids_to_fetch = run_activity_ids[:90]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_detail, aid): aid for aid in ids_to_fetch}
        for future in as_completed(futures):
            detail = future.result()
            if not detail:
                continue
            efforts = detail.get("best_efforts") or []
            act_date = (detail.get("start_date_local") or "")[:10]
            act_url = f"https://www.strava.com/activities/{detail['id']}"
            avg_hr = detail.get("average_heartrate")
            cadence = detail.get("average_cadence")
            elevation = round(detail.get("total_elevation_gain") or 0) or None

            for effort in efforts:
                name = effort.get("name", "")
                if name not in best_effort_keys:
                    continue
                key = best_effort_keys[name]
                elapsed = effort.get("elapsed_time") or 0
                dist_m = effort.get("distance") or 400
                dist_km_e = dist_m / 1000
                if elapsed == 0:
                    continue
                if best[key] is None or elapsed < best[key]['_elapsed']:
                    best[key] = {
                        "time":       _seconds_to_time(elapsed),
                        "pace":       _pace_str(elapsed, dist_km_e),
                        "date":       act_date,
                        "avg_hr":     round(avg_hr) if avg_hr else None,
                        "elevation":  elevation,
                        "cadence":    round(cadence * 2) if cadence else None,
                        "strava_url": act_url,
                        "_elapsed":   elapsed,
                    }

    # Remove internal sort key and save cache
    for k in best:
        if best[k]:
            best[k].pop("_elapsed", None)

    try:
        if cache_file:
            import json as _json2
            with cache_file.open('w', encoding='utf-8') as cf:
                _json2.dump(best, cf)
    except Exception:
        pass

    return best


# -----------------------------
# Routes
# -----------------------------
@app.route('/')
def index():
    recent_logs = BiometricLog.query.order_by(BiometricLog.date.desc()).limit(14).all()

    last_hrv = None
    avg_hrv_7 = None
    last_recovery = None
    last_rhr = None

    if recent_logs:
        last = recent_logs[0]
        last_hrv = last.hrv
        last_recovery = last.recovery
        last_rhr = last.rhr

        hrvs = [l.hrv for l in recent_logs[:7] if l.hrv is not None]
        if hrvs:
            avg_hrv_7 = sum(hrvs) / len(hrvs)

    week_ago = date.today() - timedelta(days=6)
    trainings_week = TrainingLog.query.filter(TrainingLog.date >= week_ago).all()
    weekly_km = sum((t.distance_km or 0.0) for t in trainings_week)

    return render_template(
        'index.html',
        weekly_km=round(weekly_km, 1),
        last_hrv=last_hrv,
        avg_hrv_7=round(avg_hrv_7, 1) if avg_hrv_7 is not None else None,
        last_recovery=last_recovery,
        last_rhr=last_rhr,
        recent_logs=recent_logs[:5],
        last_video=VideoAnalysis.query.order_by(VideoAnalysis.created_at.desc()).first(),
        strava_logged_in=bool(session.get('strava_access_token'))
    )


@app.route('/log', methods=['GET', 'POST'])
def log_data():
    if request.method == 'POST':
        hrv = request.form.get('hrv', type=float)
        recovery = request.form.get('recovery', type=int)
        rhr = request.form.get('rhr', type=int)

        if hrv is not None:
            new_log = BiometricLog(
                hrv=hrv,
                recovery=recovery,
                rhr=rhr
            )
            db.session.add(new_log)
            db.session.commit()
            flash('Záznam uložený', 'success')

        return redirect(url_for('log_data'))

    logs = BiometricLog.query.order_by(BiometricLog.date.desc()).limit(5).all()
    return render_template('log.html', logs=logs)


@app.route('/import-whoop', methods=['POST'])
def import_whoop():
    import io, csv as csv_mod, re as _re

    files = request.files.getlist('whoop_csv')
    files = [f for f in files if f and f.filename.endswith('.csv')]
    if not files:
        flash('Vyber platný CSV súbor z WHOOP.', 'danger')
        return redirect(url_for('log_data'))

    def _find(row, *keys):
        for k in keys:
            for col in row:
                if col.strip().lower() == k.strip().lower():
                    val = row[col]
                    return val.strip() if val else None
        return None

    def _to_float(val):
        if not val:
            return None
        cleaned = _re.sub(r'[^\d.\-]', '', str(val).strip())
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    total_imported = 0
    total_skipped = 0
    debug_cols = None

    for f in files:
        raw = f.stream.read()

        content = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1250'):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            total_skipped += 1
            continue

        lines = content.splitlines()
        header_idx = 0
        for i, line in enumerate(lines):
            low = line.lower()
            if ('date' in low or 'time' in low) and ('hrv' in low or 'recovery' in low or 'heart' in low):
                header_idx = i
                break

        stream = io.StringIO('\n'.join(lines[header_idx:]))
        reader = csv_mod.DictReader(stream)

        if debug_cols is None:
            try:
                stream2 = io.StringIO('\n'.join(lines[header_idx:]))
                debug_cols = next(csv_mod.reader(stream2))
            except Exception:
                pass

        for row in reader:
            raw_date = _find(row,
                'Cycle start time', 'Date', 'date', 'Day',
                'cycle_start_time', 'Start Time', 'start_time', 'Datum', 'Dátum')
            if not raw_date:
                total_skipped += 1
                continue

            entry_date = None
            date_str = raw_date.strip()[:10]
            for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
                try:
                    entry_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if entry_date is None:
                total_skipped += 1
                continue

            raw_hrv = _find(row,
                'Heart rate variability (ms)', 'HRV (ms)', 'hrv', 'HRV',
                'Heart Rate Variability (ms)', 'Heart Rate Variability',
                'heart_rate_variability_ms', 'hrv_rmssd_ms')
            raw_rec = _find(row,
                'Recovery score %', 'Recovery (%)', 'Recovery score', 'recovery',
                'Recovery Score %', 'Recovery Score', 'recovery_score', 'Recovery')
            raw_rhr = _find(row,
                'Resting heart rate (bpm)', 'RHR (bpm)', 'Resting Heart Rate (bpm)',
                'rhr', 'RHR', 'resting_heart_rate', 'Resting Heart Rate',
                'Resting heart rate')

            hrv_val = _to_float(raw_hrv)
            rec_num = _to_float(raw_rec)
            rhr_num = _to_float(raw_rhr)

            if hrv_val is None:
                total_skipped += 1
                continue

            if BiometricLog.query.filter_by(date=entry_date).first():
                total_skipped += 1
                continue

            db.session.add(BiometricLog(
                date=entry_date,
                hrv=hrv_val,
                recovery=int(rec_num) if rec_num is not None else None,
                rhr=int(rhr_num) if rhr_num is not None else None
            ))
            total_imported += 1

    db.session.commit()

    if total_imported == 0:
        col_str = ', '.join(f'"{c}"' for c in (debug_cols or [])[:10])
        msg = f'Import: 0 pridaných, {total_skipped} preskočených.'
        if col_str:
            msg += f' Stĺpce: {col_str}'
        flash(msg, 'warning')
    else:
        flash(f'Import dokončený: {total_imported} záznamov pridaných z {len(files)} súbor(ov), {total_skipped} preskočených.', 'success')

    return redirect(url_for('log_data'))


@app.route('/trainings', methods=['GET', 'POST'])
def trainings():
    if request.method == 'POST':
        t_type = request.form.get('training_type')
        dist = request.form.get('distance_km', type=float)
        dur_min = request.form.get('dur_min', type=int)
        dur_sec = request.form.get('dur_sec', type=int)
        dur_hun = request.form.get('dur_hun', type=int)
        notes = request.form.get('notes')

        intervals = request.form.getlist('interval_times[]')
        intervals_clean = [i for i in intervals if i.strip()]
        intervals_str = ','.join(intervals_clean) if intervals_clean else None

        final_sec = None
        if dur_sec is not None or dur_hun is not None:
            final_sec = float((dur_sec or 0) + (dur_hun or 0) / 100.0)

        if t_type:
            new_log = TrainingLog(
                training_type=t_type,
                distance_km=dist,
                duration_min=dur_min,
                duration_sec=final_sec,
                intervals_data=intervals_str,
                notes=notes
            )
            db.session.add(new_log)
            db.session.commit()
            return redirect(url_for('trainings'))

    all_trainings = TrainingLog.query.order_by(TrainingLog.date.desc()).all()

    grouped_trainings = {}
    train_dates = {}
    for t in all_trainings:
        grouped_trainings.setdefault(t.training_type, []).append(t)
        ds = t.date.strftime('%Y-%m-%d')
        train_dates.setdefault(ds, []).append(t.training_type)

    import json
    return render_template('trainings.html',
                           grouped_trainings=grouped_trainings,
                           train_dates_json=json.dumps(train_dates))


@app.route('/ai')
def ai_portal():
    return render_template('ai.html')


@app.route('/strava/login')
def strava_login():
    if session.get('strava_access_token'):
        return redirect(url_for('dashboard'))

    if not STRAVA_CLIENT_ID or not STRAVA_REDIRECT_URI:
        return "Server not configured for Strava OAuth.", 500

    from urllib.parse import urlencode

    params = {
        'client_id': STRAVA_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': STRAVA_REDIRECT_URI,
        'scope': 'read,activity:read_all',
        'approval_prompt': 'force'
    }
    auth_url = f"https://www.strava.com/oauth/authorize?{urlencode(params)}"
    return redirect(auth_url)


@app.route('/strava/callback')
def strava_authorized():
    code = request.args.get('code')
    if not code:
        return "Chyba: Nedostal som autorizačný kód.", 400

    try:
        client = Client()
        token_response = client.exchange_code_for_token(
            client_id=STRAVA_CLIENT_ID,
            client_secret=STRAVA_CLIENT_SECRET,
            code=code
        )

        athlete = token_response.get('athlete', {})
        athlete_id = str(athlete.get('id')) if athlete.get('id') else None

        session['strava_access_token'] = token_response['access_token']
        session['strava_refresh_token'] = token_response['refresh_token']
        session['strava_expires_at'] = token_response['expires_at']
        session['strava_athlete_id'] = athlete_id

        if athlete_id:
            token_row = StravaToken.query.filter_by(athlete_id=athlete_id).first()
            if not token_row:
                token_row = StravaToken(
                    athlete_id=athlete_id,
                    access_token=token_response['access_token'],
                    refresh_token=token_response['refresh_token'],
                    expires_at=token_response['expires_at']
                )
                db.session.add(token_row)
            else:
                token_row.access_token = token_response['access_token']
                token_row.refresh_token = token_response['refresh_token']
                token_row.expires_at = token_response['expires_at']

            db.session.commit()

        flash("Úspešne si sa prepojil so Stravou!", "success")
        return redirect(url_for('dashboard'))

    except Exception as e:
        return f"Chyba pri komunikácii so Stravou: {e}", 500


@app.route('/dashboard')
def dashboard():
    athlete_id = session.get('strava_athlete_id')
    token = session.get('strava_access_token')

    if not token and not athlete_id:
        flash("Najskôr sa musíš prihlásiť cez Stravu.", "warning")
        return redirect(url_for('index'))

    if athlete_id:
        try:
            token = get_valid_access_token(athlete_id)
        except Exception:
            session.clear()
            flash("Platnosť prihlásenia vypršala. Prihlás sa znova.", "warning")
            return redirect(url_for('index'))

    if not token:
        flash("Najskôr sa musíš prihlásiť cez Stravu.", "warning")
        return redirect(url_for('index'))

    client = Client()
    client.access_token = token

    try:
        activities = list(client.get_activities(limit=10))

        my_trainings = []
        for act in activities:
            hr = getattr(act, 'average_heartrate', None)
            act_type = str(act.type) if act.type else ''
            act_type_lower = act_type.lower()
            if 'ride' in act_type_lower or 'cycling' in act_type_lower:
                pace = speed_to_kmh(act.average_speed)
            elif 'swim' in act_type_lower:
                pace = swim_pace_from_speed(act.average_speed)
            else:
                pace = pace_from_speed(act.average_speed)
            total_sec = int(act.moving_time)
            my_trainings.append({
                'name': act.name,
                'type': act_type,
                'distance': round(float(act.distance) / 1000, 2),
                'moving_time': f"{total_sec // 60}:{total_sec % 60:02d}",
                'pace': pace,
                'hr': int(hr) if hr else None,
                'date': act.start_date.strftime("%d.%m.%Y %H:%M")
            })

        return render_template(
            'dashboard.html',
            trainings=my_trainings,
            logged_in=True
        )

    except Exception as e:
        print(f"Chyba pri načítaní aktivít: {repr(e)}")
        return f"Nepodarilo sa načítať tréningy: {repr(e)}"


@app.route('/osobaky')
def osobaky():
    athlete_id = session.get('strava_athlete_id')
    token = session.get('strava_access_token')

    if athlete_id:
        try:
            token = get_valid_access_token(athlete_id)
        except Exception:
            session.clear()
            flash("Platnosť prihlásenia vypršala. Prihlás sa znova.", "warning")
            return redirect(url_for('index'))

    if not token:
        flash("Najskôr sa musíš prihlásiť cez Stravu.", "warning")
        return redirect(url_for('strava_login'))

    # ?refresh=1 vymaže cache a načíta znova
    if request.args.get('refresh') == '1':
        try:
            from pathlib import Path
            cache_dir = Path('cache')
            athlete_hint = (token or '')[-8:]
            cache_file = cache_dir / f'prs_{athlete_hint}.json'
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass

    prs = {}
    error = None

    try:
        prs = get_strava_prs(token)
    except Exception as e:
        print(f"[WARN] PR fetch failed: {e}")
        error = "Nepodarilo sa načítať osobné rekordy zo Stravy."

    return render_template('osobaky.html', prs=prs, error=error)


@app.route('/logout')
def logout():
    access_token = session.get('strava_access_token')
    athlete_id = session.get('strava_athlete_id')

    # Revoke token at Strava so next login forces credentials
    if access_token:
        try:
            requests.post(
                'https://www.strava.com/oauth/deauthorize',
                data={'access_token': access_token},
                timeout=5
            )
        except Exception:
            pass

    # Delete token from DB so it can't be silently refreshed
    if athlete_id:
        StravaToken.query.filter_by(athlete_id=str(athlete_id)).delete()
        db.session.commit()

    session.clear()
    session.modified = True
    flash("Bol si odhlásený.", "success")
    return redirect(url_for('index'))


@app.route('/api/debug-prs')
def debug_prs():
    """Debug endpoint — ukáže čo Strava vracia pre best_efforts posledných 5 behov."""
    token = session.get('strava_access_token')
    athlete_id = session.get('strava_athlete_id')
    if athlete_id:
        try:
            token = get_valid_access_token(athlete_id)
        except Exception:
            pass
    if not token:
        return jsonify({'error': 'Nie si prihlásený cez Stravu'}), 401

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers=headers,
        params={"per_page": 10, "page": 1},
        timeout=15
    )
    activities = resp.json() if resp.status_code == 200 else []

    results = []
    for act in activities:
        if act.get("type") not in ("Run", "VirtualRun", "TrailRun"):
            continue
        detail_resp = requests.get(
            f"https://www.strava.com/api/v3/activities/{act['id']}",
            headers=headers,
            timeout=10
        )
        if detail_resp.status_code != 200:
            continue
        detail = detail_resp.json()
        efforts = detail.get("best_efforts") or []
        results.append({
            'activity_name': act.get('name'),
            'distance_km': round((act.get('distance') or 0) / 1000, 2),
            'moving_time': act.get('moving_time'),
            'best_efforts': [
                {'name': e.get('name'), 'elapsed_time': e.get('elapsed_time'), 'distance_m': e.get('distance')}
                for e in efforts
            ]
        })
        if len(results) >= 5:
            break

    return jsonify(results)


def decode_polyline(encoded):
    points = []
    index = 0
    lat = lng = 0
    while index < len(encoded):
        for coord_idx in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 32:
                    break
            value = ~(result >> 1) if result & 1 else result >> 1
            if coord_idx == 0:
                lat += value
            else:
                lng += value
        points.append([round(lat / 1e5, 5), round(lng / 1e5, 5)])
    return points


@app.route('/heatmap')
def heatmap():
    athlete_id = session.get('strava_athlete_id')
    token = session.get('strava_access_token')
    if not token and not athlete_id:
        flash("Najskôr sa musíš prihlásiť cez Stravu.", "warning")
        return redirect(url_for('strava_login'))
    return render_template('heatmap.html')


@app.route('/api/heatmap-data')
def heatmap_data():
    athlete_id = session.get('strava_athlete_id')
    token = session.get('strava_access_token')

    if athlete_id:
        try:
            token = get_valid_access_token(athlete_id)
        except Exception:
            return jsonify({'error': 'Prihlásenie vypršalo'}), 401

    if not token:
        return jsonify({'error': 'Nie si prihlásený'}), 401

    headers = {'Authorization': f'Bearer {token}'}
    all_points = []
    activity_count = 0
    page = 1

    while True:
        resp = requests.get(
            'https://www.strava.com/api/v3/athlete/activities',
            headers=headers,
            params={'per_page': 100, 'page': page},
            timeout=20
        )
        if resp.status_code != 200:
            break
        activities = resp.json()
        if not activities or not isinstance(activities, list):
            break

        for act in activities:
            if act.get('type') not in ('Run', 'VirtualRun', 'TrailRun'):
                continue
            polyline = (act.get('map') or {}).get('summary_polyline') or ''
            if not polyline:
                continue
            try:
                pts = decode_polyline(polyline)
                all_points.extend(pts[::2])
                activity_count += 1
            except Exception:
                continue

        page += 1
        if len(activities) < 100:
            break

    return jsonify({
        'points': all_points,
        'activity_count': activity_count,
    })


@app.route('/api/readiness')
def readiness():
    today = date.today()
    today_log = BiometricLog.query.filter_by(date=today).order_by(BiometricLog.id.desc()).first()
    if not today_log:
        today_log = BiometricLog.query.order_by(BiometricLog.date.desc()).first()

    if not today_log:
        return jsonify({'error': 'Žiadne dáta. Najprv si zaloguj HRV a recovery.'}), 404

    cutoff = today - timedelta(days=30)
    logs_30 = BiometricLog.query.filter(BiometricLog.date >= cutoff).all()
    hrv_values = [l.hrv for l in logs_30 if l.hrv is not None]
    avg_hrv = sum(hrv_values) / len(hrv_values) if hrv_values else today_log.hrv

    hrv_ratio = today_log.hrv / avg_hrv if avg_hrv else 1.0
    hrv_ratio = max(0.5, min(1.5, hrv_ratio))
    hrv_score = ((hrv_ratio - 0.5) / 1.0) * 60

    recovery = today_log.recovery
    if recovery is not None:
        recovery_score = recovery * 0.4
        readiness_pct = round(min(100, max(0, hrv_score + recovery_score)))
    else:
        # Recovery nezadané — readiness vychádza len z HRV (0–60 → 0–100)
        readiness_pct = round(min(100, max(0, hrv_score / 60 * 100)))

    if readiness_pct >= 80:
        label = "Trénuj naplno"
        sublabel = "Si výborne zregenerovaný. Ideálny deň na intenzívny tréning alebo preteky."
        color = "green"
        icon = "rocket_launch"
    elif readiness_pct >= 60:
        label = "Stredný tréning"
        sublabel = "Dobrá forma. Vhodné tempové behy alebo stredné intervaly."
        color = "yellow"
        icon = "directions_run"
    elif readiness_pct >= 40:
        label = "Ľahký tréning"
        sublabel = "Telo ešte regeneruje. Odporúčam ľahký klus alebo strečing."
        color = "orange"
        icon = "self_improvement"
    else:
        label = "Oddychuj"
        sublabel = "HRV a recovery sú nízke. Dnes je lepší aktívny odpočinok než tréning."
        color = "red"
        icon = "bedtime"

    hrv_diff = round(today_log.hrv - avg_hrv, 1)
    hrv_diff_str = f"+{hrv_diff}" if hrv_diff >= 0 else str(hrv_diff)

    return jsonify({
        'readiness': readiness_pct,
        'label': label,
        'sublabel': sublabel,
        'color': color,
        'icon': icon,
        'hrv': round(today_log.hrv, 1),
        'hrv_avg_30': round(avg_hrv, 1),
        'hrv_diff': hrv_diff_str,
        'recovery': recovery,
        'rhr': today_log.rhr,
        'log_date': today_log.date.strftime('%d.%m.%Y'),
        'days_of_data': len(hrv_values),
    })


CHAT_SYSTEM_PROMPT = """Si osobný tréner a výkonnostný analytik pre šprintéra/bežca. Komunikuješ VÝLUČNE po slovensky.

## TVOJA ROLA
Ťažiskom je BEŽECKÁ TECHNIKA meraná z videa — čas kontaktu so zemou, letová fáza,
frekvencia krokov, asymetria a uhly v kĺboch. Biometrika (HRV, recovery, RHR) a tréningy
sú doplnkový kontext, ktorý vysvetľuje, prečo technika v daný deň vyzerá tak, ako vyzerá.
Dávaj presné, akčné odporúčania. Nie generické rady — vždy vychádzaj z čísel, ktoré máš.

## PRAVIDLÁ ODPOVEDÍ
1. Vždy po slovensky, nikdy po anglicky
2. Odpovedaj stručne a konkrétne — max 3-4 odstavce
3. Ak máš dáta atleta, MUSÍŠ ich použiť (cituj konkrétne HRV, recovery %)
4. Dávaj konkrétne čísla: "bež 6×200m v 28-29s" nie "rob intervaly"
5. Nezačínaj odpoveď s "Samozrejme" ani "Určite" — choď rovno k veci
6. Ak niečo nevieš presne, povedz to a navrhni alternatívu

## ODBORNÉ ZNALOSTI

### HRV interpretácia pre šprint:
- HRV >80ms = výborná forma, trénuj intenzívne
- HRV 60-80ms = dobrá forma, stredná intenzita
- HRV 40-60ms = únava, ľahký tréning alebo regenerácia
- HRV <40ms = STOP intenzívny tréning, aktívna regenerácia
- Pokles >15% oproti priemeru = varuj atleta

### Recovery (WHOOP) interpretácia:
- Zelená (67-100%) = trénovať naplno
- Žltá (34-66%) = stredná intenzita, sledovať únavu
- Červená (0-33%) = regenerácia, nič ťažké

### RHR trendy:
- Zvýšenie RHR o 5+ bpm oproti priemeru = začínajúce ochorenie alebo pretrénovanie
- Klesajúci RHR cez týždne = zlepšenie aeróbnej zdatnosti

### Technika z videa — ako čítať čísla:
- Kontakt so zemou pri maximálnej rýchlosti: elita 0,08-0,10 s, dobrý dorastenec 0,10-0,13 s,
  nad 0,15 s pri šprinte = brzdenie, noha dopadá príliš pred ťažiskom (overstriding)
- Kratší kontakt pri ROVNAKEJ dĺžke kroku = vyššia rýchlosť. Kratší kontakt za cenu
  skrátenia kroku nie je zlepšenie — vždy sa pozri na obe čísla naraz
- Frekvencia krokov pri šprinte: 4,2-5,0 kroku/s (250-300 krokov/min)
- Duty factor pri šprinte 0,20-0,30. Vyššie číslo = beh bližšie k vytrvalostnému
- Asymetria Ľ/P do 3 % je bežná, 3-5 % sleduj, nad 5 % opakovane = pozri silu,
  pohyblivosť členka/bedra alebo staršie zranenie. Jedno meranie nestačí na záver
- Vertikálna oscilácia nad ~9 cm pri šprinte = energia ide hore namiesto dopredu
- Uhol kolena pri dopade blízko 180° znamená vystretú nohu = tvrdý dopad a brzdenie

### Ako hovoriť o presnosti merania:
- Ak bolo video pod 100 fps, VŽDY povedz, že čísla sú orientačné a odporuč spomalený záber
- Ak sa metóda kostry a optický tok líšia o viac než ~25 ms, neinterpretuj rozdiely
  medzi jednotlivými krokmi — hovor len o priemere
- Nikdy nerob záver o trende z jediného videa

### 400m šprint tréning:
- Intenzity: A=>95% max, B=85-95%, C=75-85%, D=<75%
- Týždenná štruktúra: 1-2x A, 1-2x B, 1x regenerácia
- Kľúčové sedenia: špeciálna vytrvalosť (200-600m úseky), rýchlostná vytrvalosť (100-200m úseky)
- Objem pred pretekmi: znížiť o 40-60% 7-10 dní pred
- Regenerácia po závode: 3-5 dní ľahký tréning

### Výživa pre šprint:
- Pred tréningom: 3-4h pred jedlo, 1h pred ľahká sacharidová sačinka
- Po intenzívnom tréningu: proteín do 30min, sacharidy do 2h
- Hydratácia: 500ml 2h pred, 200ml každých 15min pri cvičení

## PRÍKLADY SPRÁVNYCH ODPOVEDÍ

Otázka: "Ako vyzerá môj HRV dnes?"
Správna odpoveď: "Tvoje dnešné HRV je [X]ms, čo je [nad/pod] tvojim 30-dňovým priemerom [Y]ms o [Z]%. [Ak nad priemerom:] Telo je dobre zregenerované — ideálny deň na intenzívny tréning. [Ak pod:] Odporúčam znížiť intenzitu — maximálne tempový beh v zóne 2-3."

Otázka: "Čo mám robiť dnes?"
Správna odpoveď: [vždy vychádza z aktuálnych dát recovery a HRV z kontextu]

Otázka: "Ako som na tom s technikou?"
Správna odpoveď: "Z posledného videa máš kontakt so zemou [X] ms pri frekvencii [Y] krokov/min.
[Porovnanie s pásmom pre šprint.] Asymetria [Z] % — [interpretácia]. [Ak bolo video pod 100 fps
alebo sa metódy nezhodli: upozorni na presnosť.] Konkrétne by som sa zameral na [cvičenie]." """


@app.route('/api/chat', methods=['POST'])
def chat():
    import re as _re, json as _json
    from pathlib import Path

    data = request.json or {}
    message = data.get('message', '').strip()

    if not message:
        return jsonify({'error': 'Prázdna správa'}), 400

    api_key = os.getenv('GROQ_API_KEY', '')
    if not api_key or api_key.startswith('gsk_...'):
        return jsonify({'error': 'GROQ_API_KEY nie je nastavený v .env súbore'}), 500

    athlete_context = ""
    try:
        today_d = date.today()

        # --- Profil atleta ---
        profile = UserProfile.query.first()
        if profile and any([profile.name, profile.age, profile.sport, profile.goal]):
            athlete_context += "\n\n## PROFIL ATLETA\n"
            if profile.name:  athlete_context += f"Meno: {profile.name}\n"
            if profile.age:   athlete_context += f"Vek: {profile.age} rokov\n"
            if profile.sport: athlete_context += f"Hlavná disciplína: {profile.sport}\n"
            if profile.goal:  athlete_context += f"Cieľ: {profile.goal}\n"

        # --- Osobné rekordy z cache ---
        try:
            token = session.get('strava_access_token', '')
            cache_hint = token[-8:] if token else ''
            cache_file = Path('cache') / f'prs_{cache_hint}.json'
            if cache_file.exists():
                prs = _json.loads(cache_file.read_text(encoding='utf-8'))
                pr_lines = []
                labels = {
                    '400m': '400m', '800m': '800m', '1k': '1km', '1.5k': '1,5km',
                    '1mile': '1 míľa', '2k': '2km', '5k': '5km', '10k': '10km',
                    'half': 'polmaratón', 'marathon': 'maratón',
                }
                for key, label in labels.items():
                    pr = prs.get(key)
                    if pr and pr.get('time'):
                        pace = f", tempo {pr['pace']}" if pr.get('pace') else ""
                        pr_lines.append(f"  {label}: {pr['time']}{pace}")
                if pr_lines:
                    athlete_context += "\n## OSOBNÉ REKORDY (Strava)\n" + "\n".join(pr_lines) + "\n"
        except Exception:
            pass

        # --- Biometrika (60 dní) ---
        logs_60 = BiometricLog.query.filter(
            BiometricLog.date >= today_d - timedelta(days=60)
        ).order_by(BiometricLog.date.desc()).all()

        # --- Tréningy (60 dní) ---
        train_logs = TrainingLog.query.filter(
            TrainingLog.date >= today_d - timedelta(days=60)
        ).order_by(TrainingLog.date.desc()).limit(20).all()

        def avg(v): return round(sum(v)/len(v), 1) if v else None

        if logs_60 or train_logs:
            athlete_context += "\n## AKTUÁLNE DÁTA ATLETA\n"

        if logs_60:
            hrv_all = [l.hrv for l in logs_60 if l.hrv]
            rhr_all = [l.rhr for l in logs_60 if l.rhr]
            rec_all = [l.recovery for l in logs_60 if l.recovery]
            week1 = [l for l in logs_60 if l.date >= today_d - timedelta(days=7)]
            week2 = [l for l in logs_60 if today_d - timedelta(days=14) <= l.date < today_d - timedelta(days=7)]
            hrv_w1 = [l.hrv for l in week1 if l.hrv]
            hrv_w2 = [l.hrv for l in week2 if l.hrv]
            green  = sum(1 for r in rec_all if r >= 67)
            yellow = sum(1 for r in rec_all if 34 <= r < 67)
            red    = sum(1 for r in rec_all if r < 34)

            trend_str = ""
            if hrv_w1 and hrv_w2:
                diff = (avg(hrv_w1) or 0) - (avg(hrv_w2) or 0)
                trend_str = f"{'↑' if diff > 0 else '↓'} {abs(round(diff,1))}ms vs minulý týždeň"

            athlete_context += f"""
### Biometrika (posledných 60 dní, {len(logs_60)} záznamov)
HRV: priemer={avg(hrv_all)}ms | tento týždeň={avg(hrv_w1)}ms | {trend_str}
RHR: priemer={avg(rhr_all)}bpm | min={min(rhr_all) if rhr_all else '?'}bpm
Recovery: zelená={green}d | žltá={yellow}d | červená={red}d | priemer={avg(rec_all)}%

Posledných 7 dní:"""
            for log in logs_60[:7]:
                rec_str = f", Recovery={log.recovery}%" if log.recovery else ""
                status = ("Z" if log.recovery >= 67 else "Y" if log.recovery >= 34 else "R") if log.recovery else ""
                athlete_context += f"\n  {log.date.strftime('%d.%m')}: HRV={log.hrv}ms{rec_str} {status}, RHR={log.rhr or '?'}bpm"

        if train_logs:
            athlete_context += "\n\n### Tréningy (posledných 60 dní):"
            for t in train_logs:
                dur = ""
                if t.duration_min:
                    dur = f" {t.duration_min}min"
                    if t.duration_sec:
                        dur += f" {int(t.duration_sec)}s"
                dist = f" {t.distance_km}km" if t.distance_km else ""
                intervals = f" [{t.intervals_data}]" if t.intervals_data else ""
                notes = f" — {t.notes}" if t.notes else ""
                athlete_context += f"\n  {t.date.strftime('%d.%m')} {t.training_type}:{dist}{dur}{intervals}{notes}"

        # --- Analýza techniky z videa (posledné 3 merania) ---
        vids = VideoAnalysis.query.order_by(VideoAnalysis.created_at.desc()).limit(3).all()
        if vids:
            athlete_context += "\n### Analýza techniky z videa\n"
            for v in vids:
                parts = [f"{v.created_at.strftime('%d.%m.%Y')}"]
                if v.label:
                    parts.append(v.label)
                if v.contact_ms_mean:
                    sd = f" ±{v.contact_ms_sd:.0f}" if v.contact_ms_sd else ""
                    parts.append(f"kontakt so zemou {v.contact_ms_mean:.0f}{sd} ms")
                if v.flight_ms_mean:
                    parts.append(f"let {v.flight_ms_mean:.0f} ms")
                if v.cadence_spm:
                    parts.append(f"kadencia {v.cadence_spm:.0f} krokov/min")
                if v.duty_factor_pct:
                    parts.append(f"duty factor {v.duty_factor_pct:.0f} %")
                if v.asymmetry_pct is not None:
                    parts.append(f"asymetria Ľ/P {v.asymmetry_pct:.1f} %")
                if v.speed_ms:
                    parts.append(f"rýchlosť {v.speed_ms:.1f} m/s")
                if v.stride_length_m:
                    parts.append(f"dvojkrok {v.stride_length_m:.2f} m")
                if v.fps:
                    parts.append(f"(video {v.fps:.0f} fps"
                                 + (", presnosť nízka" if v.fps < 100 else "") + ")")
                athlete_context += "  " + " | ".join(parts) + "\n"
                # Vyhodnotenie techniky podľa pravidiel (čo je zlé)
                try:
                    tech = (_json.loads(v.result_json or '{}').get('technique') or {})
                    bad = [f for f in tech.get('findings', []) if f.get('status') in ('problem', 'pozor')]
                    if bad:
                        athlete_context += "    Technika – na zlepšenie: " + "; ".join(
                            f"{f['label']} {f['value_text']} ({f['status']})" for f in bad[:5]) + "\n"
                except Exception:
                    pass
            athlete_context += (
                "  Referencia pre šprint: kontakt so zemou 0.09–0.13 s, "
                "kratší kontakt pri rovnakej dĺžke kroku = vyššia rýchlosť. "
                "Asymetria nad 5 % môže naznačovať svalovú nerovnováhu.\n")

        # --- Prekážky (posledné 3 merania) ---
        hur = HurdleAnalysis.query.order_by(HurdleAnalysis.created_at.desc()).limit(3).all()
        if hur:
            athlete_context += "\n### Analýza prekážok z videa\n"
            for h in hur:
                parts = [h.created_at.strftime('%d.%m.%Y'), h.discipline or '']
                if h.label:
                    parts.append(h.label)
                if h.hurdles_detected:
                    parts.append(f"{h.hurdles_detected} prekážok")
                if h.interval_mean_s:
                    parts.append(f"medzičas {h.interval_mean_s:.2f} s")
                if h.steps_pattern:
                    parts.append(f"kroky medzi prekážkami {h.steps_pattern}")
                if h.speed_between_ms_mean:
                    parts.append(f"rýchlosť medzi prekážkami {h.speed_between_ms_mean:.1f} m/s")
                athlete_context += "  " + " | ".join(p for p in parts if p) + "\n"

        if athlete_context:
            athlete_context += "\n\n[Toto sú TVOJE skutočné dáta — vždy ich použi pri odpovedi]"

    except Exception:
        pass

    # --- História z DB (posledných 20 správ) ---
    db_history = ChatMessage.query.order_by(ChatMessage.created_at.desc()).limit(20).all()
    db_history = [{'role': m.role, 'content': m.content} for m in reversed(db_history)]

    MODELS = [
        'deepseek-r1-distill-llama-70b',
        'llama-3.3-70b-versatile',
    ]

    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT + athlete_context}]
    messages += db_history
    messages.append({"role": "user", "content": message})

    last_error = None
    for model_name in MODELS:
        try:
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model_name,
                    'messages': messages,
                    'max_tokens': 1500,
                    'temperature': 0.3,
                },
                timeout=40
            )
            if resp.status_code == 401:
                return jsonify({'error': 'Neplatný Groq API kľúč'}), 401
            if resp.status_code == 200:
                reply = resp.json()['choices'][0]['message']['content']
                reply = _re.sub(r'<think>.*?</think>', '', reply, flags=_re.DOTALL).strip()
                # Ulož do DB
                db.session.add(ChatMessage(role='user', content=message))
                db.session.add(ChatMessage(role='assistant', content=reply))
                db.session.commit()
                return jsonify({'reply': reply, 'model': model_name})
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            continue

    return jsonify({'error': f'Chyba: {last_error}'}), 500


@app.route('/api/chat/history')
def chat_history():
    msgs = ChatMessage.query.order_by(ChatMessage.created_at.asc()).all()
    return jsonify([{'role': m.role, 'content': m.content} for m in msgs])


@app.route('/api/chat/clear', methods=['POST'])
def chat_clear():
    ChatMessage.query.delete()
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/profile/api')
def profile_api():
    p = UserProfile.query.first()
    if not p:
        return jsonify({})
    return jsonify({'name': p.name, 'age': p.age, 'sport': p.sport, 'goal': p.goal})


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    p = UserProfile.query.first()
    if request.method == 'POST':
        if not p:
            p = UserProfile()
            db.session.add(p)
        p.name = request.form.get('name', '').strip() or None
        age_raw = request.form.get('age', '').strip()
        p.age = int(age_raw) if age_raw.isdigit() else None
        p.sport = request.form.get('sport', '').strip() or None
        p.goal = request.form.get('goal', '').strip() or None
        h_raw = request.form.get('height_cm', '').strip().replace(',', '.')
        try:
            p.height_cm = float(h_raw) if h_raw else None
        except ValueError:
            p.height_cm = None
        p.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Profil uložený.', 'success')
        return redirect(url_for('ai_portal'))
    return render_template('profile.html', p=p)


# ===========================================================================
# ANALÝZA VIDEA – kontakt so zemou, technika, prekážky
# ===========================================================================

VIDEO_DIR = os.path.join(app.root_path, 'static', 'uploads', 'videos')
os.makedirs(VIDEO_DIR, exist_ok=True)

ALLOWED_VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm'}
app.config['MAX_CONTENT_LENGTH'] = 600 * 1024 * 1024   # 600 MB

# Prebiehajúce analýzy: job_id -> stav.  Beží v pamäti, po reštarte sa zabudne.
_video_jobs = {}
_video_jobs_lock = threading.Lock()


def _job_set(job_id, **kw):
    with _video_jobs_lock:
        job = _video_jobs.setdefault(job_id, {})
        job.update(kw)
        # Hotové úlohy sa nedržia v pamäti donekonečna (výsledok je v DB)
        done = [k for k, v in _video_jobs.items() if v.get('state') in ('done', 'error')]
        for k in done[:-20]:
            _video_jobs.pop(k, None)


def _job_get(job_id):
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
        return dict(job) if job else None


def _video_url(name):
    # Bez url_for – volá sa aj z vlákna analýzy, kde nie je aktívny request.
    return f"{app.static_url_path}/uploads/videos/{name}" if name else None


def _attach_urls(result):
    """Doplní do výsledku URL prekryvného videa a kľúčových snímok."""
    result['overlay_url'] = _video_url(os.path.basename(result['overlay_path'])) \
        if result.get('overlay_path') else None
    for k in result.get('keyframes') or []:
        k['url'] = _video_url(k['file'])
    return result


def _parse_upload_form():
    """Spoločné čítanie formulára pre šprint aj prekážky."""
    def _num(name):
        raw = (request.form.get(name) or '').strip().replace(',', '.')
        try:
            v = float(raw)
            return v if v > 0 else None
        except ValueError:
            return None

    init_point = None
    px, py = _num('click_x'), _num('click_y')
    if px is not None and py is not None and 0 < px < 1 and 0 < py < 1:
        init_point = (px, py)

    return {
        'height_cm': _num('height_cm'),
        'fps_override': _num('fps_override'),
        'make_overlay': request.form.get('overlay', '1') != '0',
        'with_flow': request.form.get('flow', '1') != '0',
        'label': (request.form.get('label') or '').strip()[:120],
        'init_point': init_point,
        'phase': (request.form.get('phase') or 'auto').strip(),
        'discipline': (request.form.get('discipline') or '400mH').strip(),
    }


def _save_upload():
    """Uloží nahraté video, vráti (job_id, path, stored_name, original_name) alebo chybu."""
    file = request.files.get('video')
    if not file or not file.filename:
        return None, (jsonify({'error': 'Nevybral si žiadne video.'}), 400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXT:
        return None, (jsonify({
            'error': f'Formát {ext or "?"} nie je podporovaný. '
                     f'Použi {", ".join(sorted(ALLOWED_VIDEO_EXT))}.'
        }), 400)
    job_id = uuid.uuid4().hex
    stored_name = f'{job_id}{ext}'
    path = os.path.join(VIDEO_DIR, stored_name)
    file.save(path)
    return (job_id, path, stored_name, file.filename), None


def _remember_height(height_cm):
    if height_cm:
        p = UserProfile.query.first()
        if not p:
            p = UserProfile()
            db.session.add(p)
        p.height_cm = height_cm
        db.session.commit()


def _run_video_job(job_id, video_path, stored_name, original_name, opts):
    """Beží v samostatnom vlákne – analýza môže trvať desiatky sekúnd."""
    import video_analysis as va

    def progress(frac, msg):
        _job_set(job_id, state='running', progress=round(frac * 100), message=msg)

    overlay_path = None
    try:
        overlay_name = None
        if opts['make_overlay']:
            overlay_name = os.path.splitext(stored_name)[0] + '_overlay.mp4'
            overlay_path = os.path.join(VIDEO_DIR, overlay_name)

        result = va.analyze_video(
            video_path,
            athlete_height_cm=opts['height_cm'],
            real_fps=opts['fps_override'],
            overlay_path=overlay_path,
            with_flow=opts['with_flow'],
            progress_cb=progress,
            init_point=opts['init_point'],
            keyframes_dir=VIDEO_DIR,
            keyframes_prefix=os.path.splitext(stored_name)[0],
            phase=opts['phase'],
        )

        if not result.get('overlay_path'):
            overlay_name = None

        s = result['summary']
        with app.app_context():
            row = VideoAnalysis(
                original_name=original_name,
                stored_name=stored_name,
                overlay_name=overlay_name,
                label=opts['label'] or None,
                fps=result['video']['fps'],
                duration_s=result['video']['duration_s'],
                athlete_height_cm=opts['height_cm'],
                steps_detected=s['steps_detected'],
                contact_ms_mean=s['contact_ms_mean'],
                contact_ms_sd=s['contact_ms_sd'],
                flight_ms_mean=s['flight_ms_mean'],
                cadence_spm=s['cadence_spm'],
                duty_factor_pct=s['duty_factor_pct'],
                asymmetry_pct=s['asymmetry_pct'],
                speed_ms=s['speed_ms'],
                stride_length_m=s['stride_length_m'],
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.session.add(row)
            db.session.commit()
            new_id = row.id
            _attach_urls(result)
            result['analysis_id'] = new_id

        _job_set(job_id, state='done', progress=100, message='Hotovo',
                 analysis_id=new_id, result=result)

    except va.VideoAnalysisError as e:
        _job_set(job_id, state='error', message=str(e))
        _cleanup_failed(video_path, overlay_path)
    except Exception as e:
        _job_set(job_id, state='error',
                 message=f'Neočakávaná chyba pri analýze: {e}')
        _cleanup_failed(video_path, overlay_path)


def _cleanup_failed(video_path, overlay_path):
    """Po neúspešnej analýze nenechá na disku nahraté video ani rozpracované súbory."""
    for p in (video_path, overlay_path, (overlay_path + '.tmp.mp4') if overlay_path else None):
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
    base = os.path.splitext(os.path.basename(video_path))[0]
    try:
        for name in os.listdir(VIDEO_DIR):
            if name.startswith(base + '_key'):
                os.remove(os.path.join(VIDEO_DIR, name))
    except OSError:
        pass


@app.route('/video')
def video_page():
    import video_analysis as va
    ok, dep_msg = va.dependencies_ok()
    profile = UserProfile.query.first()
    history = VideoAnalysis.query.order_by(VideoAnalysis.created_at.desc()).limit(25).all()
    return render_template(
        'video.html',
        deps_ok=ok, deps_msg=dep_msg,
        default_height=(profile.height_cm if profile and profile.height_cm else ''),
        history=history,
        groq_ok=bool(os.getenv('GROQ_API_KEY', '')) and not os.getenv('GROQ_API_KEY', '').startswith('gsk_...'),
    )


@app.route('/api/video/analyze', methods=['POST'])
def video_analyze():
    import video_analysis as va
    ok, dep_msg = va.dependencies_ok()
    if not ok:
        return jsonify({'error': dep_msg}), 500

    saved, err = _save_upload()
    if err:
        return err
    job_id, path, stored_name, original_name = saved
    opts = _parse_upload_form()
    _remember_height(opts['height_cm'])

    try:
        info = va.probe_video(path)
    except va.VideoAnalysisError as e:
        os.remove(path)
        return jsonify({'error': str(e)}), 400

    _job_set(job_id, state='queued', progress=0, message='Čakám na spracovanie',
             video_info=info)
    threading.Thread(
        target=_run_video_job,
        args=(job_id, path, stored_name, original_name, opts),
        daemon=True,
    ).start()

    return jsonify({'job_id': job_id, 'video_info': info})


@app.route('/api/video/job/<job_id>')
def video_job(job_id):
    job = _job_get(job_id)
    if not job:
        return jsonify({'error': 'Úloha neexistuje (server bol pravdepodobne reštartovaný).'}), 404
    return jsonify(job)


@app.route('/api/video/result/<int:analysis_id>')
def video_result(analysis_id):
    row = db.session.get(VideoAnalysis, analysis_id)
    if not row:
        return jsonify({'error': 'Analýza neexistuje.'}), 404
    data = json.loads(row.result_json or '{}')
    _attach_urls(data)
    data['overlay_url'] = _video_url(row.overlay_name)
    data['analysis_id'] = row.id
    data['label'] = row.label
    data['created_at'] = row.created_at.strftime('%d.%m.%Y %H:%M')
    return jsonify(data)


@app.route('/api/video/delete/<int:analysis_id>', methods=['POST'])
def video_delete(analysis_id):
    row = db.session.get(VideoAnalysis, analysis_id)
    if not row:
        return jsonify({'error': 'Analýza neexistuje.'}), 404
    _delete_media(row)
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


def _delete_media(row):
    names = [row.stored_name, row.overlay_name]
    try:
        for k in (json.loads(row.result_json or '{}').get('keyframes') or []):
            names.append(k.get('file'))
    except Exception:
        pass
    for name in names:
        if name:
            try:
                os.remove(os.path.join(VIDEO_DIR, name))
            except OSError:
                pass


# ---------------------------------------------------------------------------
# PREKÁŽKY
# ---------------------------------------------------------------------------

def _run_hurdle_job(job_id, video_path, stored_name, original_name, opts):
    import video_analysis as va
    import hurdle_analysis as ha

    def progress(frac, msg):
        _job_set(job_id, state='running', progress=round(frac * 100), message=msg)

    overlay_path = None
    try:
        overlay_name = None
        if opts['make_overlay']:
            overlay_name = os.path.splitext(stored_name)[0] + '_overlay.mp4'
            overlay_path = os.path.join(VIDEO_DIR, overlay_name)

        result = ha.analyze_hurdle_video(
            video_path,
            discipline=opts['discipline'],
            athlete_height_cm=opts['height_cm'],
            real_fps=opts['fps_override'],
            overlay_path=overlay_path,
            with_flow=opts['with_flow'],
            init_point=opts['init_point'],
            keyframes_dir=VIDEO_DIR,
            keyframes_prefix=os.path.splitext(stored_name)[0],
            progress_cb=progress,
        )
        if not result.get('overlay_path'):
            overlay_name = None

        s = result['summary']
        with app.app_context():
            row = HurdleAnalysis(
                original_name=original_name,
                stored_name=stored_name,
                overlay_name=overlay_name,
                label=opts['label'] or None,
                discipline=result['discipline'],
                fps=result['video']['fps'],
                duration_s=result['video']['duration_s'],
                athlete_height_cm=opts['height_cm'],
                hurdles_detected=s['hurdles_detected'],
                interval_mean_s=s['interval_mean_s'],
                interval_sd_s=s['interval_sd_s'],
                steps_pattern=s['steps_pattern_text'],
                flight_ms_mean=s['flight_ms_mean'],
                landing_contact_ms_mean=s['landing_contact_ms_mean'],
                speed_between_ms_mean=s['speed_between_ms_mean'],
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.session.add(row)
            db.session.commit()
            new_id = row.id
            _attach_urls(result)
            result['analysis_id'] = new_id

        _job_set(job_id, state='done', progress=100, message='Hotovo',
                 analysis_id=new_id, result=result)
    except va.VideoAnalysisError as e:
        _job_set(job_id, state='error', message=str(e))
        _cleanup_failed(video_path, overlay_path)
    except Exception as e:
        _job_set(job_id, state='error', message=f'Neočakávaná chyba pri analýze: {e}')
        _cleanup_failed(video_path, overlay_path)


@app.route('/prekazky')
def hurdles_page():
    import video_analysis as va
    ok, dep_msg = va.dependencies_ok()
    profile = UserProfile.query.first()
    history = HurdleAnalysis.query.order_by(HurdleAnalysis.created_at.desc()).limit(25).all()
    return render_template(
        'prekazky.html',
        deps_ok=ok, deps_msg=dep_msg,
        default_height=(profile.height_cm if profile and profile.height_cm else ''),
        history=history,
        groq_ok=bool(os.getenv('GROQ_API_KEY', '')) and not os.getenv('GROQ_API_KEY', '').startswith('gsk_...'),
    )


@app.route('/api/hurdles/analyze', methods=['POST'])
def hurdles_analyze():
    import video_analysis as va
    ok, dep_msg = va.dependencies_ok()
    if not ok:
        return jsonify({'error': dep_msg}), 500
    saved, err = _save_upload()
    if err:
        return err
    job_id, path, stored_name, original_name = saved
    opts = _parse_upload_form()
    _remember_height(opts['height_cm'])
    try:
        info = va.probe_video(path)
    except va.VideoAnalysisError as e:
        os.remove(path)
        return jsonify({'error': str(e)}), 400
    _job_set(job_id, state='queued', progress=0, message='Čakám na spracovanie', video_info=info)
    threading.Thread(target=_run_hurdle_job,
                     args=(job_id, path, stored_name, original_name, opts),
                     daemon=True).start()
    return jsonify({'job_id': job_id, 'video_info': info})


@app.route('/api/hurdles/result/<int:analysis_id>')
def hurdles_result(analysis_id):
    row = db.session.get(HurdleAnalysis, analysis_id)
    if not row:
        return jsonify({'error': 'Analýza neexistuje.'}), 404
    data = json.loads(row.result_json or '{}')
    _attach_urls(data)
    data['overlay_url'] = _video_url(row.overlay_name)
    data['analysis_id'] = row.id
    data['label'] = row.label
    data['created_at'] = row.created_at.strftime('%d.%m.%Y %H:%M')
    return jsonify(data)


@app.route('/api/hurdles/delete/<int:analysis_id>', methods=['POST'])
def hurdles_delete(analysis_id):
    row = db.session.get(HurdleAnalysis, analysis_id)
    if not row:
        return jsonify({'error': 'Analýza neexistuje.'}), 404
    _delete_media(row)
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# AI TRÉNER – komentár k nameranej technike (Groq)
# ---------------------------------------------------------------------------

COACH_SYSTEM_PROMPT = """Si skúsený atletický tréner šprintu a prekážkového behu. Dostaneš výsledky
automatickej analýzy techniky z videa (čísla + vyhodnotenie podľa pravidiel). Napíš atlétovi krátky,
konkrétny komentár v slovenčine (tykaj mu):

1. Dve-tri vety, čo je na technike dobré.
2. Najviac 3 najdôležitejšie veci, ktoré zlepšiť – pri každej: čo vidíš v číslach, prečo to
   spomaľuje a 1–2 konkrétne cvičenia alebo pokyny do tréningu.
3. Jedna veta, na čo sa zamerať v ďalšom videu.

Pravidlá: opieraj sa výhradne o dodané čísla, nič si nevymýšľaj. Ak je video pod 100 fps alebo sú
v upozorneniach pochybnosti o kvalite, povedz, že časové údaje sú orientačné. Uhly platia len pri
zábere presne z boku. Buď stručný (do 250 slov), bez nadpisov, môžeš použiť odrážky."""


def _groq_complete(messages, max_tokens=900):
    """Zavolá Groq API s fallbackom medzi modelmi; vráti (text, model) alebo vyhodí výnimku."""
    import re as _re
    api_key = os.getenv('GROQ_API_KEY', '')
    if not api_key or api_key.startswith('gsk_...'):
        raise RuntimeError('GROQ_API_KEY nie je nastavený v .env súbore')
    last_error = None
    for model_name in ['llama-3.3-70b-versatile', 'openai/gpt-oss-120b',
                       'deepseek-r1-distill-llama-70b']:
        try:
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': model_name, 'messages': messages,
                      'max_tokens': max_tokens, 'temperature': 0.3},
                timeout=40,
            )
            if resp.status_code == 401:
                raise RuntimeError('Neplatný Groq API kľúč')
            if resp.status_code == 200:
                reply = resp.json()['choices'][0]['message']['content']
                reply = _re.sub(r'<think>.*?</think>', '', reply, flags=_re.DOTALL).strip()
                return reply, model_name
            last_error = f'HTTP {resp.status_code}'
        except RuntimeError:
            raise
        except Exception as e:
            last_error = str(e)
    raise RuntimeError(f'Groq neodpovedal: {last_error}')


def _coach_context(kind, data):
    """Textový súhrn merania pre AI trénera."""
    lines = []
    v = data.get('video') or {}
    lines.append(f"Video: {v.get('fps')} fps, {v.get('duration_s')} s, kostra rozpoznaná na "
                 f"{round((v.get('detected_ratio') or 0) * 100)} % snímok.")
    s = data.get('summary') or {}
    if kind == 'sprint':
        lines.append("Typ: šprint, fáza: " + str((data.get('technique') or {}).get('phase_text')))
        for k, lab in (('contact_ms_mean', 'kontakt so zemou (ms)'), ('contact_ms_sd', 'rozptyl kontaktu (ms)'),
                       ('flight_ms_mean', 'letová fáza (ms)'), ('cadence_spm', 'frekvencia (krokov/min)'),
                       ('step_length_m', 'dĺžka kroku (m)'), ('step_length_height_ratio', 'dĺžka kroku / výška'),
                       ('speed_ms', 'rýchlosť (m/s)'), ('duty_factor_pct', 'duty factor (%)'),
                       ('asymmetry_pct', 'asymetria Ľ/P (%)'), ('vertical_oscillation_cm', 'vertikálna oscilácia (cm)')):
            if s.get(k) is not None:
                lines.append(f"- {lab}: {s[k]}")
        steps = data.get('steps') or []
        if steps:
            def _avg(key, sub):
                vals = [((st.get(sub) or {}).get(key)) for st in steps]
                vals = [x for x in vals if x is not None]
                return round(sum(vals) / len(vals), 1) if vals else None
            lines.append(f"- priemerné uhly pri dopade: koleno {_avg('knee_deg', 'angles_touchdown')}°, "
                         f"trup {_avg('trunk_lean_deg', 'angles_touchdown')}°, holeň {_avg('shank_deg', 'angles_touchdown')}° "
                         f"(+ = koleno pred členkom)")
            lines.append(f"- priemerné uhly pri odraze: koleno {_avg('knee_deg', 'angles_toeoff')}°, "
                         f"stehno {_avg('thigh_deg', 'angles_toeoff')}° (- = koleno za bedrom)")
    else:
        lines.append(f"Typ: prekážky ({s.get('discipline_label')}), prekážok v zábere: {s.get('hurdles_detected')}. "
                     "Hodnotí sa len rytmus: medzičasy medzi prekážkami (dopad → dopad), počet krokov medzi "
                     "prekážkami a odrazová noha.")
        for k, lab in (('interval_mean_s', 'priemerný medzičas medzi prekážkami (s)'),
                       ('interval_sd_s', 'rozptyl medzičasov (s)'),
                       ('interval_trend_pct', 'zmena medzičasu prvý→posledný (%)'),
                       ('steps_pattern_text', 'kroky medzi prekážkami'),
                       ('speed_between_ms_mean', 'rýchlosť medzi prekážkami (m/s)')):
            if s.get(k) is not None:
                lines.append(f"- {lab}: {s[k]}")
        for iv in data.get('intervals') or []:
            lines.append(f"- úsek P{iv['from_hurdle']}→P{iv['to_hurdle']}: medzičas {iv.get('interval_s')} s, "
                         f"{iv.get('steps_between')} krokov"
                         + (f", rýchlosť {iv.get('speed_ms')} m/s" if iv.get('speed_ms') else ""))
        for h in data.get('hurdles') or []:
            lines.append(f"- prekážka {h['index']}: odraz z {h.get('takeoff_side_text')} nohy, "
                         f"dopad za prekážkou v čase {h.get('landing_s')} s videa")
    t = data.get('technique') or {}
    if t.get('findings'):
        lines.append("Vyhodnotenie podľa pravidiel:")
        for f in t['findings']:
            lines.append(f"- [{f['status']}] {f['label']}: {f['value_text']} (referencia {f['reference']}) – {f['text']}")
    if data.get('warnings'):
        lines.append("Upozornenia k meraniu: " + " | ".join(data['warnings']))
    return "\n".join(lines)


@app.route('/api/coach/<kind>/<int:analysis_id>', methods=['POST'])
def coach_comment(kind, analysis_id):
    model = VideoAnalysis if kind == 'sprint' else HurdleAnalysis if kind == 'hurdles' else None
    if model is None:
        return jsonify({'error': 'Neznámy typ analýzy.'}), 400
    row = db.session.get(model, analysis_id)
    if not row:
        return jsonify({'error': 'Analýza neexistuje.'}), 404
    data = json.loads(row.result_json or '{}')

    profile = UserProfile.query.first()
    prof = ''
    if profile:
        bits = []
        if profile.name: bits.append(f"meno {profile.name}")
        if profile.age: bits.append(f"vek {profile.age}")
        if profile.height_cm: bits.append(f"výška {profile.height_cm:.0f} cm")
        if profile.sport: bits.append(f"disciplína {profile.sport}")
        if profile.goal: bits.append(f"cieľ {profile.goal}")
        if bits:
            prof = "Atlét: " + ", ".join(bits) + "\n"

    context = prof + _coach_context(kind, data)
    messages = [
        {'role': 'system', 'content': COACH_SYSTEM_PROMPT},
        {'role': 'user', 'content': "Toto sú výsledky merania:\n\n" + context +
         "\n\nNapíš mi komentár k technike a čo mám zlepšiť."},
    ]
    try:
        reply, model_name = _groq_complete(messages)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500

    # Komentár sa uloží k výsledku, aby sa nemusel generovať znova
    data['coach_comment'] = reply
    data['coach_model'] = model_name
    row.result_json = json.dumps(data, ensure_ascii=False)
    db.session.commit()
    return jsonify({'reply': reply, 'model': model_name})


if __name__ == '__main__':
    # Otvor prehliadač iba raz (v child procese reloadera, nie v parent procese)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        def _open_browser():
            time.sleep(1.5)
            webbrowser.open('http://127.0.0.1:5001')
        threading.Thread(target=_open_browser, daemon=True).start()
    app.run(debug=True, port=5001)
