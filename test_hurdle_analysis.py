# -*- coding: utf-8 -*-
"""
test_hurdle_analysis.py
=======================
Overenie prekážkovej analýzy na SYNTETICKÝCH dátach so známou pravdou.

Zostrojí sa bežec, ktorý strieda bežné kroky (kontakt 110 ms, let 120 ms)
s prechodmi prekážok (odraz 120 ms, let 400 ms, dopad 140 ms) v rytme
13 krokov (400 m prekážky) alebo 3 krokov (110 m prekážky).  Kontroluje sa,
či algoritmus nájde všetky prekážky, správny počet krokov medzi nimi,
medzičasy a letové časy.

Spustenie:   python test_hurdle_analysis.py
"""

import math

import numpy as np

import video_analysis as va
import hurdle_analysis as ha


def build_events(steps_between, n_hurdles, contact_s=0.110, flight_s=0.120,
                 h_takeoff_s=0.120, h_flight_s=0.400, h_landing_s=0.140, lead_steps=4):
    """
    Zoznam kontaktov (td, to, side, role).  Rytmus: lead_steps bežných krokov,
    potom prekážka, potom `steps_between` krokov (posledný z nich je odraz).
    """
    events = []
    t = 0.30
    side = 0
    role_next = None

    def add(td, ct, role):
        nonlocal side
        events.append({"td": td, "to": td + ct, "side": "L" if side % 2 == 0 else "R", "role": role})
        side += 1
        return td + ct

    # rozbeh
    for _ in range(lead_steps):
        t = add(t, contact_s, "run") + flight_s
    for h in range(n_hurdles):
        # posledný krok pred prekážkou je odraz
        t = add(t, h_takeoff_s, "takeoff") + h_flight_s
        t = add(t, h_landing_s, "landing") + flight_s
        n_run = steps_between - 1 if h < n_hurdles - 1 else 3
        for _ in range(n_run):
            t = add(t, contact_s, "run") + flight_s
    return events


def synth_from_events(fps, events, speed_px_s=5600.0, panning=False, noise_px=0.0, seed=0):
    """Rovnaká figurína ako v test_video_analysis.synth_runner, ale s prekážkovými letmi."""
    rng = np.random.default_rng(seed)
    duration_s = events[-1]["to"] + 0.4
    n = int(duration_s * fps)
    t = np.arange(n) / fps

    H = 300.0
    hip_h = 520.0
    ground_y = 900.0

    hip_x_world = speed_px_s * t
    hip_y = ground_y - hip_h + 12.0 * np.sin(2 * np.pi * t / 0.23)
    # zdvih panvy počas prekážkového letu
    for i in range(len(events) - 1):
        if events[i]["role"] == "takeoff":
            a, b = events[i]["to"], events[i + 1]["td"]
            m = (t >= a) & (t <= b)
            f = (t[m] - a) / max(b - a, 1e-6)
            hip_y[m] -= 70.0 * np.sin(np.pi * f)
    cam_x = hip_x_world - 640.0 if panning else np.zeros(n)
    hip_x = hip_x_world - cam_x

    feet = {}
    for side in ("L", "R"):
        ev = [e for e in events if e["side"] == side]
        ankle_x = np.zeros(n)
        ankle_y = np.zeros(n)
        for i, ti in enumerate(t):
            cur = next((e for e in ev if e["td"] <= ti <= e["to"]), None)
            if cur:
                stance_x = speed_px_s * (cur["td"] + 0.45 * (cur["to"] - cur["td"]))
                ankle_x[i] = stance_x - cam_x[i]
                ankle_y[i] = ground_y - 40.0
            else:
                prev = max([e for e in ev if e["to"] < ti], key=lambda e: e["to"], default=None)
                nxt = min([e for e in ev if e["td"] > ti], key=lambda e: e["td"], default=None)
                if prev is None:
                    prev = {"td": ev[0]["td"] - 0.46, "to": ev[0]["td"] - 0.46 + 0.11, "role": "run"}
                if nxt is None:
                    nxt = {"td": ev[-1]["td"] + 0.46, "to": ev[-1]["td"] + 0.46 + 0.11, "role": "run"}
                x0 = speed_px_s * (prev["td"] + 0.45 * (prev["to"] - prev["td"]))
                x1 = speed_px_s * (nxt["td"] + 0.45 * (nxt["to"] - nxt["td"]))
                f = (ti - prev["to"]) / max(nxt["td"] - prev["to"], 1e-6)
                f = min(max(f, 0.0), 1.0)
                smooth = f * f * (3 - 2 * f)
                ankle_x[i] = x0 + (x1 - x0) * smooth - cam_x[i]
                # vyšší oblúk, keď je v tejto medzere prekážka
                over_hurdle = any(e["role"] == "takeoff" and prev["to"] - 0.5 <= e["to"] <= nxt["td"]
                                  for e in events)
                lift = (330.0 if over_hurdle else 190.0) * np.sin(np.pi * f)
                ankle_y[i] = ground_y - 40.0 - lift
        feet[side] = (ankle_x, ankle_y)

    P = np.full((n, 33, 2), np.nan)
    P[:, va.L_HIP] = np.stack([hip_x - 12, hip_y], axis=1)
    P[:, va.R_HIP] = np.stack([hip_x + 12, hip_y], axis=1)
    P[:, va.L_SHOULDER] = np.stack([hip_x - 14, hip_y - H], axis=1)
    P[:, va.R_SHOULDER] = np.stack([hip_x + 14, hip_y - H], axis=1)
    P[:, va.NOSE] = np.stack([hip_x + 20, hip_y - H - 40], axis=1)
    for side, (ax, ay) in feet.items():
        A = va.L_ANKLE if side == "L" else va.R_ANKLE
        K = va.L_KNEE if side == "L" else va.R_KNEE
        HE = va.L_HEEL if side == "L" else va.R_HEEL
        TO = va.L_TOE if side == "L" else va.R_TOE
        hx = P[:, va.L_HIP if side == "L" else va.R_HIP, 0]
        hy = P[:, va.L_HIP if side == "L" else va.R_HIP, 1]
        P[:, A] = np.stack([ax, ay], axis=1)
        P[:, K] = np.stack([(hx + ax) / 2 + 15, (hy + ay) / 2], axis=1)
        P[:, HE] = np.stack([ax - 22, ay + 26], axis=1)
        P[:, TO] = np.stack([ax + 60, ay + 34], axis=1)
    for j in (va.L_ELBOW, va.R_ELBOW, va.L_WRIST, va.R_WRIST):
        P[:, j] = np.stack([hip_x, hip_y - H / 2], axis=1)
    if noise_px > 0:
        P += rng.normal(0, noise_px, P.shape)
    V = np.ones((n, 33)) * 0.95
    return P, V, cam_x


def run_case(fps, steps_between, n_hurdles, label, noise=0.0, panning=False):
    events = build_events(steps_between, n_hurdles)
    P, V, _ = synth_from_events(fps, events, noise_px=noise, panning=panning)
    sig = va.build_signals(P, V, fps)
    contacts = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
    contacts = ha.dedupe_contacts(contacts)
    flights = ha.find_hurdle_flights(contacts, sig, fps)

    truth_land = [e["td"] for e in events if e["role"] == "landing"]
    found_land = [contacts[f["i_landing"]]["td_time"] for f in flights]
    matched = 0
    errs = []
    for tl in truth_land:
        if found_land:
            best = min(found_land, key=lambda x: abs(x - tl))
            if abs(best - tl) < 0.1:
                matched += 1
                errs.append((best - tl) * 1000)
    # počet krokov medzi prekážkami
    steps = []
    for k in range(len(flights) - 1):
        steps.append(flights[k + 1]["i_takeoff"] - flights[k]["i_landing"])
    fl_err = [(f["flight_s"] - 0.400) * 1000 for f in flights]
    print(f"  {label:<30} fps={fps:>4.0f}  prekážky {len(flights)}/{n_hurdles}  "
          f"spárované {matched}  kroky medzi {steps}  "
          f"chyba dopadu {np.mean(np.abs(errs)) if errs else float('nan'):5.1f} ms  "
          f"chyba letu {np.mean(fl_err) if fl_err else float('nan'):+5.1f} ms")
    return {"found": len(flights), "matched": matched, "steps": steps,
            "td_err": float(np.mean(np.abs(errs))) if errs else float("nan"),
            "fl_err": float(np.mean(np.abs(fl_err))) if fl_err else float("nan")}


def end_to_end():
    """Celý analyze_hurdle_video() so syntetickou kostrou namiesto MediaPipe."""
    import os
    import cv2

    fps = 120
    events = build_events(13, 3)
    P, V, cam_x = synth_from_events(fps, events)
    n = P.shape[0]
    w, h = 1280, 960
    tmp_in = "/tmp/_synth_hurdles_in.mp4"
    tmp_out = "/tmp/_synth_hurdles_overlay.mp4"
    tmp_keys = "/tmp/_synth_hurdle_keys"
    writer = cv2.VideoWriter(tmp_in, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _ in range(n):
        writer.write(np.full((h, w, 3), 30, dtype=np.uint8))
    writer.release()

    orig = va.extract_landmarks
    va.extract_landmarks = lambda path, progress_cb=None, **kw: {
        "points": P, "visibility": V, "width": w, "height": h,
        "n_frames": n, "detected_ratio": 1.0, "work_scale": 0.5, "crop_ratio": 0.0,
    }
    try:
        res = ha.analyze_hurdle_video(tmp_in, discipline="400mH", athlete_height_cm=182,
                                      real_fps=fps, overlay_path=tmp_out,
                                      keyframes_dir=tmp_keys, keyframes_prefix="t")
    finally:
        va.extract_landmarks = orig

    s = res["summary"]
    print(f"  prekážok: {s['hurdles_detected']}, rytmus {s['steps_pattern_text']}, "
          f"medzičas {s['interval_mean_s']} s, let {s['flight_ms_mean']} ms, "
          f"rýchlosť medzi prekážkami {s['speed_between_ms_mean']} m/s")
    print(f"  hodnotenie: {res['technique']['overall']}")

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    # pravda: medzičas = 13 krokov (12x0.23 + odraz 0.12 + let 0.40 + ... )
    truth_land = [e["td"] for e in events if e["role"] == "landing"]
    truth_iv = truth_land[1] - truth_land[0]
    check(s["hurdles_detected"] == 3, f"našli sa 3 prekážky (je {s['hurdles_detected']})")
    check(s["steps_pattern"] == [13, 13], f"13 krokov medzi prekážkami (je {s['steps_pattern']})")
    check(s["interval_mean_s"] is not None and abs(s["interval_mean_s"] - truth_iv) < 0.02,
          f"medzičas {truth_iv:.3f} s ± 20 ms (je {s['interval_mean_s']})")
    check(s["flight_ms_mean"] is not None and abs(s["flight_ms_mean"] - 400) < 15,
          f"let nad prekážkou 400 ± 15 ms (je {s['flight_ms_mean']})")
    check(s["speed_between_ms_mean"] is not None and abs(s["speed_between_ms_mean"] - 35 / truth_iv) < 0.1,
          f"rýchlosť medzi prekážkami = 35 m / medzičas (je {s['speed_between_ms_mean']})")
    check(all(h["takeoff_side"] != h["landing_side"] for h in res["hurdles"]),
          "odraz a dopad sú z rôznych nôh")
    check(len(res["intervals"]) == 2 and all(len(iv["step_times_ms"]) == 13 for iv in res["intervals"]),
          "rytmus medzi prekážkami má 13 časov krokov")
    check(res["technique"] and len(res["technique"]["findings"]) >= 3,
          f"hodnotenie techniky má aspoň 3 pravidlá ({len(res['technique']['findings'])})")
    check(os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 10000, "prekryvné video sa vytvorilo")
    check(len(res["keyframes"]) >= 6, f"kľúčové snímky sa uložili ({len(res['keyframes'])})")
    import json
    try:
        json.dumps(res)
        check(True, "výsledok sa dá serializovať do JSON")
    except (TypeError, ValueError) as e:
        check(False, f"JSON: {e}")

    for f in (tmp_in, tmp_out):
        try:
            os.remove(f)
        except OSError:
            pass
    return ok


def main():
    print("\n=== Detekcia prekážok (syntetické dáta) ===\n")
    res = {}
    for fps in (30, 60, 120, 240):
        res[fps] = run_case(fps, 13, 3, "400 m, 13 krokov")
    print()
    r110 = run_case(240, 3, 5, "110 m, 3 kroky")
    r110n = run_case(120, 3, 5, "110 m, 3 kroky, šum 2 px", noise=2.0)
    rpan = run_case(240, 13, 3, "400 m, panning", panning=True)

    print("\n=== Kontrola podmienok ===")
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    check(res[240]["found"] == 3 and res[240]["matched"] == 3, "240 fps: nájdené všetky 3 prekážky")
    check(res[240]["steps"] == [13, 13], f"240 fps: 13 krokov medzi prekážkami ({res[240]['steps']})")
    check(res[120]["steps"] == [13, 13], f"120 fps: 13 krokov medzi prekážkami ({res[120]['steps']})")
    check(res[30]["found"] == 3, "30 fps: prekážky sa nájdu aj pri nízkom fps")
    check(res[240]["td_err"] < 5, f"240 fps: dopad po prekážke presne na 5 ms ({res[240]['td_err']:.1f})")
    check(r110["found"] == 5 and r110["steps"] == [3, 3, 3, 3], f"110 m: 3 kroky ({r110['steps']})")
    check(r110n["found"] == 5 and r110n["steps"] == [3, 3, 3, 3], f"110 m so šumom: 3 kroky ({r110n['steps']})")
    check(rpan["found"] == 3 and rpan["steps"] == [13, 13], f"panning: rytmus sedí ({rpan['steps']})")

    print("\n=== Celý reťazec analyze_hurdle_video() ===")
    ok = end_to_end() and ok
    print("\n" + ("VŠETKY KONTROLY PREŠLI" if ok else "NIEKTORÉ KONTROLY ZLYHALI") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
