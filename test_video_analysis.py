# -*- coding: utf-8 -*-
"""
test_video_analysis.py
======================
Overenie detektora kontaktu so zemou na SYNTETICKÝCH dátach.

Vygeneruje sa pohyb kostry bežca so ZNÁMYM časom kontaktu a skontroluje sa,
či ho algoritmus nájde s prijateľnou chybou.  Test sa robí pri 30, 60, 120
a 240 fps – tým sa dá ukázať, prečo je spomalené video nutné.

Spustenie:   python test_video_analysis.py
"""

import numpy as np

import video_analysis as va


def synth_runner(fps, contact_s=0.110, step_s=0.230, duration_s=3.0,
                 speed_px_s=5600.0, panning=False, noise_px=0.0, seed=0):
    """
    Zostrojí pole bodov kostry (n, 33, 2) pre bežca so zadaným časom kontaktu.
    Vracia (P, V, ground_truth_kontakty).
    """
    rng = np.random.default_rng(seed)
    n = int(duration_s * fps)
    t = np.arange(n) / fps

    H = 300.0                      # výška trupu v pixeloch (mierka postavy)
    hip_h = 520.0                  # výška panvy nad spodkom obrazu
    ground_y = 900.0               # y-ová súradnica zeme v obraze
    stride_s = 2 * step_s

    # Panva: vodorovne konštantná rýchlosť, zvislo osciluje 2x za krok
    hip_x_world = speed_px_s * t
    hip_y = ground_y - hip_h + 12.0 * np.sin(2 * np.pi * t / step_s)

    cam_x = hip_x_world - 640.0 if panning else np.zeros(n)
    hip_x = hip_x_world - cam_x

    truth = []
    feet = {}
    for side, phase in (("L", 0.0), ("R", step_s)):
        ankle_x = np.zeros(n)
        ankle_y = np.zeros(n)
        # Cykly opory: začínajú v čase phase + k*stride_s
        k = 0
        events = []
        while phase + k * stride_s < duration_s + stride_s:
            td = phase + k * stride_s
            to = td + contact_s
            events.append((td, to))
            k += 1

        for i, ti in enumerate(t):
            # nájdeme, či sme v opore
            cur = None
            for td, to in events:
                if td <= ti <= to:
                    cur = (td, to)
                    break
            if cur:
                td, to = cur
                # chodidlo stojí na mieste (vo svete), telo ide okolo neho
                stance_x_world = speed_px_s * (td + 0.45 * contact_s)
                ankle_x[i] = stance_x_world - cam_x[i]
                ankle_y[i] = ground_y - 40.0        # členok je 40 px nad zemou
            else:
                # letová fáza: hľadáme predošlý odraz a nasledujúci dopad
                prev = max([e for e in events if e[1] < ti], key=lambda e: e[1], default=None)
                nxt = min([e for e in events if e[0] > ti], key=lambda e: e[0], default=None)
                if prev is None:
                    prev = (events[0][0] - stride_s, events[0][0] - stride_s + contact_s)
                if nxt is None:
                    nxt = (events[-1][0] + stride_s, events[-1][0] + stride_s + contact_s)
                x0 = speed_px_s * (prev[0] + 0.45 * contact_s)
                x1 = speed_px_s * (nxt[0] + 0.45 * contact_s)
                f = (ti - prev[1]) / max(nxt[0] - prev[1], 1e-6)
                f = min(max(f, 0.0), 1.0)
                smooth = f * f * (3 - 2 * f)                      # smoothstep
                ankle_x[i] = x0 + (x1 - x0) * smooth - cam_x[i]
                # Zdvih nohy – sínusový oblúk.  Sínus má na okrajoch nenulovú
                # deriváciu, takže noha dopadá aj odlepuje sa reálnou rýchlosťou
                # (~2.8 m/s), tak ako pri skutočnom šprinte.
                lift = 190.0 * np.sin(np.pi * f)
                ankle_y[i] = ground_y - 40.0 - lift

        feet[side] = (ankle_x, ankle_y)
        truth += [{"side": side, "td": td, "to": to, "contact": to - td}
                  for td, to in events if to < duration_s]

    # Zloženie 33 bodov
    P = np.full((n, 33, 2), np.nan)
    mid_hip_x, mid_hip_y = hip_x, hip_y
    P[:, va.L_HIP] = np.stack([mid_hip_x - 12, mid_hip_y], axis=1)
    P[:, va.R_HIP] = np.stack([mid_hip_x + 12, mid_hip_y], axis=1)
    P[:, va.L_SHOULDER] = np.stack([mid_hip_x - 14, mid_hip_y - H], axis=1)
    P[:, va.R_SHOULDER] = np.stack([mid_hip_x + 14, mid_hip_y - H], axis=1)
    P[:, va.NOSE] = np.stack([mid_hip_x + 20, mid_hip_y - H - 40], axis=1)
    for side, (ax, ay) in feet.items():
        A = va.L_ANKLE if side == "L" else va.R_ANKLE
        K = va.L_KNEE if side == "L" else va.R_KNEE
        HE = va.L_HEEL if side == "L" else va.R_HEEL
        TO = va.L_TOE if side == "L" else va.R_TOE
        hx = P[:, va.L_HIP if side == "L" else va.R_HIP, 0]
        hy = P[:, va.L_HIP if side == "L" else va.R_HIP, 1]
        P[:, A] = np.stack([ax, ay], axis=1)
        P[:, K] = np.stack([(hx + ax) / 2 + 15, (hy + ay) / 2], axis=1)
        P[:, HE] = np.stack([ax - 22, ay + 26], axis=1)     # päta vzadu a nižšie
        P[:, TO] = np.stack([ax + 60, ay + 34], axis=1)     # špička vpredu, najnižší bod
    # ostatné body (ruky) – nepoužívajú sa, doplníme hrubo
    for j in (va.L_ELBOW, va.R_ELBOW, va.L_WRIST, va.R_WRIST):
        P[:, j] = np.stack([mid_hip_x, mid_hip_y - H / 2], axis=1)

    if noise_px > 0:
        P += rng.normal(0, noise_px, P.shape)

    V = np.ones((n, 33)) * 0.95
    truth.sort(key=lambda d: d["td"])
    return P, V, truth


def run_case(fps, contact_s, panning=False, noise=0.0, label=""):
    P, V, truth = synth_runner(fps, contact_s=contact_s, panning=panning, noise_px=noise)
    sig = va.build_signals(P, V, fps)
    contacts = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
    contacts.sort(key=lambda c: c["td_time"])

    # Spárovanie s pravdou podľa najbližšieho dopadu tej istej nohy
    errs, td_errs = [], []
    for c in contacts:
        cands = [g for g in truth if g["side"] == c["side"]]
        if not cands:
            continue
        g = min(cands, key=lambda g: abs(g["td"] - c["td_time"]))
        if abs(g["td"] - c["td_time"]) > 0.10:
            continue
        errs.append((c["contact_s"] - g["contact"]) * 1000)
        td_errs.append((c["td_time"] - g["td"]) * 1000)

    n_truth = len([g for g in truth if g["to"] < 2.9])
    bias = float(np.mean(errs)) if errs else float("nan")
    rms = float(np.sqrt(np.mean(np.square(errs)))) if errs else float("nan")
    print(f"  {label:<34} fps={fps:>4.0f}  GT={contact_s*1000:5.0f} ms  "
          f"nájdených {len(contacts):>2}/{n_truth:<2}  "
          f"bias {bias:+6.1f} ms  RMS {rms:5.1f} ms  "
          f"(1 snímok = {1000/fps:.1f} ms)")
    return {"n": len(contacts), "n_truth": n_truth, "bias": bias, "rms": rms}


def end_to_end():
    """
    Overí celý reťazec analyze_video() vrátane výpočtu uhlov, rýchlosti
    a vykreslenia prekryvného videa.  MediaPipe sa nahradí syntetickou
    kostrou, aby test nepotreboval skutočné video s bežcom.
    """
    import os
    import cv2

    fps = 240
    P, V, truth = synth_runner(fps, contact_s=0.110)
    n = P.shape[0]
    w, h = 1280, 960

    tmp_in = "/tmp/_synth_in.mp4"
    tmp_out = "/tmp/_synth_overlay.mp4"
    writer = cv2.VideoWriter(tmp_in, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _ in range(n):
        writer.write(np.full((h, w, 3), 30, dtype=np.uint8))
    writer.release()

    orig = va.extract_landmarks
    va.extract_landmarks = lambda path, progress_cb=None, **kw: {
        "points": P, "visibility": V, "width": w, "height": h,
        "n_frames": n, "detected_ratio": 1.0,
    }
    try:
        res = va.analyze_video(tmp_in, athlete_height_cm=182, real_fps=fps,
                               overlay_path=tmp_out)
    finally:
        va.extract_landmarks = orig

    s = res["summary"]
    print(f"  krokov: {s['steps_detected']},  kontakt {s['contact_ms_mean']} ms,  "
          f"let {s['flight_ms_mean']} ms,  kadencia {s['cadence_spm']} spm")
    print(f"  rýchlosť {s['speed_ms']} m/s ({s['speed_kmh']} km/h),  "
          f"dĺžka kroku {res['steps'][0]['step_length_m']} m,  "
          f"duty factor {s['duty_factor_pct']} %")
    print(f"  uhol kolena pri dopade: {res['steps'][0]['angles_touchdown']['knee_deg']}°,  "
          f"náklon trupu: {res['steps'][0]['angles_touchdown']['trunk_lean_deg']}°")
    print(f"  upozornenia: {len(res['warnings'])}")

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    check(s["steps_detected"] >= 10, "našlo sa aspoň 10 krokov")
    check(s["contact_ms_mean"] is not None and abs(s["contact_ms_mean"] - 110) < 5,
          f"priemerný kontakt je 110 ± 5 ms (je {s['contact_ms_mean']})")
    check(s["cadence_spm"] is not None and abs(s["cadence_spm"] - 261) < 8,
          f"kadencia zodpovedá kroku 230 ms (je {s['cadence_spm']} spm)")
    # Rýchlosť sa overuje v pixeloch – prevod na metre závisí od proporcií
    # figuríny, ktoré sú v tomto zjednodušenom modeli len približné.
    # Kalibrácia mierky sa preto testuje zvlášť (scale_check nižšie).
    sig = va.build_signals(P, V, fps)
    cons = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
    px = va.estimate_speed(sig, cons, None, fps)["px_per_s"]
    check(abs(px - 5600.0) < 60,
          f"rýchlosť zo stojnej nohy sedí na 1 % (model 5600 px/s, vyšlo {px:.0f})")
    check(s["speed_ms"] is not None and s["speed_ms"] > 0,
          f"prevod na m/s prebehol ({s['speed_ms']} m/s)")
    check(s["stride_length_m"] is not None and s["stride_length_m"] > 0,
          f"dĺžka dvojkroku sa vypočítala ({s['stride_length_m']} m)")
    check(s["asymmetry_pct"] is not None and s["asymmetry_pct"] < 5,
          f"symetrický model dáva malú asymetriu ({s['asymmetry_pct']} %)")
    check(res["steps"][0]["angles_touchdown"]["knee_deg"] is not None,
          "uhly v kĺboch sa vypočítali")
    check(os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 10000,
          "prekryvné video sa vytvorilo")
    if os.path.exists(tmp_out):
        cap = cv2.VideoCapture(tmp_out)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        check(frames >= n - 2, f"prekryvné video má správny počet snímok ({frames}/{n})")
    check(res["summary"]["step_length_method"] == "geometria",
          f"dĺžka kroku je z geometrie dopadov ({res['summary']['step_length_m']} m)")
    check(res["technique"] and len(res["technique"]["findings"]) >= 8,
          f"hodnotenie techniky má aspoň 8 pravidiel ({len((res.get('technique') or {}).get('findings', []))})")
    check(json_safe(res), "výsledok sa dá serializovať do JSON")

    for f in (tmp_in, tmp_out):
        try:
            os.remove(f)
        except OSError:
            pass
    return ok


def scale_check():
    """
    Overí prevod pixel → meter na anatomicky správnej figuríne so ZNÁMOU
    dĺžkou nohy (bedro–členok), ktorá je počas celého záberu vystretá.
    """
    n = 60
    leg_px_true = 500.0
    P = np.full((n, 33, 2), np.nan)
    hip_y = 400.0
    for j, off in ((va.L_HIP, -12), (va.R_HIP, 12)):
        P[:, j] = np.array([[640 + off, hip_y]] * n)
    for j, off in ((va.L_SHOULDER, -14), (va.R_SHOULDER, 14)):
        P[:, j] = np.array([[640 + off, hip_y - 300]] * n)
    # Noha vystretá zvisle → vzdialenosť bedro–členok je presne leg_px_true
    for hip_j, knee_j, ankle_j in ((va.L_HIP, va.L_KNEE, va.L_ANKLE),
                                   (va.R_HIP, va.R_KNEE, va.R_ANKLE)):
        P[:, ankle_j] = np.array([[640, hip_y + leg_px_true]] * n)
        P[:, knee_j] = np.array([[640, hip_y + leg_px_true / 2]] * n)
    V = np.ones((n, 33))

    height_cm = 182.0
    sig = va.build_signals(P, V, 120)
    mpp = va.estimate_scale(sig, height_cm)
    expected = va.LEG_TO_HEIGHT * (height_cm / 100.0) / leg_px_true

    ok = mpp is not None and abs(mpp - expected) / expected < 0.01
    print(f"  m/px = {mpp:.6f}, očakávané {expected:.6f}  "
          f"(noha {leg_px_true:.0f} px = {va.LEG_TO_HEIGHT * height_cm:.1f} cm)")
    print(("  [OK]   " if ok else "  [CHYBA] ") +
          "kalibrácia pixel → meter je presná na 1 %")
    return ok


def step_length_check():
    """
    Overí geometrickú dĺžku kroku a rýchlosť na syntetickom bežcovi so ZNÁMOU
    rýchlosťou (5600 px/s) a časom kroku (0.230 s): pravá dĺžka kroku je
    1288 px.  Testuje sa statická kamera, pohyblivá kamera (posun pozadia zo
    syntetického optického toku) aj šum kostry.
    """
    fps = 240
    truth_px = 5600.0 * 0.230
    height_cm = 182.0
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    for label, panning, noise, flow in (("statická kamera", False, 0.0, True),
                                        ("statická kamera, bez optického toku", False, 0.0, False),
                                        ("panning (kamera ide s bežcom)", True, 0.0, True),
                                        ("panning + šum 2 px", True, 2.0, True)):
        P, V, truth = synth_runner(fps, contact_s=0.110, panning=panning, noise_px=noise)
        n = P.shape[0]
        sig = va.build_signals(P, V, fps)
        contacts = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
        contacts.sort(key=lambda c: c["td_time"])
        m_per_px = va.estimate_scale(sig, height_cm)
        lm_flow = None
        if flow:
            # syntetický optický tok: pozadie sa posúva o -posun kamery
            t = np.arange(n) / fps
            cam_x = 5600.0 * t - 640.0 if panning else np.zeros(n)
            dx = -np.diff(cam_x, prepend=cam_x[0])
            lm_flow = {"cam_dx": dx, "cam_dy": np.zeros(n)}
        cam = va.camera_track(lm_flow, 1.0, n, fps)
        geom = va.estimate_step_geometry(sig, contacts, cam, fps, m_per_px)
        speed_st = va.estimate_speed(sig, contacts, m_per_px, fps)
        err = (geom["step_px_mean"] - truth_px) / truth_px * 100 if geom["step_px_mean"] else float("nan")
        print(f"  {label:<38} krokov {geom['n_valid']:>2}/{geom['n_steps']:<2}  "
              f"dĺžka {geom['step_px_mean'] or 0:7.1f} px (pravda {truth_px:.0f})  chyba {err:+5.1f} %  "
              f"rýchlosť geometria {geom['speed_ms'] or 0:.2f} / stojná noha {speed_st['speed_ms'] or 0:.2f} m/s  "
              f"[{cam['note']}]")
        if panning and not flow:
            continue
        check(geom["n_valid"] >= geom["n_steps"] - 1, f"{label}: takmer všetky kroky uznané")
        check(abs(err) <= 2.0 + noise, f"{label}: dĺžka kroku na {2.0 + noise:.0f} % (je {err:+.1f} %)")
        if geom["speed_ms"] and speed_st["speed_ms"]:
            diff = abs(geom["speed_ms"] - speed_st["speed_ms"]) / geom["speed_ms"] * 100
            check(diff < 5, f"{label}: rýchlosť z geometrie a zo stojnej nohy sa zhodujú ({diff:.1f} %)")

    # Panning BEZ kompenzácie kamery musí byť odhalený (chodidlo sa vo svete hýbe)
    P, V, truth = synth_runner(fps, contact_s=0.110, panning=True)
    n = P.shape[0]
    sig = va.build_signals(P, V, fps)
    contacts = sorted(va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps),
                      key=lambda c: c["td_time"])
    geom = va.estimate_step_geometry(sig, contacts, va.camera_track(None, 1.0, n, fps), fps,
                                     va.estimate_scale(sig, height_cm))
    check(geom["n_valid"] == 0,
          f"panning bez optického toku: kroky sa správne NEuznajú ({geom['n_valid']} uznaných)")
    return ok


def rotation_check():
    """
    Video otočené na výšku (metadáta rotate=90): rozmery sa musia brať zo
    skutočnej snímky, nie z hlavičky.  MediaPipe sa nahradí atrapou.
    """
    import os
    import shutil
    import subprocess
    import cv2

    if not shutil.which("ffmpeg"):
        print("  (ffmpeg nie je k dispozícii – test rotácie sa preskočí)")
        return True
    src_path = "/tmp/_rot_src.mp4"
    rot_path = "/tmp/_rot_90.mp4"
    w, h, n = 640, 360, 24
    writer = cv2.VideoWriter(src_path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(n):
        writer.write(np.full((h, w, 3), 40, dtype=np.uint8))
    writer.release()
    # Rotácia ako v telefóne (display matrix v metadátach)
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", "90",
                        "-i", src_path, "-c", "copy", rot_path])
    if r.returncode != 0:      # staršie ffmpeg bez -display_rotation
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src_path, "-c", "copy",
                        "-metadata:s:v:0", "rotate=90", rot_path], check=True)

    class FakePose:
        def __init__(self, *a, **k):
            pass

        def process(self, rgb, ts):
            hh, ww = rgb.shape[:2]
            # jednoduchá postava v strede obrazu (normalizované súradnice)
            pts = [(0.5, 0.5, 0.9)] * 33
            pts[va.L_HIP] = (0.48, 0.5, 0.9); pts[va.R_HIP] = (0.52, 0.5, 0.9)
            pts[va.L_ANKLE] = (0.48, 0.8, 0.9); pts[va.R_ANKLE] = (0.52, 0.8, 0.9)
            return pts

        def close(self):
            pass

    orig = va._PoseRunner
    va._PoseRunner = FakePose
    try:
        out = va.extract_landmarks(rot_path, with_flow=False, roi_track=False)
    finally:
        va._PoseRunner = orig
    cap = va.open_capture(rot_path)
    meta = cap.get(getattr(cv2, "CAP_PROP_ORIENTATION_META", -1)) if hasattr(cv2, "CAP_PROP_ORIENTATION_META") else 0
    ret, frame = cap.read()
    cap.release()
    fh, fw = frame.shape[:2]
    print(f"  hlavička videa {w}×{h}, značka otočenia {meta:.0f}°, snímka po otočení {fw}×{fh}, "
          f"modul hlási {out['width']}×{out['height']}")
    ok = (out["width"], out["height"]) == (fw, fh)
    print(("  [OK]   " if ok else "  [CHYBA] ") + "rozmery sa berú zo skutočnej snímky")
    if meta and int(meta) % 180 == 90:
        ok2 = out["height"] > out["width"]
        print(("  [OK]   " if ok2 else "  [CHYBA] ") + "video na výšku sa otočí správne (bežec stojí)")
        ok = ok and ok2
    else:
        print("  (ffmpeg nezapísal značku otočenia – kontrola otočenia sa preskočí)")
    for f in (src_path, rot_path):
        try:
            os.remove(f)
        except OSError:
            pass
    return ok


def json_safe(obj) -> bool:
    import json
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError) as e:
        print("    JSON chyba:", e)
        return False


def main():
    print("\n=== Presnosť detekcie kontaktu so zemou (syntetické dáta) ===\n")
    print("Statická kamera, čistý signál:")
    res = {}
    for fps in (30, 60, 120, 240):
        res[fps] = run_case(fps, 0.110, label="šprint, kontakt 110 ms")

    print("\nPohyblivá kamera (panning za bežcom):")
    for fps in (120, 240):
        run_case(fps, 0.110, panning=True, label="panning")

    print("\nŠum v detekcii kostry (±2 px):")
    for fps in (120, 240):
        run_case(fps, 0.110, noise=2.0, label="šum 2 px")

    print("\nIné časy kontaktu pri 240 fps:")
    for ct in (0.085, 0.130, 0.180):
        run_case(240, ct, label=f"kontakt {ct*1000:.0f} ms")

    print("\n=== Kontrola podmienok ===")
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    check(res[240]["n"] >= res[240]["n_truth"] - 1,
          f"pri 240 fps sa nájde takmer každý krok ({res[240]['n']}/{res[240]['n_truth']})")
    check(abs(res[240]["bias"]) <= 6.0,
          f"pri 240 fps je systematická chyba do 6 ms (je {res[240]['bias']:+.1f} ms)")
    check(res[240]["rms"] <= 8.0,
          f"pri 240 fps je RMS chyba do 8 ms (je {res[240]['rms']:.1f} ms)")
    check(res[120]["rms"] <= 14.0,
          f"pri 120 fps je RMS chyba do 14 ms (je {res[120]['rms']:.1f} ms)")
    check(res[30]["rms"] > res[240]["rms"],
          "30 fps je preukázateľne horšie ako 240 fps – preto sa vyžaduje spomalené video")

    print("\n=== Kalibrácia mierky (pixel → meter) ===")
    ok = scale_check() and ok

    print("\n=== Dĺžka kroku a rýchlosť (geometria dopadov) ===")
    ok = step_length_check() and ok

    print("\n=== Otočené video (telefón na výšku) ===")
    ok = rotation_check() and ok

    print("\n=== Celý reťazec analyze_video() ===")
    ok = end_to_end() and ok

    print("\n" + ("VŠETKY KONTROLY PREŠLI" if ok else "NIEKTORÉ KONTROLY ZLYHALI") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
