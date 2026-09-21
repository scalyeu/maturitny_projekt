# -*- coding: utf-8 -*-
"""
video_analysis.py
=================
Analýza šprintérskej techniky z videa – kontakt so zemou (ground contact time),
letová fáza, frekvencia krokov, uhly v kĺboch a odhad rýchlosti.

Ako to funguje (zhrnutie pre obhajobu maturitného projektu)
-----------------------------------------------------------
1.  KOSTRA:  Každý snímok videa prejde cez MediaPipe Pose (Google), ktorý vráti
    33 bodov kostry v normalizovaných súradniciach.
2.  NORMALIZÁCIA:  Súradnice nôh sa prepočítajú relatívne k panve a vydelia sa
    dĺžkou trupu.  Vďaka tomu detekcia funguje aj keď kamera zoomuje alebo sa
    otáča za bežcom (panning).
3.  DETEKCIA KROKOV – dve nezávislé metódy:
    a) POLOHOVÁ (Zeni et al., 2008) – dopad = lokálne maximum vzdialenosti päty
       pred panvou, odraz = lokálne minimum vzdialenosti špičky za panvou.
       Robustná, nepotrebuje žiadny prah.
    b) VERTIKÁLNA – noha je v kontakte, kým je jej najnižší bod v pásme pri zemi.
       Presnejšia, ale potrebuje prah.
    Metóda (a) nájde približné udalosti, metóda (b) ich spresní.  Rozdiel medzi
    nimi sa vypisuje ako kontrola kvality merania.
4.  SUB-FRAME INTERPOLÁCIA:  Presný okamih dopadu/odrazu sa dopočíta lineárnou
    interpoláciou medzi dvoma snímkami, takže presnosť je lepšia než 1 snímok.
5.  KALIBRÁCIA:  Z výšky atléta sa určí mierka (meter na pixel).  Rýchlosť sa
    počíta z posunu panvy voči stojnej nohe (tá je počas kontaktu nehybná voči
    zemi), takže funguje aj s pohyblivou kamerou.

DÔLEŽITÉ – PRESNOSŤ:
    Kontakt so zemou pri šprinte trvá zhruba 0.09 – 0.13 s.  Pri 30 fps je jeden
    snímok 33 ms, čiže kontakt má len 3–4 snímky a chyba je obrovská.
    Preto NATÁČAJ V SPOMALENOM REŽIME (120 alebo 240 fps).
    Modul vždy vracia aj odhad neistoty merania (`uncertainty_ms`).

Autor: maturitný projekt – Track AI (Sprint Predictor)
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import json
from typing import Callable, Optional, List, Dict, Any

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import mediapipe as mp
except Exception:  # pragma: no cover
    mp = None


# ---------------------------------------------------------------------------
# Konštanty
# ---------------------------------------------------------------------------

# Indexy bodov v MediaPipe Pose
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_HEEL, R_HEEL = 29, 30
L_TOE, R_TOE = 31, 32

SKELETON = [
    (L_SHOULDER, R_SHOULDER), (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
    (L_HIP, R_HIP),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST),
    (L_HIP, L_KNEE), (L_KNEE, L_ANKLE), (L_ANKLE, L_HEEL), (L_HEEL, L_TOE),
    (L_ANKLE, L_TOE),
    (R_HIP, R_KNEE), (R_KNEE, R_ANKLE), (R_ANKLE, R_HEEL), (R_HEEL, R_TOE),
    (R_ANKLE, R_TOE),
]

# Antropometria (Winter, Biomechanics and Motor Control of Human Movement)
# Výška bedrového kĺbu ≈ 0.530 * telesná výška, členok ≈ 0.039 * výška.
LEG_TO_HEIGHT = 0.530 - 0.039   # ≈ 0.491

# Fyziologické medze – čokoľvek mimo sa zahodí ako chybná detekcia
MIN_CONTACT_S = 0.045
MAX_CONTACT_S = 0.450
MIN_STEP_S = 0.130
MAX_STEP_S = 0.900

# Rozumný rozsah dĺžky kroku pri behu (m).  Čokoľvek mimo je takmer isto chyba
# merania (zlá výška, zlé fps, bežec nie je celý v zábere) a appka to povie.
MIN_STEP_LEN_M = 0.60
MAX_STEP_LEN_M = 3.20

DEFAULT_THRESHOLD_FRAC = 0.06   # pásmo pri zemi ako podiel amplitúdy švihu
DEFAULT_MAX_FRAMES = 6000       # ~25 s pri 240 fps, ~3 min pri 30 fps


class VideoAnalysisError(Exception):
    """Chyba pri analýze videa – text je určený priamo pre používateľa."""


# ---------------------------------------------------------------------------
# Malé numerické pomôcky (zámerne bez scipy, aby bolo menej závislostí)
# ---------------------------------------------------------------------------

def _savgol(y: np.ndarray, window: int, order: int = 2) -> np.ndarray:
    """Savitzky-Golayov filter implementovaný cez najmenšie štvorce."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if window % 2 == 0:
        window += 1
    if window < order + 2 or n < window:
        return y.copy()
    half = window // 2
    # Návrhová matica pre polynóm stupňa `order` na okne [-half, half]
    x = np.arange(-half, half + 1, dtype=float)
    A = np.vander(x, order + 1, increasing=True)
    # Riadok pseudo-inverznej matice pre hodnotu v strede okna (x = 0)
    coeffs = np.linalg.pinv(A)[0]
    padded = np.concatenate([
        np.full(half, y[0]),
        y,
        np.full(half, y[-1]),
    ])
    out = np.convolve(padded, coeffs[::-1], mode="valid")
    return out[:n]


def _interp_nans(y: np.ndarray, max_gap: int = 8) -> np.ndarray:
    """Doplní krátke medzery lineárnou interpoláciou, dlhé nechá ako NaN."""
    y = np.asarray(y, dtype=float).copy()
    n = y.size
    good = np.isfinite(y)
    if good.sum() < 2:
        return y
    idx = np.arange(n)
    filled = np.interp(idx, idx[good], y[good])
    # Dlhé medzery vrátime späť na NaN
    bad = ~good
    if bad.any():
        starts = np.where(bad & ~np.r_[False, bad[:-1]])[0]
        for s in starts:
            e = s
            while e < n and bad[e]:
                e += 1
            if (e - s) > max_gap:
                filled[s:e] = np.nan
    # Okraje bez dát nedopĺňame
    first, last = idx[good][0], idx[good][-1]
    filled[:first] = np.nan
    filled[last + 1:] = np.nan
    return filled


def _rolling_max(y: np.ndarray, window: int) -> np.ndarray:
    """Kĺzavé maximum (ignoruje NaN) – slúži na odhad úrovne zeme."""
    y = np.asarray(y, dtype=float)
    n = y.size
    window = max(3, min(window, n))
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.concatenate([np.full(half, y[0]), y, np.full(half, y[-1])])
    try:
        view = np.lib.stride_tricks.sliding_window_view(padded, window)
        with np.errstate(all="ignore"):
            out = np.nanmax(view, axis=1)
        return out[:n]
    except Exception:
        return np.array([np.nanmax(y[max(0, i - half): i + half + 1]) for i in range(n)])


def _find_extrema(y: np.ndarray, min_dist: int, mode: str = "max") -> List[int]:
    """Nájde lokálne maximá (alebo minimá) so zadanou minimálnou vzdialenosťou."""
    y = np.asarray(y, dtype=float)
    if mode == "min":
        y = -y
    n = y.size
    cand = []
    for i in range(1, n - 1):
        if not np.isfinite(y[i]):
            continue
        left = y[i - 1]
        right = y[i + 1]
        if not (np.isfinite(left) and np.isfinite(right)):
            continue
        if y[i] >= left and y[i] >= right:
            cand.append(i)
    # Zoradíme podľa výšky a hladovo vyberáme s odstupom
    cand.sort(key=lambda i: -y[i])
    chosen: List[int] = []
    for i in cand:
        if all(abs(i - j) >= min_dist for j in chosen):
            chosen.append(i)
    return sorted(chosen)


def _cross_time(y: np.ndarray, i_lo: int, i_hi: int, level: float) -> float:
    """Sub-frame poloha prechodu cez `level` medzi snímkami i_lo a i_hi."""
    a, b = y[i_lo], y[i_hi]
    if not (np.isfinite(a) and np.isfinite(b)) or a == b:
        return float(i_hi)
    frac = (level - a) / (b - a)
    frac = min(max(frac, 0.0), 1.0)
    return i_lo + frac * (i_hi - i_lo)


def _refine_edge(y: np.ndarray, idx: int, fps: float, rising: bool,
                 fallback: float) -> float:
    """
    Spresní okamih dopadu / odrazu priesečníkom dvoch priamok.

    Pri dopade noha rýchlo klesá a potom sa jej pohyb takmer zastaví (je na zemi).
    Priesečník priamky klesania a priamky "plató" je oveľa presnejší okamih
    dopadu než obyčajné prekročenie prahu – prah totiž vždy zachytí nohu
    kúsok pred dopadom a tým nadhodnotí čas kontaktu.

    `rising=True`  → hľadá sa dopad  (pred udalosťou sa y zväčšuje)
    `rising=False` → hľadá sa odraz  (za udalosťou sa y zmenšuje)
    """
    n = y.size
    k = max(2, int(round(0.030 * fps)))          # ~30 ms na každú priamku
    # Medzera okolo prahu – prah vždy zachytí udalosť o kúsok skôr/neskôr,
    # takže snímky tesne pri ňom do žiadnej z dvoch priamok nepatria.
    gap = max(1, int(round(0.008 * fps)))

    if rising:
        seg_a = (idx - gap - k, idx - gap)        # fáza priblíženia k zemi
        seg_b = (idx + gap, idx + gap + k)        # fáza na zemi
    else:
        seg_a = (idx - gap - k + 1, idx - gap + 1)   # fáza na zemi
        seg_b = (idx + gap + 1, idx + gap + k + 1)   # odlepenie

    def fit(lo, hi):
        lo, hi = max(0, lo), min(n, hi)
        t = np.arange(lo, hi, dtype=float)
        v = y[lo:hi]
        m = np.isfinite(v)
        if m.sum() < 2:
            return None
        return np.polyfit(t[m], v[m], 1)

    p1, p2 = fit(*seg_a), fit(*seg_b)
    if p1 is None or p2 is None:
        return fallback
    if abs(p1[0] - p2[0]) < 1e-9:
        return fallback
    t_star = (p2[1] - p1[1]) / (p1[0] - p2[0])
    # Priesečník musí padnúť do rozumného okolia, inak dôverujeme prahu
    if not np.isfinite(t_star) or abs(t_star - idx) > (k + gap):
        return fallback
    return float(min(max(t_star, 0.0), n - 1.0))


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Uhol pri vrchole b (v stupňoch); 180° = vystretý kĺb."""
    v1 = a - b
    v2 = c - b
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos = float(np.dot(v1, v2) / (n1 * n2))
    return math.degrees(math.acos(min(1.0, max(-1.0, cos))))


def _angle_from_vertical(top: np.ndarray, bottom: np.ndarray) -> float:
    """Odklon úsečky od zvislice v stupňoch (kladné = naklonené dopredu +x)."""
    dx = float(top[0] - bottom[0])
    dy = float(bottom[1] - top[1])   # obraz má y smerom dole
    if abs(dy) < 1e-6:
        return float("nan")
    return math.degrees(math.atan2(dx, dy))


def _sample(arr: np.ndarray, t: float) -> np.ndarray:
    """Lineárna interpolácia poľa (n, ...) v desatinnom čase snímku t."""
    n = arr.shape[0]
    t = min(max(t, 0.0), n - 1.0)
    i0 = int(math.floor(t))
    i1 = min(i0 + 1, n - 1)
    f = t - i0
    return arr[i0] * (1 - f) + arr[i1] * f


def _nan_stats(values) -> Dict[str, Optional[float]]:
    v = np.array([x for x in values if x is not None and np.isfinite(x)], dtype=float)
    if v.size == 0:
        return {"mean": None, "sd": None, "min": None, "max": None, "n": 0}
    return {
        "mean": float(np.mean(v)),
        "sd": float(np.std(v, ddof=1)) if v.size > 1 else 0.0,
        "min": float(np.min(v)),
        "max": float(np.max(v)),
        "n": int(v.size),
    }


# ---------------------------------------------------------------------------
# Kontrola prostredia
# ---------------------------------------------------------------------------

def dependencies_ok() -> tuple[bool, str]:
    missing = []
    if cv2 is None:
        missing.append("opencv-python")
    if mp is None:
        missing.append("mediapipe")
    if missing:
        return False, (
            "Chýbajú knižnice: " + ", ".join(missing) +
            ".  Nainštaluj ich príkazom:  pip install " + " ".join(missing)
        )
    return True, "OK"


# ---------------------------------------------------------------------------
# Rozpoznávanie kostry – podpora oboch verzií MediaPipe
# ---------------------------------------------------------------------------
# Staršie verzie (<= 0.10.21) mali jednoduché API mp.solutions.pose.
# Novšie verzie ho zrušili a majú len tzv. Tasks API, ktoré navyše potrebuje
# stiahnutý model (.task).  Modul zvládne obe – vyberie, čo je k dispozícii.

POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)
POSE_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
POSE_MODEL_PATH = os.path.join(POSE_MODEL_DIR, "pose_landmarker_full.task")


def _has_legacy_solutions() -> bool:
    return mp is not None and hasattr(mp, "solutions") and hasattr(mp.solutions, "pose")


def ensure_pose_model(progress_cb: Optional[Callable[[float, str], None]] = None) -> str:
    """Stiahne model kostry, ak ešte nie je na disku (len pre nové MediaPipe)."""
    if os.path.exists(POSE_MODEL_PATH) and os.path.getsize(POSE_MODEL_PATH) > 100000:
        return POSE_MODEL_PATH
    os.makedirs(POSE_MODEL_DIR, exist_ok=True)
    if progress_cb:
        progress_cb(0.03, "Sťahujem model kostry (jednorazovo, ~9 MB)")
    try:
        import urllib.request
        tmp = POSE_MODEL_PATH + ".part"
        urllib.request.urlretrieve(POSE_MODEL_URL, tmp)
        os.replace(tmp, POSE_MODEL_PATH)
    except Exception as e:
        raise VideoAnalysisError(
            "Nepodarilo sa stiahnuť model kostry pre MediaPipe.\n"
            f"Chyba: {e}\n\n"
            "Riešenie A: stiahni súbor ručne z\n  " + POSE_MODEL_URL +
            "\na ulož ho ako\n  " + POSE_MODEL_PATH + "\n\n"
            "Riešenie B: nainštaluj staršiu verziu, ktorá model nepotrebuje:\n"
            "  pip install \"mediapipe==0.10.21\""
        )
    return POSE_MODEL_PATH


class _PoseRunner:
    """Jednotné rozhranie nad oboma verziami MediaPipe."""

    def __init__(self, model_complexity: int = 1,
                 progress_cb: Optional[Callable[[float, str], None]] = None):
        self.legacy = _has_legacy_solutions()
        if self.legacy:
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=model_complexity,
                smooth_landmarks=True,
                enable_segmentation=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        else:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            model_path = ensure_pose_model(progress_cb)
            options = vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._pose = vision.PoseLandmarker.create_from_options(options)

    def process(self, rgb: np.ndarray, timestamp_ms: int):
        """Vráti zoznam 33 bodov (x, y, visibility) v normalizovaných súradniciach."""
        if self.legacy:
            rgb.flags.writeable = False
            res = self._pose.process(rgb)
            if not res.pose_landmarks:
                return None
            return [(l.x, l.y, l.visibility) for l in res.pose_landmarks.landmark]
        # Tasks API vyžaduje prísne rastúce časové značky (aj keď sa jeden
        # snímok spracúva dvakrát – výrez a potom celý obraz).
        last = getattr(self, "_last_ts", -1)
        timestamp_ms = max(int(timestamp_ms), last + 1)
        self._last_ts = timestamp_ms
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self._pose.detect_for_video(image, timestamp_ms)
        if not res.pose_landmarks:
            return None
        # Tasks API: visibility býva None, keď ju model neposkytol -> berie sa 1.0
        return [(l.x, l.y, (l.visibility if getattr(l, "visibility", None) is not None else 1.0))
                for l in res.pose_landmarks[0]]

    def close(self):
        try:
            self._pose.close()
        except Exception:
            pass


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def open_capture(path: str):
    """
    Otvorí video tak, aby sa rešpektovala značka otočenia v metadátach.

    Telefón držaný na výšku uloží video ako 1920x1080 + „otoč o 90°“.  Staršie
    OpenCV (napr. 4.10) túto značku štandardne ignoruje a snímky vracia
    naležato – bežec je potom „položený“ a kostra je nezmysel.  Zapnutím
    CAP_PROP_ORIENTATION_AUTO sa snímky otočia správne a aj CAP_PROP_FRAME_WIDTH
    / HEIGHT hlásia otočené rozmery.
    """
    cap = cv2.VideoCapture(path)
    try:
        prop = getattr(cv2, "CAP_PROP_ORIENTATION_AUTO", None)
        if prop is not None and cap.isOpened():
            cap.set(prop, 1)
    except Exception:
        pass
    return cap


def probe_video(path: str) -> Dict[str, Any]:
    """Základné info o videu; fps sa overuje aj cez ffprobe (spoľahlivejšie)."""
    if cv2 is None:
        raise VideoAnalysisError("OpenCV nie je nainštalované.")
    cap = open_capture(path)
    if not cap.isOpened():
        raise VideoAnalysisError("Video sa nepodarilo otvoriť. Skús formát MP4 alebo MOV.")
    info = {
        "fps": float(cap.get(cv2.CAP_PROP_FPS) or 0),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
    }
    cap.release()

    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-select_streams", "v:0", path],
                capture_output=True, text=True, timeout=20,
            )
            data = json.loads(out.stdout or "{}")
            st = (data.get("streams") or [{}])[0]
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate")
            if rate and "/" in rate:
                num, den = rate.split("/")
                if float(den) > 0:
                    info["fps_ffprobe"] = float(num) / float(den)
        except Exception:
            pass

    info["duration_s"] = info["frames"] / info["fps"] if info["fps"] > 0 else 0.0
    return info


# ---------------------------------------------------------------------------
# 1. krok – extrakcia kostry
# ---------------------------------------------------------------------------

class _RoiTracker:
    """
    Výrez (ROI) okolo bežca.

    Model kostry pracuje vnútri na malom obrázku (~256 px).  Keď je bežec na
    zábere z diaľky (celá dráha v obraze, prekážky zo štadióna), zaberá po
    zmenšení na 640 px len pár desiatok pixelov a body nôh sú nepresné.  Preto
    sa okolo bežca vystrihne štvorec z PÔVODNÉHO rozlíšenia a modelu sa podá
    ten – bežec v ňom zaberá väčšinu obrázka.  Výrez sa posúva plynulo za
    bežcom; keď sa rozpoznávanie stratí, vráti sa na celý obraz.

    Rovnaký mechanizmus umožňuje vybrať konkrétneho človeka, keď je v zábere
    viac ľudí (klik do prvého snímku -> `init_point`).
    """

    def __init__(self, width: int, height: int,
                 init_point: Optional[tuple] = None,
                 enable_below: float = 0.62, disable_above: float = 0.80):
        self.W, self.H = float(width), float(height)
        self.box: Optional[List[float]] = None       # x0, y0, x1, y1 (px)
        self.size: Optional[float] = None
        self.misses = 0
        self.enable_below = enable_below
        self.disable_above = disable_above
        self.frames_cropped = 0
        if init_point is not None:
            # Používateľ vybral konkrétneho človeka – výrez sa drží stále,
            # aj keď je bežec veľký (inak by si model mohol vybrať iného).
            self.enable_below = 10.0
            self.disable_above = 10.0
            cx = float(init_point[0]) * self.W
            cy = float(init_point[1]) * self.H
            side = 0.55 * self.H
            self.size = side
            self.box = self._clamp(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)

    def _clamp(self, x0, y0, x1, y1) -> List[float]:
        w, h = x1 - x0, y1 - y0
        x0 = min(max(0.0, x0), max(0.0, self.W - w))
        y0 = min(max(0.0, y0), max(0.0, self.H - h))
        return [x0, y0, min(self.W, x0 + w), min(self.H, y0 + h)]

    def update(self, p_full: Optional[np.ndarray], v: Optional[np.ndarray]):
        """Po každom snímku: posunie výrez podľa nájdenej kostry."""
        if p_full is None or v is None:
            self.misses += 1
            if self.misses >= 12:            # bežec sa stratil -> celý obraz
                self.box = None
                self.size = None
            return
        good = np.isfinite(p_full[:, 0]) & (v > 0.3)
        if good.sum() < 8:
            self.misses += 1
            if self.misses >= 12:
                self.box, self.size = None, None
            return
        self.misses = 0
        # Na určenie veľkosti sa berú aj menej isté body (časť tela mimo výrezu
        # model dopočíta s nižšou viditeľnosťou) – inak by výrez ostal malý.
        loose = np.isfinite(p_full[:, 0]) & (v > 0.1)
        xs, ys = p_full[loose, 0], p_full[loose, 1]
        bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
        bh = max(by1 - by0, 1.0)
        bw = max(bx1 - bx0, 1.0)

        # Hysteréza: veľkého bežca netreba vystrihovať
        frac = bh / self.H
        if self.box is None and frac > self.enable_below:
            return
        if self.box is not None and frac > self.disable_above:
            self.box, self.size = None, None
            return

        want = max(1.45 * bh, 1.25 * bw, 0.18 * self.H)
        want = min(want, min(self.W, self.H))
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if self.box is None:
            self.size = want
            side = self.size
            self.box = self._clamp(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
            return
        # Keď postava presahuje výrez, výrez sa zväčší hneď; zmenšuje sa pomaly
        margin = 0.12 * self.size
        near_edge = (bx0 < self.box[0] + margin or bx1 > self.box[2] - margin or
                     by0 < self.box[1] + margin or by1 > self.box[3] - margin)
        if want > self.size:
            self.size = want if near_edge else self.size + 0.35 * (want - self.size)
        else:
            self.size += 0.10 * (want - self.size)
        side = self.size
        ox = (self.box[0] + self.box[2]) / 2
        oy = (self.box[1] + self.box[3]) / 2
        # Plynulé sledovanie; keď sa bežec blíži k okraju výrezu, skočí hneď
        a = 1.0 if near_edge else 0.35
        nx, ny = ox + a * (cx - ox), oy + a * (cy - oy)
        self.box = self._clamp(nx - side / 2, ny - side / 2, nx + side / 2, ny + side / 2)

    def crop(self, frame: np.ndarray):
        """Vráti (výrez, x0, y0, w, h) alebo None, keď sa výrez nepoužíva."""
        if self.box is None:
            return None
        x0, y0, x1, y1 = [int(round(v)) for v in self.box]
        if x1 - x0 < 32 or y1 - y0 < 32:
            return None
        self.frames_cropped += 1
        return frame[y0:y1, x0:x1], x0, y0, x1 - x0, y1 - y0


def extract_landmarks(
    path: str,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    work_width: int = 640,
    model_complexity: int = 1,
    with_flow: bool = True,
    roi_track: bool = True,
    init_point: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    Prejde video snímok po snímku a vráti pole bodov kostry v pixeloch.

    `roi_track`  – keď je bežec v zábere malý, model dostane výrez okolo neho
                   z plného rozlíšenia (výrazne presnejšie body nôh).
    `init_point` – (x, y) v rozsahu 0–1: kde v prvom snímku je bežec.  Použije
                   sa, keď je v zábere viac ľudí (preteky) – výrez začne tam.
    """
    ok, msg = dependencies_ok()
    if not ok:
        raise VideoAnalysisError(msg)

    cap = open_capture(path)
    if not cap.isOpened():
        raise VideoAnalysisError("Video sa nepodarilo otvoriť. Skús formát MP4 alebo MOV.")

    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if n_frames > max_frames:
        cap.release()
        raise VideoAnalysisError(
            f"Video má {n_frames} snímok, maximum je {max_frames}. "
            "Zostrih na kratší úsek (stačí 3–5 sekúnd behu, pri prekážkach "
            "úsek s 2–4 prekážkami)."
        )

    # POZOR: rozmery sa berú zo skutočnej snímky, nie z CAP_PROP_FRAME_WIDTH.
    # Telefón otočený na výšku uloží video ako 1920x1080 + značku „otoč o 90°“;
    # OpenCV snímku otočí, ale CAP_PROP hlási pôvodné rozmery.  Prepočet
    # normalizovaných súradníc na pixely by potom bol prehodený a všetky
    # vzdialenosti (mierka, rýchlosť, dĺžka kroku) nezmyselné.
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise VideoAnalysisError("Vo videu nie sú žiadne snímky.")
    height, width = frame.shape[:2]

    scale = 1.0
    if max(width, height) > work_width:
        scale = work_width / max(width, height)

    fps_for_ts = float(cap.get(cv2.CAP_PROP_FPS) or 30.0) or 30.0
    pose = _PoseRunner(model_complexity=model_complexity, progress_cb=progress_cb)
    tracker = _FlowTracker() if with_flow else None
    roi = _RoiTracker(width, height, init_point=init_point) if roi_track else None

    pts: List[np.ndarray] = []
    vis: List[np.ndarray] = []
    frame_idx = 0
    try:
        while True:
            if frame_idx > 0:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                if frame.shape[0] != height or frame.shape[1] != width:
                    frame = cv2.resize(frame, (width, height))
            if frame_idx >= max_frames:
                break

            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            ts = int(frame_idx * 1000.0 / fps_for_ts)

            # --- kostra: buď z výrezu okolo bežca, alebo z celého obrazu ---
            lm = None
            if frame_idx == 0 and roi is not None and init_point is not None:
                # Prvý snímok: ak model na celom obraze nájde práve človeka, na
                # ktorého sa kliklo, výrez sa hneď prispôsobí jeho veľkosti.
                lm0 = pose.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB), ts)
                if lm0:
                    p0 = np.array([[x * width, y * height] for x, y, _ in lm0], dtype=float)
                    v0 = np.array([vv for _, _, vv in lm0], dtype=float)
                    g0 = np.isfinite(p0[:, 0]) & (v0 > 0.3)
                    if g0.sum() >= 8:
                        x0, x1 = p0[g0, 0].min(), p0[g0, 0].max()
                        y0, y1 = p0[g0, 1].min(), p0[g0, 1].max()
                        mx, my = 0.25 * (x1 - x0), 0.15 * (y1 - y0)
                        px_, py_ = init_point[0] * width, init_point[1] * height
                        if x0 - mx <= px_ <= x1 + mx and y0 - my <= py_ <= y1 + my:
                            roi.box = None
                            roi.update(p0, v0)
            cr = roi.crop(frame) if roi is not None else None
            if cr is not None:
                crop, cx0, cy0, cw, ch = cr
                cs = min(1.0, work_width / max(cw, ch))
                crop_in = cv2.resize(crop, None, fx=cs, fy=cs) if cs < 1.0 else crop
                lm_c = pose.process(cv2.cvtColor(crop_in, cv2.COLOR_BGR2RGB), ts)
                if lm_c:
                    # späť do normalizovaných súradníc CELÉHO obrazu
                    lm = [((cx0 + x * cw) / width, (cy0 + y * ch) / height, vv)
                          for x, y, vv in lm_c]
            if lm is None:
                lm = pose.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB), ts)

            if lm:
                p = np.array([[x * width, y * height] for x, y, _ in lm], dtype=float)
                v = np.array([vv for _, _, vv in lm], dtype=float)
            else:
                p = np.full((33, 2), np.nan)
                v = np.zeros(33)
            pts.append(p)
            vis.append(v)
            if roi is not None:
                roi.update(p if lm else None, v if lm else None)

            # Optický tok beží v tom istom prechode videom – druhý prechod
            # by zbytočne zdvojnásobil čas analýzy.
            if tracker is not None:
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                tracker.step(gray, lm, small.shape[1], small.shape[0])

            frame_idx += 1
            if progress_cb and (frame_idx % 10 == 0 or frame_idx == n_frames):
                frac = frame_idx / n_frames if n_frames else 0.0
                progress_cb(0.05 + 0.65 * min(frac, 1.0),
                            "Rozpoznávam kostru a počítam optický tok"
                            if tracker else "Rozpoznávam kostru")
    finally:
        cap.release()
        pose.close()

    if not pts:
        raise VideoAnalysisError("Vo videu nie sú žiadne snímky.")

    P = np.stack(pts)          # (n, 33, 2) v pixeloch
    V = np.stack(vis)          # (n, 33)
    detected = np.isfinite(P[:, L_HIP, 0])
    if detected.mean() < 0.35:
        raise VideoAnalysisError(
            "Postava bola rozpoznaná len na {:.0f} % snímok. "
            "Potrebujem video, kde je vidieť celého bežca z boku, "
            "dobre osvetleného a bez toho, aby ho niečo zakrývalo."
            .format(100 * detected.mean())
        )

    out = {"points": P, "visibility": V, "width": width, "height": height,
           "n_frames": P.shape[0], "detected_ratio": float(detected.mean()),
           "work_scale": scale,
           "crop_ratio": (roi.frames_cropped / P.shape[0]) if roi is not None else 0.0}
    if tracker is not None:
        out["flow"] = {
            "L": np.array([r["L"] for r in tracker.records], dtype=float),
            "R": np.array([r["R"] for r in tracker.records], dtype=float),
            "camera_px": np.array([r["camera_px"] for r in tracker.records], dtype=float),
            # posun pozadia (= -pohyb kamery) pri bežcovi, v px zmenšenej snímky
            "cam_dx": np.array([r["cam_dx"] for r in tracker.records], dtype=float),
            "cam_dy": np.array([r["cam_dy"] for r in tracker.records], dtype=float),
        }
    return out


# ---------------------------------------------------------------------------
# 1b. krok – OPTICKÝ TOK (čistý OpenCV, nezávislý na rozpoznávaní kostry)
# ---------------------------------------------------------------------------
#
# Druhá, úplne nezávislá metóda detekcie kontaktu.  Nepozerá sa na to, KDE
# chodidlo je, ale ako rýchlo sa HÝBE voči zemi:
#
#   1.  Lucas-Kanade optický tok (cv2.calcOpticalFlowPyrLK) sleduje body
#       vnútri chodidla medzi dvoma snímkami  ->  posun chodidla v obraze.
#   2.  Ten istý tok sa počíta na bodoch POZADIA a cez cv2.estimateAffinePartial2D
#       sa z nich odhadne pohyb kamery  ->  vieme, o koľko sa posunul celý obraz.
#   3.  Rozdiel oboch je posun chodidla VOČI ZEMI.  Počas opory je chodidlo
#       voči zemi nehybné, takže táto rýchlosť spadne takmer na nulu — a to
#       platí aj keď kamera uhýba za bežcom.
#
# Prečo to má zmysel: prvá metóda meria polohu, táto rýchlosť.  Keď sa zhodnú,
# je výsledok dôveryhodný; keď nie, appka to povie namiesto toho, aby tvrdila
# presnosť, ktorú nemá.

def _foot_box(lm_small, side: str, w: int, h: int) -> Optional[tuple]:
    """Obdĺžnik okolo chodidla v súradniciach zmenšenej snímky."""
    idx = (L_HEEL, L_TOE, L_ANKLE) if side == "L" else (R_HEEL, R_TOE, R_ANKLE)
    xs = [lm_small[i][0] * w for i in idx]
    ys = [lm_small[i][1] * h for i in idx]
    if not all(np.isfinite(v) for v in xs + ys):
        return None
    pad_x = max(12.0, 0.45 * (max(xs) - min(xs)) + 10)
    pad_y = max(12.0, 0.45 * (max(ys) - min(ys)) + 10)
    x0 = int(max(0, min(xs) - pad_x))
    x1 = int(min(w - 1, max(xs) + pad_x))
    y0 = int(max(0, min(ys) - pad_y))
    y1 = int(min(h - 1, max(ys) + pad_y))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return (x0, y0, x1, y1)


def _person_box(lm_small, w: int, h: int) -> Optional[tuple]:
    """Obdĺžnik okolo celej postavy – pri hľadaní pozadia sa vynechá."""
    xs = [p[0] * w for p in lm_small if np.isfinite(p[0])]
    ys = [p[1] * h for p in lm_small if np.isfinite(p[1])]
    if len(xs) < 5:
        return None
    mx = 0.12 * (max(xs) - min(xs)) + 20
    my = 0.12 * (max(ys) - min(ys)) + 20
    return (int(max(0, min(xs) - mx)), int(max(0, min(ys) - my)),
            int(min(w - 1, max(xs) + mx)), int(min(h - 1, max(ys) + my)))


_LK_PARAMS = dict(winSize=(21, 21), maxLevel=3,
                  criteria=(3, 30, 0.01))   # 3 == COUNT | EPS


def _track(prev_gray, gray, pts):
    """Lucas-Kanade: vráti dvojice (pôvodné, posunuté) bodov, ktoré sa podarilo nájsť."""
    if pts is None or len(pts) == 0:
        return None, None
    nxt, status, err = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None, **_LK_PARAMS)
    if nxt is None:
        return None, None
    # Spätná kontrola – bod musí ísť aj naspäť tam, odkiaľ prišiel
    back, status_b, _ = cv2.calcOpticalFlowPyrLK(gray, prev_gray, nxt, None, **_LK_PARAMS)
    if back is None:
        return None, None
    good = (status.ravel() == 1) & (status_b.ravel() == 1)
    good &= np.linalg.norm((back - pts).reshape(-1, 2), axis=1) < 1.5
    if good.sum() < 3:
        return None, None
    return pts[good], nxt[good]


class _FlowTracker:
    """Počíta posun chodidiel voči zemi medzi po sebe idúcimi snímkami."""

    def __init__(self):
        self.prev_gray = None
        self.prev_lm = None
        self.records = []      # (foot_dx_L, foot_dx_R, ok_L, ok_R) v px zmenšenej snímky

    def step(self, gray: np.ndarray, lm_small, w: int, h: int):
        out = {"L": np.nan, "R": np.nan, "camera_px": np.nan,
               "cam_dx": np.nan, "cam_dy": np.nan}
        if self.prev_gray is None or self.prev_lm is None or lm_small is None:
            self.prev_gray, self.prev_lm = gray, lm_small
            self.records.append(out)
            return out

        # --- 1. pohyb kamery z bodov pozadia ---
        mask = np.full(gray.shape, 255, dtype=np.uint8)
        pb = _person_box(self.prev_lm, w, h)
        if pb:
            mask[pb[1]:pb[3], pb[0]:pb[2]] = 0
        bg_pts = cv2.goodFeaturesToTrack(self.prev_gray, maxCorners=180,
                                         qualityLevel=0.01, minDistance=12, mask=mask)
        cam_M = None
        if bg_pts is not None and len(bg_pts) >= 6:
            a, b = _track(self.prev_gray, gray, bg_pts)
            if a is not None and len(a) >= 6:
                cam_M, inl = cv2.estimateAffinePartial2D(
                    a.reshape(-1, 2), b.reshape(-1, 2),
                    method=cv2.RANSAC, ransacReprojThreshold=2.0)

        # --- 2. pohyb chodidiel ---
        for side in ("L", "R"):
            box = _foot_box(self.prev_lm, side, w, h)
            if not box:
                continue
            x0, y0, x1, y1 = box
            m = np.zeros(gray.shape, dtype=np.uint8)
            m[y0:y1, x0:x1] = 255
            fpts = cv2.goodFeaturesToTrack(self.prev_gray, maxCorners=40,
                                           qualityLevel=0.005, minDistance=4, mask=m)
            a, b = _track(self.prev_gray, gray, fpts)
            if a is None:
                continue
            a2, b2 = a.reshape(-1, 2), b.reshape(-1, 2)

            if cam_M is not None:
                # Kam by sa body posunuli, keby stáli na zemi a hýbala sa len kamera
                ones = np.ones((len(a2), 1), dtype=np.float32)
                pred = (np.hstack([a2, ones]) @ cam_M.T)
                rel = b2 - pred
            else:
                rel = b2 - a2
            out[side] = float(np.median(np.linalg.norm(rel, axis=1)))

        if cam_M is not None:
            out["camera_px"] = float(np.hypot(cam_M[0, 2], cam_M[1, 2]))
            # Posun pozadia priamo pri bežcovi (v strede panvy).  Pri zoome
            # alebo natáčaní kamery nie je posun všade rovnaký, a nás zaujíma
            # práve okolie bežca – z neho sa neskôr skladá „svetová“ poloha
            # chodidiel a z nej dĺžka kroku.
            hx = [self.prev_lm[i][0] * w for i in (L_HIP, R_HIP)]
            hy = [self.prev_lm[i][1] * h for i in (L_HIP, R_HIP)]
            if all(np.isfinite(hx + hy)):
                p0 = np.array([np.mean(hx), np.mean(hy), 1.0])
                p1 = cam_M @ p0
                out["cam_dx"] = float(p1[0] - p0[0])
                out["cam_dy"] = float(p1[1] - p0[1])
            else:
                out["cam_dx"] = float(cam_M[0, 2])
                out["cam_dy"] = float(cam_M[1, 2])

        self.prev_gray, self.prev_lm = gray, lm_small
        self.records.append(out)
        return out


def flow_contacts(speed: np.ndarray, fps: float,
                  quiet_frac: float = 0.22) -> List[List[int]]:
    """
    Z rýchlosti chodidla voči zemi nájde intervaly, keď je takmer nulová.
    `speed` je v dĺžkach trupu za sekundu, takže prah nezávisí od rozlíšenia.
    """
    s = np.asarray(speed, dtype=float)
    good = s[np.isfinite(s)]
    if good.size < 10:
        return []
    hi = float(np.percentile(good, 85))     # typická rýchlosť vo švihu
    if hi <= 1e-6:
        return []
    thr = quiet_frac * hi

    n = s.size
    quiet = np.isfinite(s) & (s < thr)
    out: List[List[int]] = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j + 1 < n and quiet[j + 1]:
                j += 1
            out.append([i, j])
            i = j + 1
        else:
            i += 1

    gap = max(1, int(round(0.02 * fps)))
    merged: List[List[int]] = []
    for iv in out:
        if merged and iv[0] - merged[-1][1] <= gap:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)
    lo = max(1, int(round(MIN_CONTACT_S * fps * 0.6)))
    return [iv for iv in merged if (iv[1] - iv[0] + 1) >= lo]


# ---------------------------------------------------------------------------
# 2. krok – signály normalizované voči telu
# ---------------------------------------------------------------------------

def build_signals(P: np.ndarray, V: np.ndarray, fps: float,
                  hip_ref_window_s: float = 0.80) -> Dict[str, Any]:
    n = P.shape[0]

    # Body sa najprv vyhladia a doplnia
    Q = P.copy()
    # Vyhladenie cez okno ~20 ms.  Zámerne krátke: dlhšie okno by zaoblilo
    # ostrý zlom pri dopade a predĺžilo nameraný čas kontaktu.
    win = max(3, int(round(0.020 * fps)) // 2 * 2 + 1)
    for j in range(33):
        for k in range(2):
            s = _interp_nans(Q[:, j, k])
            Q[:, j, k] = _savgol(np.nan_to_num(s, nan=np.nanmean(s) if np.isfinite(s).any() else 0.0),
                                 win, 2)
            Q[~np.isfinite(s), j, k] = np.nan

    hip = (Q[:, L_HIP] + Q[:, R_HIP]) / 2.0
    sho = (Q[:, L_SHOULDER] + Q[:, R_SHOULDER]) / 2.0

    # Mierka tela = dĺžka trupu (stabilná, málokedy zakrytá)
    torso = np.linalg.norm(sho - hip, axis=1)
    torso = _interp_nans(torso)
    torso_ref = float(np.nanmedian(torso))
    if not np.isfinite(torso_ref) or torso_ref < 5:
        raise VideoAnalysisError("Nepodarilo sa určiť mierku postavy vo videu.")
    torso = np.where(np.isfinite(torso), torso, torso_ref)

    # Smer behu: špička je vpredu oproti päte
    toe_minus_heel = np.nanmean(
        np.concatenate([Q[:, L_TOE, 0] - Q[:, L_HEEL, 0],
                        Q[:, R_TOE, 0] - Q[:, R_HEEL, 0]])
    )
    direction = 1.0 if (np.isfinite(toe_minus_heel) and toe_minus_heel >= 0) else -1.0

    # Referenčná zvislá čiara pre výšku chodidla.
    # Panva sama hore-dole kmitá (~2 cm) v rytme krokov.  Keby sme od chodidla
    # odčítali priamo výšku panvy, toto kmitanie by sa premietlo do signálu
    # chodidla a rozbilo by detekciu.  Preto sa odčítava PRIEMEROVANÁ výška
    # panvy cez okno dlhšie než jeden krok – tým sa odstráni len pomalý pohyb
    # kamery (naklápanie, zoom), nie vlastné kmitanie tela.
    hip_ref_win = max(5, int(round(hip_ref_window_s * fps)) // 2 * 2 + 1)
    hip_y_ref = _savgol(np.nan_to_num(_interp_nans(hip[:, 1]),
                                      nan=float(np.nanmedian(hip[:, 1]))),
                        hip_ref_win, 2)

    feet = {}
    for side, heel, toe, ankle, knee, hip_i in (
        ("L", L_HEEL, L_TOE, L_ANKLE, L_KNEE, L_HIP),
        ("R", R_HEEL, R_TOE, R_ANKLE, R_KNEE, R_HIP),
    ):
        heel_y, toe_y = Q[:, heel, 1], Q[:, toe, 1]
        low_y = np.nanmax(np.stack([heel_y, toe_y]), axis=0)   # najnižší bod chodidla
        rel_y = (low_y - hip_y_ref) / torso     # voči vyhladenej panve, bezrozmerné
        rel_y = _interp_nans(rel_y)

        heel_x_rel = direction * (Q[:, heel, 0] - hip[:, 0]) / torso
        toe_x_rel = direction * (Q[:, toe, 0] - hip[:, 0]) / torso
        heel_x_rel = _interp_nans(heel_x_rel)
        toe_x_rel = _interp_nans(toe_x_rel)

        conf = np.nanmean(np.stack([V[:, heel], V[:, toe], V[:, ankle]]), axis=0)

        feet[side] = {
            "rel_y": rel_y,
            "heel_x_rel": heel_x_rel,
            "toe_x_rel": toe_x_rel,
            "foot_x_px": np.nanmean(np.stack([Q[:, heel, 0], Q[:, toe, 0]]), axis=0),
            # Špička je počas opory najstabilnejší bod (chodidlo sa okolo nej
            # odvaľuje), preto sa používa ako referencia pre výpočet rýchlosti.
            "toe_x_px": Q[:, toe, 0],
            "conf": conf,
        }

    return {
        "Q": Q, "hip": hip, "shoulder": sho, "torso": torso,
        "torso_ref": torso_ref, "direction": direction, "feet": feet, "n": n,
        "hip_y_ref": hip_y_ref,
    }


# ---------------------------------------------------------------------------
# 3. krok – detekcia kontaktov
# ---------------------------------------------------------------------------

def detect_contacts(sig: Dict[str, Any], side: str, fps: float,
                    threshold_frac: float = DEFAULT_THRESHOLD_FRAC) -> List[Dict[str, Any]]:
    """Vráti zoznam kontaktov {td, to} v desatinných snímkoch."""
    f = sig["feet"][side]
    rel_y = f["rel_y"]
    n = rel_y.size

    finite = rel_y[np.isfinite(rel_y)]
    if finite.size < 10:
        return []

    # --- Metóda A: polohová (Zeni) – približné udalosti ---------------------
    min_dist = max(2, int(round(MIN_STEP_S * fps)))
    td_cand = _find_extrema(f["heel_x_rel"], min_dist, "max")
    to_cand = _find_extrema(f["toe_x_rel"], min_dist, "min")

    # --- Metóda B: vertikálna – pásmo pri zemi ------------------------------
    # Úroveň zeme sa odhaduje kĺzavo, aby zvládla nerovný terén / pohyb kamery
    stride_frames = max(min_dist * 2, int(round(0.7 * fps)))
    ground = _rolling_max(np.where(np.isfinite(rel_y), rel_y, -np.inf), stride_frames)
    ground = _savgol(ground, max(3, stride_frames // 2 * 2 + 1), 1)
    amp = float(np.nanpercentile(finite, 97) - np.nanpercentile(finite, 5))
    if amp <= 0:
        return []
    level = ground - threshold_frac * amp
    # Schmittov klopný obvod: do kontaktu sa vstupuje pri prísnejšom prahu,
    # vystupuje pri voľnejšom.  Zabraňuje "blikaniu" pri zašumenej kostre.
    level_in = level
    level_out = ground - (threshold_frac + 0.05) * amp

    ok_frame = np.isfinite(rel_y)
    intervals: List[List[int]] = []
    i = 0
    while i < n:
        if ok_frame[i] and rel_y[i] >= level_in[i]:
            j = i
            while j + 1 < n and ok_frame[j + 1] and rel_y[j + 1] >= level_out[j + 1]:
                j += 1
            intervals.append([i, j])
            i = j + 1
        else:
            i += 1

    # Zlúčenie úsekov oddelených krátkou medzerou (šum v detekcii kostry)
    merged: List[List[int]] = []
    gap_tol = max(1, int(round(0.025 * fps)))
    for iv in intervals:
        if merged and iv[0] - merged[-1][1] <= gap_tol:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)

    contacts: List[Dict[str, Any]] = []
    for a, b in merged:
        # Sub-frame spresnenie okrajov: najprv prah, potom priesečník priamok
        td = float(a)
        if a > 0:
            td = _cross_time(rel_y, a - 1, a, level[a])
            td = _refine_edge(rel_y, a, fps, rising=True, fallback=td)
        to = float(b)
        if b < n - 1:
            to = _cross_time(rel_y, b, b + 1, level[b])
            to = _refine_edge(rel_y, b, fps, rising=False, fallback=to)

        dur = (to - td) / fps
        if dur < MIN_CONTACT_S or dur > MAX_CONTACT_S:
            continue

        # Zhoda s polohovou metódou (kontrola kvality)
        zeni_td = min(td_cand, key=lambda c: abs(c - td)) if td_cand else None
        zeni_to = min(to_cand, key=lambda c: abs(c - to)) if to_cand else None
        agree_td = abs(zeni_td - td) / fps * 1000 if zeni_td is not None else None
        agree_to = abs(zeni_to - to) / fps * 1000 if zeni_to is not None else None

        conf = float(np.nanmean(f["conf"][a:b + 1])) if b >= a else 0.0
        if not np.isfinite(conf):
            conf = 0.0

        contacts.append({
            "side": side,
            "td_frame": td, "to_frame": to,
            "td_time": td / fps, "to_time": to / fps,
            "contact_s": dur,
            "frames_in_contact": b - a + 1,
            "confidence": conf,
            "agree_td_ms": agree_td,
            "agree_to_ms": agree_to,
        })

    # Odstránenie zjavných výstrelkov (spojené alebo rozpadnuté kontakty).
    # Robí sa až pri dostatku krokov, aby sa nezamaskovala skutočná asymetria.
    if len(contacts) >= 5:
        med = float(np.median([c["contact_s"] for c in contacts]))
        contacts = [c for c in contacts if 0.5 * med <= c["contact_s"] <= 1.9 * med]

    return contacts


def dedupe_contacts(contacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pri behu nie sú nikdy obe nohy na zemi naraz.  Keď sa ľavý a pravý kontakt
    v čase prekrývajú, MediaPipe si vymenil strany alebo jednu nohu nakreslil
    na miesto druhej – ponechá sa kontakt s vyššou dôverou kostry.
    """
    out: List[Dict[str, Any]] = []
    for c in sorted(contacts, key=lambda c: c["td_time"]):
        if out:
            p = out[-1]
            overlap = min(p["to_time"], c["to_time"]) - max(p["td_time"], c["td_time"])
            shorter = min(p["contact_s"], c["contact_s"])
            if overlap > 0.5 * shorter:
                if c.get("confidence", 0) > p.get("confidence", 0):
                    out[-1] = c
                continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 4. krok – uhly v kĺboch
# ---------------------------------------------------------------------------

def joint_angles_at(sig: Dict[str, Any], t_frame: float, side: str) -> Dict[str, Optional[float]]:
    Q = sig["Q"]
    pt = _sample(Q, t_frame)
    hip_i, knee_i, ankle_i, toe_i, sho_i = (
        (L_HIP, L_KNEE, L_ANKLE, L_TOE, L_SHOULDER) if side == "L"
        else (R_HIP, R_KNEE, R_ANKLE, R_TOE, R_SHOULDER)
    )
    mid_hip = (pt[L_HIP] + pt[R_HIP]) / 2.0
    mid_sho = (pt[L_SHOULDER] + pt[R_SHOULDER]) / 2.0
    d = sig["direction"]

    def clean(x):
        return None if (x is None or not np.isfinite(x)) else round(float(x), 1)

    knee = _angle(pt[hip_i], pt[knee_i], pt[ankle_i])
    hip_ang = _angle(pt[sho_i], pt[hip_i], pt[knee_i])
    trunk = _angle_from_vertical(mid_sho, mid_hip) * d
    shank = _angle_from_vertical(pt[knee_i], pt[ankle_i]) * d
    # stehno: + = koleno pred bedrom (v smere behu), − = koleno za bedrom
    thigh = -_angle_from_vertical(pt[hip_i], pt[knee_i]) * d

    return {
        "knee_deg": clean(knee),
        "hip_deg": clean(hip_ang),
        "trunk_lean_deg": clean(trunk),
        "shank_deg": clean(shank),
        "thigh_deg": clean(thigh),
    }


# ---------------------------------------------------------------------------
# 5. krok – kalibrácia a rýchlosť
# ---------------------------------------------------------------------------

# Podiely dĺžok segmentov na telesnej výške (Winter, 2009):
#   trup (bedro–rameno) 0.818-0.530 = 0.288,  stehno 0.245,  predkolenie 0.246
SEGMENT_FRACTIONS = {
    "leg": LEG_TO_HEIGHT,     # bedro–členok pri vystretej nohe
    "thigh": 0.245,
    "shank": 0.246,
    "torso": 0.288,
}


def estimate_scale_detail(sig: Dict[str, Any],
                          athlete_height_cm: Optional[float]) -> Optional[Dict[str, Any]]:
    """
    Metre na pixel z výšky atléta.

    Pôvodne sa mierka brala len z dĺžky nohy (bedro–členok) pri vystretí.
    Jeden bod kostry (napr. bedro, ktoré MediaPipe kreslí kúsok inde než je
    kĺb) ale vie mierku posunúť o 5–10 %.  Teraz sa výška odhadne zo štyroch
    segmentov nezávisle a berie sa medián – jeden zle rozpoznaný bod výsledok
    nepokazí.  Vracia aj rozptyl odhadov, čo je kontrola kvality kostry.
    """
    if not athlete_height_cm:
        return None
    Q = sig["Q"]
    H_m = athlete_height_cm / 100.0

    def seg(a, b, pct):
        d = np.concatenate([np.linalg.norm(Q[:, a[0]] - Q[:, b[0]], axis=1),
                            np.linalg.norm(Q[:, a[1]] - Q[:, b[1]], axis=1)])
        d = d[np.isfinite(d)]
        if d.size < 5:
            return None
        return float(np.percentile(d, pct))

    px = {
        # p95 = takmer vystretá noha (pri odraze a pred dopadom)
        "leg": seg((L_HIP, R_HIP), (L_ANKLE, R_ANKLE), 95),
        "thigh": seg((L_HIP, R_HIP), (L_KNEE, R_KNEE), 95),
        "shank": seg((L_KNEE, R_KNEE), (L_ANKLE, R_ANKLE), 95),
        # trup sa počas behu nemení -> medián
        "torso": seg((L_HIP, R_HIP), (L_SHOULDER, R_SHOULDER), 50),
    }
    estimates = {}
    for k, v in px.items():
        if v is not None and v > 5:
            estimates[k] = SEGMENT_FRACTIONS[k] * H_m / v
    if not estimates:
        return None
    vals = np.array(list(estimates.values()))
    m_per_px = float(np.median(vals))
    spread = float((vals.max() - vals.min()) / m_per_px) if m_per_px > 0 else None
    return {
        "m_per_px": m_per_px,
        "estimates": {k: round(float(v), 6) for k, v in estimates.items()},
        "segments_px": {k: round(float(v), 1) for k, v in px.items() if v is not None},
        "spread_pct": round(spread * 100, 1) if spread is not None else None,
        "body_height_px": round(H_m / m_per_px, 1),
    }


def estimate_scale(sig: Dict[str, Any], athlete_height_cm: Optional[float]) -> Optional[float]:
    """Metre na pixel z výšky atléta (medián zo štyroch segmentov tela)."""
    d = estimate_scale_detail(sig, athlete_height_cm)
    return d["m_per_px"] if d else None


def estimate_speed(sig: Dict[str, Any], contacts: List[Dict[str, Any]],
                   m_per_px: Optional[float], fps: float) -> Dict[str, Any]:
    """
    Rýchlosť z posunu panvy voči stojnej nohe.
    Počas kontaktu je chodidlo nehybné voči zemi, takže zmena vzdialenosti
    panva–chodidlo = skutočný posun tela.  Funguje aj s pohyblivou kamerou.
    """
    out: Dict[str, Any] = {"method": None, "speed_ms": None, "per_contact": []}
    if not contacts:
        return out

    hip_x = sig["hip"][:, 0]
    speeds = []
    for c in contacts:
        f = sig["feet"][c["side"]]
        # Meria sa len VNÚTRO opory (25 % – 75 %), aby sa do vzorky nedostali
        # snímky tesne pri dopade/odraze, kde sa chodidlo ešte hýbe.
        span = c["to_frame"] - c["td_frame"]
        a = c["td_frame"] + 0.25 * span
        b = c["td_frame"] + 0.75 * span
        rel = (hip_x - f["toe_x_px"]).reshape(-1, 1)
        dpx = abs(_sample(rel, b)[0] - _sample(rel, a)[0])
        dt = 0.5 * span / fps
        if dt <= 0 or not np.isfinite(dpx):
            continue
        speeds.append({"side": c["side"], "px_per_s": dpx / dt})

    if not speeds:
        return out

    px_per_s = float(np.median([s["px_per_s"] for s in speeds]))
    out["px_per_s"] = px_per_s
    out["per_contact"] = speeds
    if m_per_px:
        out["speed_ms"] = px_per_s * m_per_px
        out["method"] = "stojná noha + kalibrácia z výšky atléta"
    else:
        out["method"] = "bez kalibrácie (zadaj výšku pre m/s)"
    return out


def _fit_len(arr: np.ndarray, n: int) -> np.ndarray:
    """Skráti alebo doplní (NaN) pole na dĺžku n – nikdy neopakuje dáta."""
    arr = np.asarray(arr, dtype=float)
    if arr.size == n:
        return arr
    if arr.size > n:
        return arr[:n]
    return np.concatenate([arr, np.full(n - arr.size, np.nan)])


def camera_track(lm_flow: Optional[Dict[str, Any]], work_scale: float, n: int,
                 fps: float) -> Dict[str, Any]:
    """
    Kumulatívny posun pozadia v pixeloch PÔVODNÉHO videa.

    Optický tok dáva pre každý snímok, o koľko sa posunulo pozadie pri bežcovi
    (= mínus pohyb kamery).  Sčítaním vznikne „svetová“ súradnica: keď sa od
    polohy chodidla v obraze odčíta tento posun, dostaneme polohu chodidla voči
    zemi, aj keď kamera uhýba za bežcom.  Statická kamera sa rozpozná a posun
    sa vtedy neintegruje vôbec (nesčítaval by sa len šum).
    """
    out = {"available": False, "static": True, "cum_x": np.zeros(n), "cum_y": np.zeros(n),
           "missing_ratio": 1.0, "median_abs_px": 0.0, "note": "bez optického toku"}
    if not lm_flow or "cam_dx" not in lm_flow:
        return out
    dx = _fit_len(lm_flow["cam_dx"], n) / max(float(work_scale), 1e-6)
    dy = _fit_len(lm_flow["cam_dy"], n) / max(float(work_scale), 1e-6)
    missing = float(np.mean(~np.isfinite(dx)))
    out["missing_ratio"] = round(missing, 3)
    good = dx[np.isfinite(dx)]
    if good.size < 5:
        out["note"] = "pozadie sa nedalo sledovať"
        return out
    med_abs = float(np.median(np.abs(good)))
    p90 = float(np.percentile(np.abs(good), 90))
    out["median_abs_px"] = round(med_abs, 2)
    if med_abs < 0.35 and p90 < 1.5:
        out.update(available=True, static=True, note="statická kamera")
        return out
    # Pohyblivá kamera: krátke výpadky sa doplnia, dlhé znamenajú nespoľahlivosť
    gap = max(2, int(round(0.15 * fps)))
    dx_f = _interp_nans(dx, max_gap=gap)
    dy_f = _interp_nans(dy, max_gap=gap)
    still_missing = float(np.mean(~np.isfinite(dx_f)))
    # Snímky v dlhých výpadkoch: posun sa tam nepozná – krok, ktorý cez ne
    # prechádza, sa neuzná (namiesto tichého skreslenia dĺžky).
    out["gap_mask"] = ~np.isfinite(dx_f)
    dx_f = np.nan_to_num(dx_f, nan=0.0)
    dy_f = np.nan_to_num(dy_f, nan=0.0)
    out["cum_x"] = np.cumsum(dx_f)
    out["cum_y"] = np.cumsum(dy_f)
    out["static"] = False
    if still_missing > 0.30 or missing > 0.5:
        out["available"] = False
        out["note"] = "kamera sa hýbe a pozadie sa nedalo sledovať"
    else:
        out["available"] = True
        out["note"] = "pohyblivá kamera, pohyb odpočítaný z pozadia"
    return out


def estimate_step_geometry(sig: Dict[str, Any], contacts: List[Dict[str, Any]],
                           cam: Dict[str, Any], fps: float,
                           m_per_px: Optional[float]) -> Dict[str, Any]:
    """
    Dĺžka kroku ako VZDIALENOSŤ medzi miestami dopadu dvoch po sebe idúcich
    krokov – tak, ako sa definuje v biomechanike.

    Prečo nie „rýchlosť × čas kroku“: rýchlosť zo stojnej nohy je citlivá na
    kvalitu kostry v pár snímkoch opory a keď sa pokazí, vyjde nezmysel (napr.
    0.3 m).  Miesto dopadu chodidla je naopak veľmi stabilné: chodidlo počas
    opory stojí, jeho poloha sa dá spriemerovať cez celú strednú časť opory.
    Nezávisí to od fps ani od času kroku.

    Pri pohyblivej kamere sa polohy prevedú do „sveta“ odčítaním posunu
    pozadia (`cam["cum_x"]`).  Kontrola kvality: chodidlo na zemi sa vo svete
    nesmie hýbať – ak sa hýbe, kompenzácia kamery zlyhala a krok sa neuzná.
    """
    n = sig["n"]
    d = sig["direction"]
    torso = float(sig["torso_ref"])
    cam = cam or {}
    cum = cam.get("cum_x")
    if cum is None or len(cum) != n:
        cum = np.zeros(n)
    gap_mask = cam.get("gap_mask")
    if gap_mask is None or len(gap_mask) != n:
        gap_mask = np.zeros(n, dtype=bool)
    usable_cam = bool(cam.get("available"))

    placements = []
    for c in contacts:
        f = sig["feet"][c["side"]]
        toe_x = f["toe_x_px"]
        span = c["to_frame"] - c["td_frame"]
        lo = c["td_frame"] + 0.25 * span
        hi = c["td_frame"] + 0.75 * span
        idx = np.arange(int(math.ceil(lo)), int(math.floor(hi)) + 1)
        idx = idx[(idx >= 0) & (idx < n)]
        if idx.size < 2:
            mid = 0.5 * (lo + hi)
            idx = np.unique(np.clip([int(math.floor(mid)), int(math.ceil(mid))], 0, n - 1))
        world = toe_x[idx] - cum[idx]
        ok = np.isfinite(world)
        rec = {"side": c["side"], "t_mid": float(0.5 * (c["td_frame"] + c["to_frame"]) / fps),
               "world_x": None, "drift_px": None, "image_drift_px": None, "valid": False,
               "n_mid": int(idx.size), "f_mid": int(idx[len(idx) // 2]) if idx.size else 0}
        if ok.sum() >= 1:
            w = world[ok]
            rec["world_x"] = float(np.mean(w))
            if ok.sum() >= 2:
                # Posun počas opory zo sklonu priamky (odolnejšie voči jednému
                # zašumenému bodu než rozdiel prvého a posledného)
                tt = np.arange(ok.sum(), dtype=float)
                if ok.sum() >= 3:
                    rec["drift_px"] = float(np.polyfit(tt, w, 1)[0] * (ok.sum() - 1))
                    rec["image_drift_px"] = float(np.polyfit(tt, toe_x[idx][ok], 1)[0] * (ok.sum() - 1))
                else:
                    rec["drift_px"] = float(w[-1] - w[0])
                    img = toe_x[idx][ok]
                    rec["image_drift_px"] = float(img[-1] - img[0])
            else:
                rec["drift_px"] = 0.0
                rec["image_drift_px"] = 0.0
            rec["valid"] = True
        placements.append(rec)

    per_step = []
    lengths_m, lengths_px, dts = [], [], []
    for i in range(len(placements) - 1):
        a, b = placements[i], placements[i + 1]
        item = {"from": i, "to": i + 1, "step_px": None, "step_m": None, "valid": False,
                "reason": None}
        if not (a["valid"] and b["valid"]):
            item["reason"] = "chodidlo sa nedalo sledovať"
            per_step.append(item)
            continue
        dx = (b["world_x"] - a["world_x"]) * d
        item["step_px"] = round(float(dx), 1)
        item["step_m"] = round(float(dx * m_per_px), 3) if m_per_px else None
        dt = b["t_mid"] - a["t_mid"]
        # kontroly
        if not usable_cam and not cam.get("static", True):
            item["reason"] = cam.get("note") or "pohyb kamery sa nedal odpočítať"
        elif dx <= 0:
            item["reason"] = "chodidlá v nesprávnom poradí (pravdepodobne zlá detekcia)"
        elif gap_mask[min(a["f_mid"], b["f_mid"]):max(a["f_mid"], b["f_mid"]) + 1].any():
            item["reason"] = "výpadok sledovania pozadia medzi dvoma dopadmi"
        else:
            # Chodidlo na zemi sa vo svete nesmie hýbať.  Posun (drift) sa meria
            # cez strednú polovicu opory, krok trvá niekoľkonásobne dlhšie –
            # preto sa tolerancia škáluje: chyba kompenzácie kamery, ktorá by
            # skreslila krok o viac než ~12 %, sa v drifte musí prejaviť.
            ratio = min(1.0, max(a["n_mid"], b["n_mid"], 1) / max(dt * fps, 1.0))
            tol = max(0.12 * dx * ratio, 0.08 * torso)
            if abs(a["drift_px"] or 0) > tol or abs(b["drift_px"] or 0) > tol:
                item["reason"] = "stojné chodidlo sa vo svete hýbe – kompenzácia kamery nesedí"
            elif m_per_px and not (MIN_STEP_LEN_M <= dx * m_per_px <= MAX_STEP_LEN_M):
                item["reason"] = "dĺžka mimo rozumného rozsahu"
            elif not m_per_px and not (1.0 * torso <= dx <= 6.5 * torso):
                item["reason"] = "dĺžka mimo rozumného rozsahu (v jednotkách trupu)"
            elif not (MIN_STEP_S <= dt <= MAX_STEP_S):
                item["reason"] = "čas medzi krokmi mimo rozsahu"
        item["valid"] = item["reason"] is None
        if item["valid"]:
            lengths_px.append(dx)
            dts.append(dt)
            if m_per_px:
                lengths_m.append(dx * m_per_px)
        per_step.append(item)

    out: Dict[str, Any] = {
        "available": bool(lengths_px),
        "n_valid": len(lengths_px),
        "n_steps": len(per_step),
        "per_step": per_step,
        "placements": placements,
        "step_px_mean": float(np.mean(lengths_px)) if lengths_px else None,
        "step_m_mean": float(np.mean(lengths_m)) if lengths_m else None,
        "step_m_sd": float(np.std(lengths_m, ddof=1)) if len(lengths_m) > 1 else None,
        "speed_ms": (float(np.sum(lengths_m) / np.sum(dts)) if lengths_m and np.sum(dts) > 0
                     else None),
        "px_per_s": (float(np.sum(lengths_px) / np.sum(dts)) if lengths_px and np.sum(dts) > 0
                     else None),
        "camera": {k: v for k, v in cam.items() if k not in ("cum_x", "cum_y", "gap_mask")},
    }
    return out


# ---------------------------------------------------------------------------
# 6. krok – kreslenie: kostra, uhly, prekryvné video, kľúčové snímky
# ---------------------------------------------------------------------------

ORANGE = (82, 120, 255)     # BGR – primárna farba appky #ff7852
BLUE = (234, 199, 184)      # #b8c7ea
RED = (60, 60, 255)
GREEN = (120, 220, 130)
YELLOW = (80, 220, 255)
WHITE = (255, 255, 255)


def _pt(p) -> tuple:
    return (int(round(float(p[0]))), int(round(float(p[1]))))


def _ascii(text: str) -> str:
    """OpenCV (Hershey fonty) nevie diakritiku – text sa prepíše bez nej."""
    import unicodedata
    out = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in out if not unicodedata.combining(ch)).replace("–", "-")


def _draw_label(frame, text, org, scale, color=WHITE, thick=1, bg=(0, 0, 0)):
    """Text s tmavým podkladom, aby bol čitateľný aj na svetlom pozadí."""
    text = _ascii(text)
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    x, y = int(org[0]), int(org[1])
    h, w = frame.shape[:2]
    x = min(max(2, x), max(2, w - tw - 4))
    y = min(max(th + 4, y), h - 4)
    sub = frame[max(0, y - th - 4):y + base + 2, max(0, x - 3):x + tw + 3]
    if sub.size:
        frame[max(0, y - th - 4):y + base + 2, max(0, x - 3):x + tw + 3] = cv2.addWeighted(
            sub, 0.35, np.full_like(sub, bg), 0.65, 0)
    cv2.putText(frame, text, (x, y), font, scale, color, thick, cv2.LINE_AA)


def _draw_angle_arc(frame, b, a, c, radius, color, thick):
    """Oblúk vnútorného uhla pri vrchole b medzi ramenami b->a a b->c."""
    v1 = np.array(a, float) - np.array(b, float)
    v2 = np.array(c, float) - np.array(b, float)
    if np.linalg.norm(v1) < 1 or np.linalg.norm(v2) < 1:
        return
    a1 = math.degrees(math.atan2(v1[1], v1[0]))
    a2 = math.degrees(math.atan2(v2[1], v2[0]))
    diff = (a2 - a1) % 360.0
    if diff > 180.0:
        a1, a2 = a2, a1
        diff = 360.0 - diff
    cv2.ellipse(frame, _pt(b), (int(radius), int(radius)), 0.0, a1, a1 + diff,
                color, thick, cv2.LINE_AA)


def draw_skeleton(frame, pts: np.ndarray, contact: Dict[str, bool], scale_f: float,
                  thick: int, angles: Optional[Dict[str, Any]] = None,
                  direction: float = 1.0):
    """Kostra + chodidlá (červené na zemi) + voliteľne uhly v kĺboch."""
    ok_pt = np.isfinite(pts[:, 0])
    for a_i, b_i in SKELETON:
        if ok_pt[a_i] and ok_pt[b_i]:
            cv2.line(frame, _pt(pts[a_i]), _pt(pts[b_i]), BLUE, thick, cv2.LINE_AA)
    for j in (L_KNEE, R_KNEE, L_HIP, R_HIP, L_SHOULDER, R_SHOULDER):
        if ok_pt[j]:
            cv2.circle(frame, _pt(pts[j]), max(2, int(4 * scale_f)), ORANGE, -1, cv2.LINE_AA)
    for side, heel, toe, ankle in (("L", L_HEEL, L_TOE, L_ANKLE), ("R", R_HEEL, R_TOE, R_ANKLE)):
        on = bool(contact.get(side))
        col = RED if on else GREEN
        for j in (heel, toe, ankle):
            if ok_pt[j]:
                cv2.circle(frame, _pt(pts[j]), max(3, int((7 if on else 5) * scale_f)),
                           col, -1, cv2.LINE_AA)
        if on and ok_pt[heel] and ok_pt[toe]:
            mid = (pts[heel] + pts[toe]) / 2
            cv2.circle(frame, _pt(mid), max(10, int(22 * scale_f)), RED, max(1, thick), cv2.LINE_AA)

    if angles is None:
        return
    fs = 0.55 * scale_f
    r = max(14, int(28 * scale_f))
    # kolená – oblúk + hodnota (oblúk je na vnútornej strane uhla)
    for side, hip_i, knee_i, ankle_i in (("L", L_HIP, L_KNEE, L_ANKLE), ("R", R_HIP, R_KNEE, R_ANKLE)):
        if ok_pt[hip_i] and ok_pt[knee_i] and ok_pt[ankle_i]:
            ang = _angle(pts[hip_i], pts[knee_i], pts[ankle_i])
            if np.isfinite(ang):
                col = ORANGE if side == "L" else (120, 200, 255)
                _draw_angle_arc(frame, pts[knee_i], pts[hip_i], pts[ankle_i], r, col, max(1, thick))
                # popis mimo nohy: na opačnú stranu než smer behu
                off = -direction * int(46 * scale_f)
                _draw_label(frame, f"{'L' if side == 'L' else 'P'} koleno {ang:.0f}",
                            (pts[knee_i][0] + off - (0 if direction < 0 else int(60 * scale_f)),
                             pts[knee_i][1] + int(6 * scale_f)), fs, col, 1)
    # trup – čiara rameno–bedro a zvislica, hodnota náklonu
    if ok_pt[L_HIP] and ok_pt[R_HIP] and ok_pt[L_SHOULDER] and ok_pt[R_SHOULDER]:
        mh = (pts[L_HIP] + pts[R_HIP]) / 2
        ms = (pts[L_SHOULDER] + pts[R_SHOULDER]) / 2
        lean = _angle_from_vertical(ms, mh) * direction
        if np.isfinite(lean):
            top = (mh[0], mh[1] - np.linalg.norm(ms - mh))
            cv2.line(frame, _pt(mh), _pt(top), (160, 160, 160), 1, cv2.LINE_AA)
            cv2.line(frame, _pt(mh), _pt(ms), YELLOW, max(1, thick), cv2.LINE_AA)
            _draw_angle_arc(frame, mh, ms, top, int(r * 1.3), YELLOW, 1)
            _draw_label(frame, f"trup {lean:+.0f}", (ms[0] + int(8 * scale_f), ms[1] - int(8 * scale_f)),
                        fs, YELLOW, 1)
    # holeň stojnej nohy – uhol od zvislice (mínus = chodidlo pred kolenom = brzdenie)
    for side, knee_i, ankle_i in (("L", L_KNEE, L_ANKLE), ("R", R_KNEE, R_ANKLE)):
        if contact.get(side) and ok_pt[knee_i] and ok_pt[ankle_i]:
            sh = _angle_from_vertical(pts[knee_i], pts[ankle_i]) * direction
            if np.isfinite(sh):
                _draw_label(frame, f"holen {sh:+.0f}",
                            (pts[ankle_i][0] + int(10 * scale_f), pts[ankle_i][1] - int(12 * scale_f)),
                            fs * 0.9, RED, 1)


def _contact_masks(contacts: List[Dict[str, Any]], n: int):
    in_contact = {"L": np.zeros(n, dtype=bool), "R": np.zeros(n, dtype=bool)}
    ct_of_frame = {"L": np.full(n, np.nan), "R": np.full(n, np.nan)}
    for c in contacts:
        a = int(math.floor(c["td_frame"]))
        b = int(math.ceil(c["to_frame"]))
        a, b = max(0, a), min(n - 1, b)
        in_contact[c["side"]][a:b + 1] = True
        ct_of_frame[c["side"]][a:b + 1] = c["contact_s"]
    return in_contact, ct_of_frame


def render_overlay(video_path: str, out_path: str, sig: Dict[str, Any],
                   contacts: List[Dict[str, Any]], fps: float,
                   progress_cb: Optional[Callable[[float, str], None]] = None,
                   draw_angles: bool = True,
                   events: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """
    Vykreslí kostru, zvýrazní kontakt so zemou, uhly v kĺboch a časy.
    `events` – voliteľné popisky (napr. prekážky): {"lo", "hi", "label"} v snímkoch.
    """
    if cv2 is None:
        return None
    cap = open_capture(video_path)
    if not cap.isOpened():
        return None

    n = sig["n"]
    Q = sig["Q"]
    direction = float(sig.get("direction", 1.0))

    ret, first = cap.read()
    if not ret or first is None:
        cap.release()
        return None
    h, w = first.shape[:2]

    tmp_path = out_path + ".tmp.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_path, fourcc, max(fps, 1.0), (w, h))
    if not writer.isOpened():
        cap.release()
        return None

    in_contact, ct_of_frame = _contact_masks(contacts, n)
    td_frames = {int(round(c["td_frame"])): c for c in contacts}
    to_frames = {int(round(c["to_frame"])): c for c in contacts}
    flash = max(2, int(round(0.03 * fps)))     # ako dlho svieti „DOPAD“/„ODRAZ“

    scale_f = max(0.5, w / 1280.0)
    thick = max(1, int(round(2 * scale_f)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.6 * scale_f
    pad = int(12 * scale_f)

    idx = 0
    frame = first
    while idx < n:
        if idx > 0:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            if frame.shape[0] != h or frame.shape[1] != w:
                frame = cv2.resize(frame, (w, h))

        pts = Q[idx]
        contact = {"L": bool(in_contact["L"][idx]), "R": bool(in_contact["R"][idx])}
        draw_skeleton(frame, pts, contact, scale_f, thick,
                      angles={} if draw_angles else None, direction=direction)

        # --- udalosti dopad / odraz pri chodidle ---
        for table, label in ((td_frames, "DOPAD"), (to_frames, "ODRAZ")):
            for fidx, c in table.items():
                if 0 <= idx - fidx < flash:
                    toe = L_TOE if c["side"] == "L" else R_TOE
                    if np.isfinite(pts[toe, 0]):
                        _draw_label(frame, f"{label} {'L' if c['side'] == 'L' else 'P'}",
                                    (pts[toe][0] - int(30 * scale_f), pts[toe][1] + int(34 * scale_f)),
                                    fs * 0.9, WHITE, max(1, thick - 1), bg=(30, 30, 200))

        # --- info panel ---
        box_h = int(78 * scale_f)
        box_w = int(300 * scale_f)
        sub = frame[pad:pad + box_h, pad:pad + box_w]
        if sub.size:
            frame[pad:pad + box_h, pad:pad + box_w] = cv2.addWeighted(
                sub, 0.25, np.zeros_like(sub), 0.75, 0)
        t_now = idx / fps
        cv2.putText(frame, f"t = {t_now:0.3f} s", (pad + int(10 * scale_f), pad + int(24 * scale_f)),
                    font, fs, WHITE, max(1, thick - 1), cv2.LINE_AA)
        line_y = pad + int(48 * scale_f)
        for side, label in (("L", "L"), ("R", "P")):
            on = contact[side]
            ct = ct_of_frame[side][idx]
            txt = f"{label}: {'KONTAKT' if on else 'let'}"
            if on and np.isfinite(ct):
                txt += f"  {ct * 1000:.0f} ms"
            cv2.putText(frame, txt, (pad + int(10 * scale_f), line_y),
                        font, fs * 0.85, RED if on else (200, 200, 200),
                        max(1, thick - 1), cv2.LINE_AA)
            line_y += int(22 * scale_f)

        # --- popisky udalostí (prekážky) ---
        if events:
            ey = pad + box_h + int(26 * scale_f)
            for ev in events:
                if ev["lo"] <= idx <= ev["hi"]:
                    _draw_label(frame, ev["label"], (pad, ey), fs * 1.1, YELLOW,
                                max(1, thick), bg=(20, 20, 20))
                    ey += int(28 * scale_f)

        # --- časová os krokov v spodnej časti ---
        bar_y = h - int(26 * scale_f)
        bar_h = int(10 * scale_f)
        cv2.rectangle(frame, (pad, bar_y), (w - pad, bar_y + bar_h), (60, 60, 60), -1)
        for c in contacts:
            x0 = int(pad + (w - 2 * pad) * (c["td_frame"] / max(n - 1, 1)))
            x1 = int(pad + (w - 2 * pad) * (c["to_frame"] / max(n - 1, 1)))
            col = ORANGE if c["side"] == "L" else (120, 200, 255)
            cv2.rectangle(frame, (x0, bar_y), (max(x1, x0 + 1), bar_y + bar_h), col, -1)
        for ev in (events or []):
            x0 = int(pad + (w - 2 * pad) * (ev["lo"] / max(n - 1, 1)))
            x1 = int(pad + (w - 2 * pad) * (ev["hi"] / max(n - 1, 1)))
            cv2.rectangle(frame, (x0, bar_y - int(5 * scale_f)), (max(x1, x0 + 1), bar_y - 1),
                          YELLOW, -1)
        cur_x = int(pad + (w - 2 * pad) * (idx / max(n - 1, 1)))
        cv2.line(frame, (cur_x, bar_y - int(4 * scale_f)),
                 (cur_x, bar_y + bar_h + int(4 * scale_f)), WHITE, max(1, thick), cv2.LINE_AA)

        writer.write(frame)
        idx += 1
        if progress_cb and idx % 20 == 0:
            progress_cb(0.80 + 0.18 * (idx / max(n, 1)), "Vykresľujem video")

    cap.release()
    writer.release()
    if idx == 0:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return None

    # Prekódovanie do H.264, aby sa video dalo prehrať priamo v prehliadači
    final = out_path
    if has_ffmpeg():
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_path,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                 "-crf", "23", "-movflags", "+faststart", final],
                check=True, timeout=600,
            )
            os.remove(tmp_path)
            return final
        except Exception:
            pass
    try:
        shutil.move(tmp_path, final)
    except Exception:
        return None
    return final


def _grab_frames(video_path: str, indices: List[int]) -> Dict[int, np.ndarray]:
    """Sekvenčne prečíta video a vráti požadované snímky (podľa indexu)."""
    want = sorted(set(int(i) for i in indices if i >= 0))
    out: Dict[int, np.ndarray] = {}
    if not want or cv2 is None:
        return out
    cap = open_capture(video_path)
    if not cap.isOpened():
        return out
    last = want[-1]
    idx = 0
    wanted = set(want)
    while idx <= last:
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        if idx in wanted:
            out[idx] = frame.copy()
        idx += 1
    cap.release()
    return out


def render_keyframes(video_path: str, sig: Dict[str, Any], contacts: List[Dict[str, Any]],
                     fps: float, out_dir: str, prefix: str,
                     extra_events: Optional[List[Dict[str, Any]]] = None,
                     max_contacts: int = 3) -> List[Dict[str, Any]]:
    """
    Uloží kľúčové snímky (dopad a odraz vybraných krokov + voliteľné ďalšie
    udalosti) s vykreslenou kostrou a uhlami.  Výrez okolo bežca, JPEG.
    Vracia zoznam {file, label, time_s, side, angles}.
    """
    if cv2 is None or not contacts:
        return []
    os.makedirs(out_dir, exist_ok=True)
    Q = sig["Q"]
    n = sig["n"]
    direction = float(sig.get("direction", 1.0))

    # Vyberú sa kroky s najvyššou dôverou kostry, z rôznych častí videa
    ranked = sorted(contacts, key=lambda c: -c.get("confidence", 0.0))
    chosen = sorted(ranked[:max_contacts], key=lambda c: c["td_time"])
    wanted: List[Dict[str, Any]] = []
    for c in chosen:
        idx_c = contacts.index(c) + 1
        wanted.append({"frame": int(round(c["td_frame"])), "side": c["side"],
                       "label": f"Dopad – krok {idx_c} ({'ľavá' if c['side'] == 'L' else 'pravá'})",
                       "event": "touchdown", "step_index": idx_c})
        wanted.append({"frame": int(round(c["to_frame"])), "side": c["side"],
                       "label": f"Odraz – krok {idx_c} ({'ľavá' if c['side'] == 'L' else 'pravá'})",
                       "event": "toeoff", "step_index": idx_c})
    for ev in (extra_events or []):
        wanted.append(dict(ev))

    frames = _grab_frames(video_path, [w["frame"] for w in wanted])
    in_contact, _ = _contact_masks(contacts, n)
    out: List[Dict[str, Any]] = []
    for k, w_ in enumerate(wanted):
        fi = min(max(0, w_["frame"]), n - 1)
        frame = frames.get(fi)
        if frame is None:
            continue
        frame = frame.copy()
        h, w = frame.shape[:2]
        pts = Q[fi]
        contact = {"L": bool(in_contact["L"][fi]), "R": bool(in_contact["R"][fi])}
        if w_.get("event") == "touchdown":
            contact[w_["side"]] = True
        scale_f = max(0.5, w / 1280.0)
        draw_skeleton(frame, pts, contact, scale_f, max(1, int(round(2 * scale_f))),
                      angles={}, direction=direction)
        # výrez okolo postavy
        ok = np.isfinite(pts[:, 0])
        if ok.sum() >= 5:
            xs, ys = pts[ok, 0], pts[ok, 1]
            bh = max(ys.max() - ys.min(), 40)
            cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
            half = 0.85 * bh
            x0, x1 = int(max(0, cx - half)), int(min(w, cx + half))
            y0, y1 = int(max(0, cy - half)), int(min(h, cy + half))
            if x1 - x0 > 40 and y1 - y0 > 40:
                frame = frame[y0:y1, x0:x1]
        # zjednotená veľkosť
        fh, fw = frame.shape[:2]
        if fh > 720:
            s = 720.0 / fh
            frame = cv2.resize(frame, None, fx=s, fy=s)
        sf = max(0.6, frame.shape[1] / 900.0)
        _draw_label(frame, w_["label"], (8, int(26 * sf) + 4), 0.62 * sf, WHITE, 1)
        _draw_label(frame, f"t = {fi / fps:.3f} s", (8, int(26 * sf) + 4 + int(26 * sf)),
                    0.55 * sf, (210, 210, 210), 1)
        angles = joint_angles_at(sig, float(fi), w_["side"]) if w_.get("side") else None
        fname = f"{prefix}_key{k + 1:02d}.jpg"
        cv2.imwrite(os.path.join(out_dir, fname), frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
        out.append({"file": fname, "label": w_["label"], "time_s": round(fi / fps, 3),
                    "frame": fi, "side": w_.get("side"), "event": w_.get("event"),
                    "step_index": w_.get("step_index"), "hurdle": w_.get("hurdle"),
                    "angles": angles})
    return out


# ---------------------------------------------------------------------------
# Hlavná funkcia
# ---------------------------------------------------------------------------

def analyze_video(
    path: str,
    athlete_height_cm: Optional[float] = None,
    real_fps: Optional[float] = None,
    overlay_path: Optional[str] = None,
    threshold_frac: float = DEFAULT_THRESHOLD_FRAC,
    with_flow: bool = True,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    roi_track: bool = True,
    init_point: Optional[tuple] = None,
    keyframes_dir: Optional[str] = None,
    keyframes_prefix: str = "video",
    phase: str = "auto",
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> Dict[str, Any]:
    """Kompletná analýza jedného videa. Vracia slovník pripravený na JSON."""

    def prog(frac: float, msg: str):
        if progress_cb:
            progress_cb(min(max(frac, 0.0), 1.0), msg)

    prog(0.02, "Načítavam video")
    info = probe_video(path)

    fps = float(real_fps) if real_fps else float(info.get("fps_ffprobe") or info["fps"] or 30.0)
    if fps <= 0:
        fps = 30.0

    lm = extract_landmarks(path, progress_cb=prog, with_flow=with_flow,
                           roi_track=roi_track, init_point=init_point, max_frames=max_frames)
    prog(0.72, "Počítam signály")
    sig = build_signals(lm["points"], lm["visibility"], fps)

    prog(0.76, "Hľadám kontakty so zemou")
    contacts = detect_contacts(sig, "L", fps, threshold_frac) + \
        detect_contacts(sig, "R", fps, threshold_frac)
    n_raw_contacts = len(contacts)
    contacts = dedupe_contacts(contacts)

    if not contacts:
        raise VideoAnalysisError(
            "Nenašiel som ani jeden kontakt so zemou. Najčastejšie príčiny: "
            "bežec nie je celý v zábere, video je príliš krátke, alebo je "
            "natočený spredu namiesto z boku."
        )

    # --- druhá metóda: optický tok (OpenCV) --------------------------------
    # Nezávisle od kostry sa zmeria, kedy je chodidlo nehybné voči zemi.
    flow_info: Dict[str, Any] = {"available": False}
    flow_by_side: Dict[str, List[List[int]]] = {}
    if lm.get("flow"):
        prog(0.78, "Porovnávam s optickým tokom")
        # posuny sú v pixeloch zmenšenej snímky, mierka tela tiež
        torso_small = sig["torso_ref"] * float(lm.get("work_scale") or 1.0)
        for side in ("L", "R"):
            disp = _fit_len(lm["flow"][side], sig["n"])
            speed_sig = disp * fps / max(torso_small, 1e-6)   # dĺžok trupu za sekundu
            flow_by_side[side] = flow_contacts(speed_sig, fps)
        cam_px = lm["flow"]["camera_px"]
        flow_info = {
            "available": True,
            "contacts_found": sum(len(v) for v in flow_by_side.values()),
            "camera_motion_px": round(float(np.nanmedian(cam_px)), 2)
            if np.isfinite(cam_px).any() else None,
        }

    def _flow_match(c) -> Optional[float]:
        """Dĺžka kontaktu podľa optického toku pre daný krok (v ms)."""
        ivs = flow_by_side.get(c["side"]) or []
        if not ivs:
            return None
        mid = (c["td_frame"] + c["to_frame"]) / 2
        best = min(ivs, key=lambda iv: abs((iv[0] + iv[1]) / 2 - mid))
        if abs((best[0] + best[1]) / 2 - mid) > 0.08 * fps:
            return None
        return (best[1] - best[0] + 1) / fps * 1000.0

    # --- kalibrácia, rýchlosť, dĺžka kroku ---------------------------------
    scale_detail = estimate_scale_detail(sig, athlete_height_cm)
    m_per_px = scale_detail["m_per_px"] if scale_detail else None
    speed = estimate_speed(sig, contacts, m_per_px, fps)          # zo stojnej nohy
    cam = camera_track(lm.get("flow"), float(lm.get("work_scale") or 1.0), sig["n"], fps)
    geom = estimate_step_geometry(sig, contacts, cam, fps, m_per_px)   # z miest dopadu

    # Hlavná rýchlosť: z geometrie krokov (robustnejšia), inak zo stojnej nohy
    speed_geom = geom.get("speed_ms")
    speed_stance = speed.get("speed_ms")
    if speed_geom:
        speed_ms = speed_geom
        speed_method = "vzdialenosť medzi dopadmi / čas (geometria krokov)"
    elif speed_stance:
        speed_ms = speed_stance
        speed_method = speed.get("method")
    else:
        speed_ms = None
        speed_method = speed.get("method")
    speed_agreement_pct = None
    if speed_geom and speed_stance:
        speed_agreement_pct = round(abs(speed_geom - speed_stance) / speed_geom * 100, 1)

    # --- uhly + kroky ------------------------------------------------------
    steps: List[Dict[str, Any]] = []
    for i, c in enumerate(contacts):
        nxt = contacts[i + 1] if i + 1 < len(contacts) else None
        flight = None
        step_time = None
        if nxt is not None:
            flight = nxt["td_time"] - c["to_time"]
            step_time = nxt["td_time"] - c["td_time"]
            if not (MIN_STEP_S <= (step_time or 0) <= MAX_STEP_S):
                step_time = None
            if flight is not None and (flight < -0.02 or flight > 0.35):
                flight = None

        a_td = joint_angles_at(sig, c["td_frame"], c["side"])
        a_to = joint_angles_at(sig, c["to_frame"], c["side"])

        # Najväčšie pokrčenie kolena počas opory (amortizácia)
        lo = int(math.floor(c["td_frame"]))
        hi = int(math.ceil(c["to_frame"]))
        knee_min = None
        if hi > lo:
            vals = [joint_angles_at(sig, t, c["side"])["knee_deg"]
                    for t in range(lo, min(hi + 1, sig["n"]))]
            vals = [v for v in vals if v is not None]
            knee_min = min(vals) if vals else None

        # Dĺžka kroku: 1. geometricky (vzdialenosť dopadov), 2. rýchlosť × čas
        step_len = None
        step_len_method = None
        g = geom["per_step"][i] if i < len(geom["per_step"]) else None
        if g and g.get("valid") and g.get("step_m") is not None:
            step_len = g["step_m"]
            step_len_method = "geometria"
        elif g and g.get("valid") and g.get("step_px") is not None and not m_per_px:
            step_len_method = "geometria (bez mierky)"
        elif step_time and speed_ms:
            step_len = speed_ms * step_time
            step_len_method = "rýchlosť × čas"

        steps.append({
            "index": i + 1,
            "side": "Ľavá" if c["side"] == "L" else "Pravá",
            "side_code": c["side"],
            "touchdown_s": round(c["td_time"], 4),
            "toeoff_s": round(c["to_time"], 4),
            "contact_ms": round(c["contact_s"] * 1000, 1),
            "flight_ms": round(flight * 1000, 1) if flight is not None else None,
            "step_time_ms": round(step_time * 1000, 1) if step_time else None,
            "step_length_m": round(step_len, 3) if step_len else None,
            "step_length_method": step_len_method,
            "step_length_px": g.get("step_px") if g else None,
            "step_length_note": (g.get("reason") if g and not g.get("valid") else None),
            "frames_in_contact": c["frames_in_contact"],
            "confidence": round(c["confidence"], 3),
            "cross_check_ms": round(max(x for x in [c["agree_td_ms"], c["agree_to_ms"]]
                                        if x is not None), 1)
            if (c["agree_td_ms"] is not None or c["agree_to_ms"] is not None) else None,
            "flow_contact_ms": (lambda v: round(v, 1) if v is not None else None)(_flow_match(c)),
            "angles_touchdown": a_td,
            "angles_toeoff": a_to,
            "knee_min_stance_deg": knee_min,
        })

    # --- súhrn -------------------------------------------------------------
    contact_ms = [s["contact_ms"] for s in steps]
    flight_ms = [s["flight_ms"] for s in steps]
    step_ms = [s["step_time_ms"] for s in steps]

    left = [s["contact_ms"] for s in steps if s["side_code"] == "L"]
    right = [s["contact_ms"] for s in steps if s["side_code"] == "R"]
    asym = None
    if left and right:
        ml, mr = float(np.mean(left)), float(np.mean(right))
        if (ml + mr) > 0:
            asym = round(abs(ml - mr) / ((ml + mr) / 2) * 100, 1)

    # Zhoda oboch metód – najpoctivejší ukazovateľ dôveryhodnosti merania
    flow_pairs = [(s["contact_ms"], s["flow_contact_ms"]) for s in steps
                  if s["flow_contact_ms"] is not None]
    flow_diff_ms = None
    flow_mean_ms = None
    if flow_pairs:
        flow_diff_ms = round(float(np.median([abs(a - b) for a, b in flow_pairs])), 1)
        flow_mean_ms = round(float(np.mean([b for _, b in flow_pairs])), 1)
    flow_info["steps_matched"] = len(flow_pairs)
    flow_info["median_diff_ms"] = flow_diff_ms
    flow_info["contact_ms_mean"] = flow_mean_ms

    st_stats = _nan_stats(step_ms)
    cadence = round(60000.0 / st_stats["mean"], 1) if st_stats["mean"] else None
    ct_stats = _nan_stats(contact_ms)
    fl_stats = _nan_stats(flight_ms)

    duty = None
    if ct_stats["mean"] and st_stats["mean"]:
        duty = round(ct_stats["mean"] / (2 * st_stats["mean"]) * 100, 1)

    # Vertikálna oscilácia panvy (po odčítaní pomalého pohybu kamery)
    vert_osc_cm = None
    if m_per_px:
        hip_y = sig["hip"][:, 1] - sig["hip_y_ref"]
        hip_y = hip_y[np.isfinite(hip_y)]
        if hip_y.size > 5:
            rng = float(np.percentile(hip_y, 95) - np.percentile(hip_y, 5))
            vert_osc_cm = round(rng * m_per_px * 100, 1)

    # Dĺžka kroku a dvojkroku
    geo_lens = [s["step_length_m"] for s in steps
                if s["step_length_m"] and s["step_length_method"] == "geometria"]
    all_lens = [s["step_length_m"] for s in steps if s["step_length_m"]]
    if geo_lens:
        step_len_mean = float(np.mean(geo_lens))
        step_len_method = "geometria"
    elif all_lens:
        step_len_mean = float(np.mean(all_lens))
        step_len_method = "rýchlosť × čas"
    else:
        step_len_mean = None
        step_len_method = None
    stride_len = round(2 * step_len_mean, 2) if step_len_mean else None

    # --- presnosť ----------------------------------------------------------
    frame_ms = 1000.0 / fps
    # Sub-frame interpolácia typicky zníži chybu na ~polovicu snímku
    uncertainty_ms = round(frame_ms * 0.5, 1)
    if fps >= 200:
        quality, quality_note = "výborná", "Vysoká snímková frekvencia – čas kontaktu je spoľahlivý."
    elif fps >= 100:
        quality, quality_note = "dobrá", "Dostatočná snímková frekvencia pre meranie kontaktu."
    elif fps >= 55:
        quality, quality_note = "hraničná", (
            "Pri 60 fps má kontakt len ~6–8 snímok. Výsledok berie ako orientačný – "
            "na presné meranie natoč video v spomalenom režime (120/240 fps).")
    else:
        quality, quality_note = "nízka", (
            f"Pri {fps:.0f} fps trvá jeden snímok {frame_ms:.0f} ms, "
            "kontakt pri šprinte má len 3–4 snímky. Čísla sú len hrubý odhad – "
            "natoč video v spomalenom režime (Slo-mo, 120 alebo 240 fps).")

    warnings: List[str] = []
    if fps < 100:
        warnings.append(quality_note)
    if lm["detected_ratio"] < 0.85:
        warnings.append(
            f"Kostra bola rozpoznaná len na {lm['detected_ratio'] * 100:.0f} % snímok – "
            "skús lepšie osvetlenie a kontrastnejšie pozadie.")
    if not athlete_height_cm:
        warnings.append(
            "Bez zadanej výšky atléta neviem prepočítať pixely na metre, "
            "takže chýba rýchlosť a dĺžka kroku.")
    if scale_detail and scale_detail.get("spread_pct") is not None and scale_detail["spread_pct"] > 25:
        warnings.append(
            f"Odhady mierky z rôznych častí tela sa líšia o {scale_detail['spread_pct']:.0f} % – "
            "kostra je nepresná (bežec malý alebo šikmý záber), rýchlosť a dĺžka kroku sú orientačné.")
    if athlete_height_cm and geom.get("n_steps", 0) > 0 and not geom.get("available"):
        reasons = [p.get("reason") for p in geom["per_step"] if p.get("reason")]
        top = max(set(reasons), key=reasons.count) if reasons else "neznámy dôvod"
        warnings.append(
            "Dĺžku kroku sa nepodarilo zmerať z polohy chodidiel "
            f"({top}); použitý je odhad rýchlosť × čas kroku, ktorý je menej spoľahlivý.")
    if speed_agreement_pct is not None and speed_agreement_pct > 25:
        warnings.append(
            "Dva nezávislé odhady rýchlosti sa líšia "
            f"(geometria krokov {speed_geom:.2f} m/s vs. stojná noha {speed_stance:.2f} m/s). "
            "Spoľahlivejšia je geometria; rozdiel býva pri nepresnej kostre chodidiel.")
    if step_len_mean and not (MIN_STEP_LEN_M <= step_len_mean <= MAX_STEP_LEN_M):
        warnings.append(
            f"Priemerná dĺžka kroku {step_len_mean:.2f} m je mimo rozumného rozsahu "
            f"({MIN_STEP_LEN_M:.1f}–{MAX_STEP_LEN_M:.1f} m). Skontroluj zadanú výšku, skutočné fps "
            "a či je bežec po celý čas celý v zábere.")
    cross = [s["cross_check_ms"] for s in steps if s["cross_check_ms"] is not None]
    if cross and float(np.median(cross)) > 2.5 * frame_ms:
        warnings.append(
            "Dve nezávislé metódy detekcie sa výrazne líšia "
            f"(medián {np.median(cross):.0f} ms) – kvalita videa je pravdepodobne slabá.")
    if flow_diff_ms is not None and flow_diff_ms > max(3 * frame_ms, 25):
        warnings.append(
            f"Dve nezávislé metódy (poloha kostry a optický tok) sa líšia v priemere "
            f"o {flow_diff_ms:.0f} ms. Číslam ver len rámcovo – pravdepodobne je záber "
            "rozmazaný, tmavý alebo je bežec príliš ďaleko.")
    if flow_info.get("available") and flow_info.get("steps_matched", 0) == 0:
        warnings.append(
            "Optický tok nenašiel ani jeden kontakt na overenie – zvyčajne pri "
            "málo kontrastnom zábere alebo rozmazaných chodidlách. "
            "Výsledok stojí len na jednej metóde.")
    if len(steps) < 4:
        warnings.append("Málo krokov v zábere – priemer je štatisticky slabý. "
                        "Natoč dlhší úsek behu (aspoň 6–8 krokov).")
    if n_raw_contacts - len(contacts) >= 2:
        warnings.append(
            f"{n_raw_contacts - len(contacts)} kontaktov sa prekrývalo s kontaktom druhej nohy "
            "(pri behu nemožné) – model si pravdepodobne mýlil ľavú a pravú nohu; "
            "duplicity boli vyradené. Pomôže ostrejší záber presne z boku.")

    summary = {
        "steps_detected": len(steps),
        "contact_ms_mean": round(ct_stats["mean"], 1) if ct_stats["mean"] else None,
        "contact_ms_sd": round(ct_stats["sd"], 1) if ct_stats["sd"] is not None else None,
        "contact_ms_min": ct_stats["min"], "contact_ms_max": ct_stats["max"],
        "flight_ms_mean": round(fl_stats["mean"], 1) if fl_stats["mean"] else None,
        "step_time_ms_mean": round(st_stats["mean"], 1) if st_stats["mean"] else None,
        "cadence_spm": cadence,
        "step_freq_hz": round(cadence / 60.0, 2) if cadence else None,
        "duty_factor_pct": duty,
        "asymmetry_pct": asym,
        "contact_left_ms": round(float(np.mean(left)), 1) if left else None,
        "contact_right_ms": round(float(np.mean(right)), 1) if right else None,
        "speed_ms": round(speed_ms, 2) if speed_ms else None,
        "speed_kmh": round(speed_ms * 3.6, 2) if speed_ms else None,
        "speed_method": speed_method,
        "speed_geometry_ms": round(speed_geom, 2) if speed_geom else None,
        "speed_stance_ms": round(speed_stance, 2) if speed_stance else None,
        "speed_agreement_pct": speed_agreement_pct,
        "step_length_m": round(step_len_mean, 2) if step_len_mean else None,
        "step_length_sd_m": round(geom["step_m_sd"], 2) if geom.get("step_m_sd") is not None else None,
        "step_length_method": step_len_method,
        "step_length_steps_measured": len(geo_lens),
        "stride_length_m": stride_len,
        "step_length_height_ratio": (round(step_len_mean / (athlete_height_cm / 100.0), 2)
                                     if step_len_mean and athlete_height_cm else None),
        "vertical_oscillation_cm": vert_osc_cm,
        "m_per_px": m_per_px,
        "flow_contact_ms_mean": flow_mean_ms,
        "flow_agreement_ms": flow_diff_ms,
    }

    # --- hodnotenie techniky (pravidlá) ------------------------------------
    technique = None
    try:
        from technique_rules import sprint_feedback
        technique = sprint_feedback(summary, steps, fps=fps, athlete_height_cm=athlete_height_cm,
                                    phase=phase)
    except Exception as e:      # hodnotenie nesmie zhodiť analýzu
        technique = {"error": f"Hodnotenie techniky zlyhalo: {e}", "findings": []}

    # --- kľúčové snímky ----------------------------------------------------
    keyframes: List[Dict[str, Any]] = []
    if keyframes_dir:
        prog(0.79, "Ukladám kľúčové snímky")
        try:
            keyframes = render_keyframes(path, sig, contacts, fps, keyframes_dir, keyframes_prefix)
        except Exception:
            keyframes = []

    # --- prekryvné video ---------------------------------------------------
    overlay_out = None
    if overlay_path:
        prog(0.80, "Vykresľujem video")
        try:
            overlay_out = render_overlay(path, overlay_path, sig, contacts, fps, prog)
        except Exception:
            overlay_out = None

    prog(0.99, "Hotovo")

    return {
        "video": {
            "fps": round(fps, 2),
            "fps_container": round(float(info.get("fps") or 0), 2),
            "frames": lm["n_frames"],
            "duration_s": round(lm["n_frames"] / fps, 2),
            "width": lm["width"], "height": lm["height"],
            "detected_ratio": round(lm["detected_ratio"], 3),
            "crop_ratio": round(float(lm.get("crop_ratio") or 0.0), 3),
        },
        "accuracy": {
            "frame_ms": round(frame_ms, 2),
            "uncertainty_ms": uncertainty_ms,
            "quality": quality,
            "note": quality_note,
        },
        "summary": summary,
        "scale": scale_detail,
        "geometry": {
            "available": geom.get("available"),
            "n_valid": geom.get("n_valid"),
            "n_steps": geom.get("n_steps"),
            "camera": geom.get("camera"),
            "per_step": geom.get("per_step"),
        },
        "optical_flow": flow_info,
        "steps": steps,
        "technique": technique,
        "keyframes": keyframes,
        "warnings": warnings,
        "overlay_path": overlay_out,
    }
