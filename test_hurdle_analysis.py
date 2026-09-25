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

Okrem čistých klipov sa skúša aj to, čo sa na skutočnom videu stáva: vypadnutý
kontakt uprostred úseku (nesmie vzniknúť falošná prekážka, úsek sa označí ako
neúplný) a video, kde kostra takmer nie je (analýza ho musí odmietnuť).

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


def missed_contact_case(fps=120, drop_step=9):
    """
    Z čistého 13-krokového úseku sa vymaže jeden bežný kontakt.  Vznikne let
    ~350 ms, ktorý je dlhší než prah – bez ďalších kontrol by z neho bola
    „prekážka“ a rytmus 13 by sa rozpadol na 8-3.  Kontroly (rovnaká noha,
    žiadny zdvih panvy) ho musia zamietnuť.
    """
    events = build_events(13, 3)
    P, V, _ = synth_from_events(fps, events)
    sig = va.build_signals(P, V, fps)
    contacts = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
    contacts = ha.dedupe_contacts(contacts)
    first_land = next(e["td"] for e in events if e["role"] == "landing")
    runs_after = [e for e in events if e["role"] == "run" and e["td"] > first_land]
    t_drop = runs_after[drop_step - 1]["td"]
    contacts = [c for c in contacts if abs(c["td_time"] - t_drop) > 0.05]

    rejected = []
    flights = ha.find_hurdle_flights(contacts, sig, fps, min_sep_s=3.2, rejected=rejected)
    steps = [flights[k + 1]["i_takeoff"] - flights[k]["i_landing"] for k in range(len(flights) - 1)]
    print(f"  vymazaný kontakt v t={t_drop:.2f} s: prekážok {len(flights)}, surové kroky {steps}, "
          f"zamietnuté lety: "
          + "; ".join(f"t={r['t_s']} s ({', '.join(r['reasons'])})" for r in rejected))
    return {"found": len(flights), "steps": steps, "rejected": rejected, "t_drop": t_drop}


def _stub_landmarks(P, V, w, h, ratio=1.0):
    n = P.shape[0]
    return lambda path, progress_cb=None, **kw: {
        "points": P, "visibility": V, "width": w, "height": h,
        "n_frames": n, "detected_ratio": ratio, "work_scale": 0.5, "crop_ratio": 0.0,
    }


def _blank_video(path, n, fps, w=640, h=480):
    import cv2
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _ in range(n):
        writer.write(np.full((h, w, 3), 30, dtype=np.uint8))
    writer.release()


def end_to_end_missed_contact():
    """
    Celý reťazec s vypadnutým kontaktom v prvom úseku: prekážky ostanú tri,
    prvý úsek sa označí ako neúplný („?“), druhý má 13, hodnotenie ostane.
    """
    import os
    fps = 120
    events = build_events(13, 3)
    P, V, _ = synth_from_events(fps, events)
    n = P.shape[0]
    tmp_in = "/tmp/_synth_hurdles_missed.mp4"
    _blank_video(tmp_in, n, fps)
    first_land = next(e["td"] for e in events if e["role"] == "landing")
    t_drop = [e for e in events if e["role"] == "run" and e["td"] > first_land][8]["td"]

    orig_lm, orig_dd = va.extract_landmarks, ha.dedupe_contacts
    va.extract_landmarks = _stub_landmarks(P, V, 640, 480)
    ha.dedupe_contacts = lambda c: [x for x in orig_dd(c) if abs(x["td_time"] - t_drop) > 0.05]
    try:
        res = ha.analyze_hurdle_video(tmp_in, discipline="400mH", real_fps=fps)
    finally:
        va.extract_landmarks, ha.dedupe_contacts = orig_lm, orig_dd
        try:
            os.remove(tmp_in)
        except OSError:
            pass
    s = res["summary"]
    print(f"  prekážok: {s['hurdles_detected']}, rytmus {s['steps_pattern_text']}, "
          f"úplných úsekov {s['intervals_steps_valid']}/{s['intervals_total']}, "
          f"zamietnutých kandidátov {s['rejected_candidates']}, spoľahlivosť {res['reliability']}")
    for iv in res["intervals"]:
        print(f"    P{iv['from_hurdle']}→P{iv['to_hurdle']}: {iv['interval_s']} s, kroky "
              f"{iv['steps_between']} (surové {iv['steps_between_raw']}), {iv['invalid_reason'] or 'ok'}")
    return res


def refuse_low_detection():
    """
    Kostra chýba na 60 % snímok uprostred úseku, kde bežec je (40 % nájdených)
    – analýza musí video odmietnuť s vysvetlením.  Prázdny rozbeh a dobeh sa
    nepočítajú, preto sa diera vkladá dovnútra, nie na okraje.
    """
    import os
    fps = 60
    events = build_events(13, 2)
    P, V, _ = synth_from_events(fps, events)
    n = P.shape[0]
    P[int(0.2 * n):int(0.8 * n)] = np.nan
    tmp_in = "/tmp/_synth_hurdles_lowratio.mp4"
    _blank_video(tmp_in, n, fps)
    orig = va.extract_landmarks
    va.extract_landmarks = _stub_landmarks(P, V, 640, 480, ratio=0.40)
    msg = None
    try:
        ha.analyze_hurdle_video(tmp_in, discipline="400mH", real_fps=fps)
    except va.VideoAnalysisError as e:
        msg = str(e)
    finally:
        va.extract_landmarks = orig
        try:
            os.remove(tmp_in)
        except OSError:
            pass
    print(f"  správa: {msg}")
    return msg


def _run_full(events, fps, discipline, drop_times=(), flip_side_at=None):
    """analyze_hurdle_video() na syntetickej kostre; vymaže kontakty v drop_times (s)."""
    import os
    P, V, _ = synth_from_events(fps, events)
    tmp_in = "/tmp/_synth_hurdles_case.mp4"
    _blank_video(tmp_in, P.shape[0], fps)
    orig_lm, orig_dd = va.extract_landmarks, ha.dedupe_contacts

    def dedupe(c):
        out = [x for x in orig_dd(c) if all(abs(x["td_time"] - t) > 0.05 for t in drop_times)]
        if flip_side_at is not None:
            for x in out:
                if abs(x["td_time"] - flip_side_at) <= 0.05:
                    x["side"] = "L" if x["side"] == "R" else "R"
        return out

    va.extract_landmarks = _stub_landmarks(P, V, 640, 480)
    ha.dedupe_contacts = dedupe
    try:
        return ha.analyze_hurdle_video(tmp_in, discipline=discipline, real_fps=fps)
    finally:
        va.extract_landmarks, ha.dedupe_contacts = orig_lm, orig_dd
        try:
            os.remove(tmp_in)
        except OSError:
            pass


def sprint_missed_contact():
    """110 m: vypadnutý kontakt v úseku s 3 krokmi – nesmie vyjsť „3-2-3-3“ ako platné."""
    events = build_events(3, 5)
    land2 = [e["td"] for e in events if e["role"] == "landing"][1]
    t_drop = next(e["td"] for e in events if e["role"] == "run" and e["td"] > land2)
    res = _run_full(events, 240, "110mH", drop_times=(t_drop,))
    s = res["summary"]
    print(f"  110 m bez jedného kontaktu: rytmus {s['steps_pattern_text']}, spoľahlivosť {res['reliability']['level']}, "
          f"hodnotenie: {res['technique']['overall']}")
    return res


def swallowed_hurdle():
    """
    400 m: dopad za 2. prekážkou sa nenašiel (alebo MediaPipe zamenil nohy).
    Prekážka sa právom zamietne, ale zlúčený 7-sekundový úsek nesmie prejsť
    ako platný medzičas s rýchlosťou.
    """
    events = build_events(13, 3)
    land2 = [e["td"] for e in events if e["role"] == "landing"][1]
    out = {}
    for label, kw in (("vypadnutý dopad", {"drop_times": (land2,)}), ("zámena nôh", {"flip_side_at": land2})):
        res = _run_full(events, 120, "400mH", **kw)
        iv = res["intervals"][0] if res["intervals"] else None
        print(f"  {label}: prekážok {res['summary']['hurdles_detected']}, úsek "
              f"{iv['interval_s'] if iv else None} s valid={iv['valid'] if iv else None} "
              f"({iv['invalid_reason'] if iv else ''}), medzičas v súhrne {res['summary']['interval_mean_s']}, "
              f"spoľahlivosť {res['reliability']['level']}")
        out[label] = res
    return out


def wrong_discipline():
    """Video s 3-krokovým rytmom analyzované ako 400 m – analýza to musí povedať."""
    events = build_events(3, 8)
    msg = None
    try:
        _run_full(events, 120, "400mH")
    except va.VideoAnalysisError as e:
        msg = str(e)
    print(f"  správa: {msg}")
    return msg


def one_stride_drill():
    """Tréning „iné“: 1-krokový rytmus cez nízke prekážky – odstup dopadov ~0,6 s musí prejsť."""
    fps = 120
    events = build_events(1, 6)
    P, V, _ = synth_from_events(fps, events)
    sig = va.build_signals(P, V, fps)
    contacts = ha.dedupe_contacts(va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps))
    rejected = []
    flights = ha.find_hurdle_flights(contacts, sig, fps, min_sep_s=ha.MIN_HURDLE_SEP_OTHER_S, rejected=rejected)
    steps = [flights[k + 1]["i_takeoff"] - flights[k]["i_landing"] for k in range(len(flights) - 1)]
    print(f"  1-krokový dril: prekážky {len(flights)}/6, kroky {steps}, zamietnuté {len(rejected)}")
    return {"found": len(flights), "steps": steps}


def gate_cases():
    """Brány dôvery kostry, nereálneho zdvihu a minimálneho odstupu (obe vetvy)."""
    import copy
    fps = 120
    events = build_events(13, 3)
    P, V, _ = synth_from_events(fps, events)
    sig = va.build_signals(P, V, fps)
    contacts = ha.dedupe_contacts(va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps))
    base = ha.find_hurdle_flights(contacts, sig, fps)
    rises = [f["hip_rise_torso"] for f in base]
    out = {"base": len(base), "rises": rises}

    # a) nízka dôvera kostry pri dopade 2. prekážky
    c2 = copy.deepcopy(contacts)
    c2[base[1]["i_landing"]]["confidence"] = 0.1
    rej = []
    fl = ha.find_hurdle_flights(c2, sig, fps, rejected=rej)
    out["conf"] = (len(fl), [r for rj in rej for r in rj["reasons"]])

    # b) nereálny zdvih panvy – kostra „preskočila“ počas letu 2. prekážky
    sig2 = dict(sig)
    sig2["hip"] = sig["hip"].copy()
    lo = int(contacts[base[1]["i_takeoff"]]["to_frame"]) + 1
    hi = int(contacts[base[1]["i_landing"]]["td_frame"])
    sig2["hip"][lo:hi, 1] -= 1.5 * sig["torso_ref"]
    rej = []
    fl = ha.find_hurdle_flights(contacts, sig2, fps, rejected=rej)
    out["rise"] = (len(fl), [r for rj in rej for r in rj["reasons"]])

    # c) minimálny odstup: 3-krokový rytmus s prahom 3,2 s – ostane vždy len jedna z blízkych dvojíc
    ev3 = build_events(3, 3)
    P3, V3, _ = synth_from_events(fps, ev3)
    sig3 = va.build_signals(P3, V3, fps)
    c3 = ha.dedupe_contacts(va.detect_contacts(sig3, "L", fps) + va.detect_contacts(sig3, "R", fps))
    all3 = ha.find_hurdle_flights(c3, sig3, fps)
    rej = []
    fl = ha.find_hurdle_flights(c3, sig3, fps, min_sep_s=3.2, rejected=rej)
    out["sep_prev"] = (len(fl), [r for rj in rej for r in rj["reasons"]])
    # d) keď je prvá prekážka málo dôveryhodná, vyhrá nasledujúca (vetva výmeny)
    c3b = copy.deepcopy(c3)
    c3b[all3[0]["i_takeoff"]]["confidence"] = 0.5
    rej = []
    fl = ha.find_hurdle_flights(c3b, sig3, fps, min_sep_s=3.2, rejected=rej)
    out["sep_next"] = (len(fl), fl[0]["i_takeoff"] if fl else None, all3[-1]["i_takeoff"],
                       [r for rj in rej for r in rj["reasons"]])
    print(f"  zdvih panvy skutočných prekážok: {[round(r, 3) for r in rises]}")
    print(f"  dôvera: prekážok {out['conf'][0]}, dôvody {out['conf'][1]}")
    print(f"  nereálny zdvih: prekážok {out['rise'][0]}, dôvody {out['rise'][1]}")
    print(f"  odstup (predch.): prekážok {out['sep_prev'][0]} z {len(all3)}, dôvody {out['sep_prev'][1][:1]}")
    print(f"  odstup (nasl.): prekážok {out['sep_next'][0]}, ostala {out['sep_next'][1]} (posledná {out['sep_next'][2]}), dôvody {out['sep_next'][3][:1]}")
    return out


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

    print("\n=== Vypadnutý kontakt uprostred úseku ===")
    mc = missed_contact_case()
    check(mc["found"] == 3, f"falošná prekážka z chýbajúceho kontaktu sa zamietla (prekážok {mc['found']})")
    check(mc["steps"] == [12, 13], f"surové kroky 12-13, nie 8-3 ({mc['steps']})")
    check(any("tej istej nohy" in r for rj in mc["rejected"] for r in rj["reasons"]),
          "dôvod zamietnutia: odraz a dopad z tej istej nohy")
    res_mc = end_to_end_missed_contact()
    ivs = res_mc["intervals"]
    check(res_mc["summary"]["hurdles_detected"] == 3, "celý reťazec: stále 3 prekážky")
    check(len(ivs) == 2 and ivs[0]["steps_valid"] is False and ivs[0]["valid"] is True,
          "prvý úsek: medzičas platný, počet krokov neistý")
    check(len(ivs) == 2 and "chýba kontakt" in (ivs[0]["invalid_reason"] or ""),
          "prvý úsek má dôvod „chýba kontakt“")
    check(len(ivs) == 2 and ivs[1]["steps_between"] == 13 and ivs[1]["steps_valid"],
          "druhý úsek: 13 krokov, platný")
    check(res_mc["summary"]["steps_pattern_text"] == "?-13",
          f"rytmus sa ukáže ako ?-13 (je {res_mc['summary']['steps_pattern_text']})")
    check(res_mc["summary"]["interval_mean_s"] is not None
          and abs(res_mc["summary"]["interval_mean_s"] - 3.54) < 0.03,
          "priemerný medzičas sa počíta z oboch úsekov (chýbajúci kontakt ho nemení)")
    check(res_mc["reliability"]["level"] == "low", f"spoľahlivosť „low“ (je {res_mc['reliability']['level']})")
    check(any("Zamietol som" in w for w in res_mc["warnings"]), "upozornenie na zamietnutý let")
    check(len(res_mc["technique"]["findings"]) >= 2, "hodnotenie beží ďalej z platných úsekov")

    print("\n=== 110 m: vypadnutý kontakt v 3-krokovom úseku ===")
    r110m = sprint_missed_contact()
    check(r110m["summary"]["steps_pattern_text"] == "3-?-3-3",
          f"rytmus 3-?-3-3, nie 3-2-3-3 (je {r110m['summary']['steps_pattern_text']})")
    check(r110m["intervals"][1]["steps_valid"] is False and r110m["reliability"]["level"] == "low",
          "úsek s chýbajúcim kontaktom je neúplný a spoľahlivosť „low“")
    check(not any(f["status"] == "problem" for f in r110m["technique"]["findings"]),
          "hodnotenie nehlási falošný problém s rytmom")

    print("\n=== 400 m: prekážka bez dopadu / so zamenenými nohami ===")
    sw = swallowed_hurdle()
    for label, res in sw.items():
        iv = res["intervals"][0] if res["intervals"] else None
        check(res["summary"]["hurdles_detected"] == 2, f"{label}: prekážka sa zamietla (ostali 2)")
        check(iv is not None and iv["valid"] is False and "spája dva úseky" in (iv["invalid_reason"] or ""),
              f"{label}: zlúčený úsek je neplatný s dôvodom „spája dva úseky“")
        check(res["summary"]["interval_mean_s"] is None and res["summary"]["speed_between_ms_mean"] is None,
              f"{label}: zlúčený medzičas nejde do priemeru ani rýchlosti")
        check(res["reliability"]["level"] == "unusable", f"{label}: spoľahlivosť „unusable“")

    print("\n=== Zle zvolená disciplína ===")
    wd = wrong_discipline()
    check(wd is not None and "disciplína" in wd, "3-krokový rytmus ako 400 m sa odmietne s vysvetlením")

    print("\n=== Tréning: 1-krokový rytmus ===")
    od = one_stride_drill()
    check(od["found"] == 6 and od["steps"] == [1, 1, 1, 1, 1], f"6 prekážok, kroky {od['steps']}")

    print("\n=== Brány kandidátov ===")
    g = gate_cases()
    check(g["base"] == 3 and min(g["rises"]) >= 1.5 * ha.MIN_HIP_RISE_TORSO,
          f"zdvih skutočných prekážok má rezervu nad prahom {ha.MIN_HIP_RISE_TORSO} ({[round(r, 2) for r in g['rises']]})")
    check(any("tej istej nohy" in r for rj in mc["rejected"] for r in rj["reasons"])
          and all((rj["hip_rise_torso"] or 0) < ha.MIN_HIP_RISE_TORSO for rj in mc["rejected"]),
          "falošná prekážka z chýbajúceho kontaktu má zdvih pod prahom")
    check(g["conf"][0] == 2 and any("nízka dôvera" in r for r in g["conf"][1]), "brána dôvery kostry")
    check(g["rise"][0] == 2 and any("nereálny zdvih" in r for r in g["rise"][1]), "brána nereálneho zdvihu")
    check(g["sep_prev"][0] < 3 and any("príliš blízko k predchádzajúcej" in r for r in g["sep_prev"][1]),
          "minimálny odstup: blízky kandidát sa zamietne")
    check(g["sep_next"][0] >= 1 and g["sep_next"][1] == g["sep_next"][2]
          and any("príliš blízko k nasledujúcej" in r for r in g["sep_next"][3]),
          "minimálny odstup: vierohodnejší neskorší kandidát nahradí skorší")

    print("\n=== Video bez bežca ===")
    msg = refuse_low_detection()
    check(msg is not None and "40 %" in msg, "analýza odmietne video s kostrou na 40 % snímok v úseku s bežcom")

    print("\n=== Celý reťazec analyze_hurdle_video() ===")
    ok = end_to_end() and ok
    print("\n" + ("VŠETKY KONTROLY PREŠLI" if ok else "NIEKTORÉ KONTROLY ZLYHALI") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
