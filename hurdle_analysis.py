# -*- coding: utf-8 -*-
"""
hurdle_analysis.py
==================
Analýza prekážkového behu z videa – medzičasy medzi prekážkami, počet krokov
medzi nimi, letový čas nad prekážkou, odrazová noha a uhly pri odraze a dopade.

Ako to funguje
--------------
Stavia na tom istom jadre ako analýza šprintu (video_analysis.py): kostra
z MediaPipe, kontakty so zemou pre každú nohu, uhly v kĺboch.  Prekážku
netreba vo videu hľadať ako predmet – prezradí ju samotný beh:

    * medzi dvoma kontaktmi je pri behu let ~0.10–0.15 s,
    * pri prechode prekážky trvá let 0.30–0.50 s a panva sa zdvihne.

Každý „dlhý let“ je teda prechod prekážky.  Kontakt pred ním je odraz
(odrazová noha), kontakt po ňom dopad (švihová noha).  Z toho sa dá vyčítať
všetko, čo tréneri prekážkarov bežne stopujú ručne:

    * medzičas medzi prekážkami = dopad za prekážkou N+1 – dopad za prekážkou N
      (tzv. touchdown-to-touchdown čas),
    * počet krokov medzi prekážkami (rytmus 3 / 13 / 14 / 15…),
    * odrazová noha pri každej prekážke,
    * priemerná rýchlosť medzi prekážkami – vzdialenosť prekážok je daná
      pravidlami (400 m: 35 m, 110 m: 9.14 m, 100 m: 8.50 m), takže rýchlosť
      vyjde bez akejkoľvek kalibrácie kamery.

Medzičasy trvajú sekundy, preto stačí bežné video (±1 snímok = chyba pod 1 %).
Krátke veličiny (let nad prekážkou, kontakty) sa síce počítajú a ukladajú do
JSON-u, ale stránka ich nezobrazuje – pri bežnom videu by boli len orientačné.

Autor: maturitný projekt – AtletCoach
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

import numpy as np

import video_analysis as va

# Vzdialenosť medzi prekážkami podľa pravidiel (m)
HURDLE_SPACING_M = {
    "400mH": 35.0,
    "110mH": 9.14,
    "100mH": 8.50,
}
DISCIPLINE_LABEL = {
    "400mH": "400 m prekážky",
    "110mH": "110 m prekážky",
    "100mH": "100 m prekážky",
    "other": "iné / tréning",
}

MIN_HURDLE_FLIGHT_S = 0.22      # kratší let nie je prechod prekážky
MAX_HURDLE_FLIGHT_S = 0.80      # dlhší let = chýbajúce kontakty, nie prekážka


# Zlúčenie prekrývajúcich sa kontaktov (zámena ľavej a pravej nohy) je
# spoločné so šprintom – definované vo video_analysis.
dedupe_contacts = va.dedupe_contacts


def find_hurdle_flights(contacts: List[Dict[str, Any]], sig: Dict[str, Any], fps: float,
                        min_flight_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Nájde lety medzi kontaktmi, ktoré sú výrazne dlhšie než bežný krok.
    Vracia zoznam {i_takeoff, i_landing, flight_s, hip_rise_torso, score}.
    """
    if len(contacts) < 2:
        return []
    flights = []
    for i in range(len(contacts) - 1):
        a, b = contacts[i], contacts[i + 1]
        flights.append(b["td_time"] - a["to_time"])
    fl = np.array(flights, dtype=float)
    valid = fl[(fl > 0) & (fl < MAX_HURDLE_FLIGHT_S)]
    if valid.size == 0:
        return []
    # „bežný“ let = medián spodných 70 % (prekážkové lety sú vždy v hornej časti);
    # pri veľmi krátkom zázname (2 lety) sa berie ten kratší
    base = np.sort(valid)[: max(1, int(math.floor(0.7 * valid.size)))]
    normal_flight = float(np.median(base))
    thr = max(min_flight_s or MIN_HURDLE_FLIGHT_S, 1.7 * normal_flight)

    hip_y = sig["hip"][:, 1]
    hip_ref = sig["hip_y_ref"]
    torso = float(sig["torso_ref"])
    n = sig["n"]

    out = []
    for i, f in enumerate(flights):
        if not (thr <= f < MAX_HURDLE_FLIGHT_S):
            continue
        # Dva dlhé lety za sebou = chýbajúci kontakt v detekcii, nie dve prekážky
        if out and out[-1]["i_landing"] >= i:
            continue
        a, b = contacts[i], contacts[i + 1]
        lo = int(math.floor(a["to_frame"]))
        hi = int(math.ceil(b["td_frame"]))
        lo, hi = max(0, lo), min(n - 1, hi)
        rise = None
        apex = None
        if hi > lo:
            seg = (hip_ref[lo:hi + 1] - hip_y[lo:hi + 1]) / torso   # + = panva vyššie
            if np.isfinite(seg).any():
                k = int(np.nanargmax(seg))
                rise = float(np.nanmax(seg))
                apex = lo + k
        out.append({
            "i_takeoff": i, "i_landing": i + 1, "flight_s": float(f),
            "hip_rise_torso": rise, "apex_frame": apex,
            "normal_flight_s": normal_flight, "threshold_s": thr,
        })
    return out


def analyze_hurdle_video(
    path: str,
    discipline: str = "400mH",
    athlete_height_cm: Optional[float] = None,
    real_fps: Optional[float] = None,
    overlay_path: Optional[str] = None,
    with_flow: bool = True,
    init_point: Optional[tuple] = None,
    keyframes_dir: Optional[str] = None,
    keyframes_prefix: str = "hurdles",
    min_hurdle_flight_s: Optional[float] = None,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    max_frames: int = 9000,
) -> Dict[str, Any]:
    """Kompletná analýza prekážkového videa. Vracia slovník pripravený na JSON."""

    def prog(frac: float, msg: str):
        if progress_cb:
            progress_cb(min(max(frac, 0.0), 1.0), msg)

    discipline = discipline if discipline in HURDLE_SPACING_M or discipline == "other" else "other"
    spacing = HURDLE_SPACING_M.get(discipline)

    prog(0.02, "Načítavam video")
    info = va.probe_video(path)
    fps = float(real_fps) if real_fps else float(info.get("fps_ffprobe") or info["fps"] or 30.0)
    if fps <= 0:
        fps = 30.0

    # Optický tok sa pri prekážkach nevyužíva (medzičasy stoja len na kostre),
    # preto sa nepočíta – analýza je tak približne o tretinu rýchlejšia.
    lm = va.extract_landmarks(path, progress_cb=prog, with_flow=False,
                              roi_track=True, init_point=init_point, max_frames=max_frames)
    prog(0.72, "Počítam signály")
    sig = va.build_signals(lm["points"], lm["visibility"], fps)

    prog(0.76, "Hľadám kontakty so zemou")
    contacts = va.detect_contacts(sig, "L", fps) + va.detect_contacts(sig, "R", fps)
    contacts = dedupe_contacts(contacts)
    if len(contacts) < 3:
        raise va.VideoAnalysisError(
            "Našiel som príliš málo kontaktov so zemou. Prekážkový záber musí "
            "obsahovať celého bežca z boku, aspoň jednu prekážku a kroky pred ňou aj za ňou."
        )

    prog(0.78, "Hľadám prechody prekážok")
    flights = find_hurdle_flights(contacts, sig, fps, min_hurdle_flight_s)
    if not flights:
        raise va.VideoAnalysisError(
            "Vo videu som nenašiel prechod prekážky (žiadny let výrazne dlhší než bežný krok). "
            "Skontroluj, či je v zábere celá prekážka s krokmi pred aj po nej a či je bežec "
            "vidieť celý. Pri zábere z diaľky klikni na bežca v prvom snímku."
        )

    m_per_px = va.estimate_scale(sig, athlete_height_cm)
    torso_m = (float(sig["torso_ref"]) * m_per_px) if m_per_px else None

    # --- prekážky ---------------------------------------------------------
    hurdles: List[Dict[str, Any]] = []
    for k, f in enumerate(flights):
        a = contacts[f["i_takeoff"]]
        b = contacts[f["i_landing"]]
        ang_to = va.joint_angles_at(sig, a["to_frame"], a["side"])
        ang_land = va.joint_angles_at(sig, b["td_frame"], b["side"])
        rise_cm = None
        if f["hip_rise_torso"] is not None and torso_m:
            rise_cm = round(f["hip_rise_torso"] * torso_m * 100, 1)
        hurdles.append({
            "index": k + 1,
            "takeoff_contact": f["i_takeoff"] + 1,
            "landing_contact": f["i_landing"] + 1,
            "takeoff_side": a["side"],
            "landing_side": b["side"],
            "takeoff_side_text": "ľavá" if a["side"] == "L" else "pravá",
            "landing_side_text": "ľavá" if b["side"] == "L" else "pravá",
            "takeoff_td_s": round(a["td_time"], 3),
            "takeoff_s": round(a["to_time"], 3),          # odlepenie odrazovej nohy
            "landing_s": round(b["td_time"], 3),          # dopad švihovej nohy
            "flight_s": round(f["flight_s"], 3),
            "hurdle_stride_s": round(b["td_time"] - a["td_time"], 3),   # TD odraz -> TD dopad
            "takeoff_contact_ms": round(a["contact_s"] * 1000, 1),
            "landing_contact_ms": round(b["contact_s"] * 1000, 1),
            "angles_takeoff": ang_to,
            "angles_landing": ang_land,
            "hip_rise_torso": (round(f["hip_rise_torso"], 3) if f["hip_rise_torso"] is not None
                               else None),
            "hip_rise_cm": rise_cm,
            "apex_s": round(f["apex_frame"] / fps, 3) if f["apex_frame"] is not None else None,
            "confidence": round(min(a.get("confidence", 0), b.get("confidence", 0)), 3),
        })

    # --- medzičasy medzi prekážkami ----------------------------------------
    intervals: List[Dict[str, Any]] = []
    for k in range(len(hurdles) - 1):
        h1, h2 = hurdles[k], hurdles[k + 1]
        land_idx = h1["landing_contact"] - 1
        take_idx = h2["takeoff_contact"] - 1
        interval = h2["landing_s"] - h1["landing_s"]
        steps_between = take_idx - land_idx          # kroky po dopade vrátane odrazu
        # rytmus: časy jednotlivých krokov (dopad -> dopad) medzi prekážkami
        step_times = []
        contact_times = []
        for i in range(land_idx, take_idx):
            step_times.append(round((contacts[i + 1]["td_time"] - contacts[i]["td_time"]) * 1000, 1))
            contact_times.append(round(contacts[i + 1]["contact_s"] * 1000, 1))
        speed = (spacing / interval) if (spacing and interval > 0) else None
        intervals.append({
            "from_hurdle": h1["index"], "to_hurdle": h2["index"],
            "interval_s": round(interval, 3),
            "steps_between": int(steps_between),
            "step_times_ms": step_times,
            "contact_times_ms": contact_times,
            "step_time_mean_ms": round(float(np.mean(step_times)), 1) if step_times else None,
            "speed_ms": round(speed, 2) if speed else None,
            "speed_kmh": round(speed * 3.6, 1) if speed else None,
            "takeoff_side": h2["takeoff_side"],
        })

    # --- bežné šprintové ukazovatele medzi prekážkami ----------------------
    hurdle_contact_idx = set()
    for h in hurdles:
        hurdle_contact_idx.add(h["takeoff_contact"] - 1)
        hurdle_contact_idx.add(h["landing_contact"] - 1)
    run_contacts = [c for i, c in enumerate(contacts) if i not in hurdle_contact_idx]
    run_ct = [c["contact_s"] * 1000 for c in run_contacts]
    run_steps = []
    for i in range(len(contacts) - 1):
        if i in hurdle_contact_idx and (i + 1) in hurdle_contact_idx:
            continue
        dt = contacts[i + 1]["td_time"] - contacts[i]["td_time"]
        if va.MIN_STEP_S <= dt <= va.MAX_STEP_S:
            run_steps.append(dt * 1000)

    # --- súhrn -------------------------------------------------------------
    iv_times = [iv["interval_s"] for iv in intervals]
    iv_stats = va._nan_stats(iv_times)
    steps_pattern = [iv["steps_between"] for iv in intervals]
    flights_s = [h["flight_s"] for h in hurdles]
    land_ct = [h["landing_contact_ms"] for h in hurdles]
    take_ct = [h["takeoff_contact_ms"] for h in hurdles]
    sides = [h["takeoff_side"] for h in hurdles]
    speeds = [iv["speed_ms"] for iv in intervals if iv["speed_ms"]]

    t0 = hurdles[0]["landing_s"]
    touchdown_rel = [round(h["landing_s"] - t0, 3) for h in hurdles]

    trend_pct = None
    if len(iv_times) >= 2 and iv_times[0] > 0:
        trend_pct = round((iv_times[-1] - iv_times[0]) / iv_times[0] * 100, 1)

    frame_ms = 1000.0 / fps
    warnings: List[str] = []
    # Medzičasy trvajú sekundy – ±1 snímok je aj pri 30 fps chyba pod 1 %,
    # preto sa nízke fps nehlási ako problém (spomalený záber netreba).
    if lm["detected_ratio"] < 0.85:
        warnings.append(
            f"Kostra bola rozpoznaná len na {lm['detected_ratio'] * 100:.0f} % snímok – "
            "pri zábere z diaľky klikni na bežca v prvom snímku a skús lepšie svetlo.")
    if len(hurdles) == 1:
        warnings.append("V zábere je len jedna prekážka – medzičas medzi prekážkami sa nedá určiť. "
                        "Natoč úsek s aspoň dvoma prekážkami.")
    if not spacing:
        warnings.append("Bez zvolenej disciplíny nepoznám vzdialenosť prekážok, takže chýba "
                        "priemerná rýchlosť medzi prekážkami.")
    if discipline in ("110mH", "100mH") and any(s not in (3, 4) for s in steps_pattern):
        warnings.append("Počet krokov medzi prekážkami nezodpovedá 3 (ani 4) – pravdepodobne "
                        "chýba kontakt v detekcii, alebo bol nájdený falošný prechod prekážky.")
    if discipline == "400mH" and any(s < 11 or s > 19 for s in steps_pattern):
        warnings.append("Počet krokov medzi prekážkami je mimo 11–19 – buď video nezachytáva "
                        "celý úsek medzi prekážkami, alebo bol nájdený falošný prechod prekážky.")
    if spacing and speeds and (min(speeds) < 4.0 or max(speeds) > 11.5):
        warnings.append("Vypočítaná rýchlosť medzi prekážkami je mimo reálneho rozsahu – "
                        "skontroluj disciplínu a skutočné fps videa.")
    low_conf = [h["index"] for h in hurdles if h["confidence"] < 0.5]
    if low_conf:
        warnings.append("Nízka dôvera kostry pri prekážke č. " + ", ".join(map(str, low_conf)) +
                        " – skontroluj na snímkach, či appka označila odraz a dopad správne.")

    summary = {
        "discipline": discipline,
        "discipline_label": DISCIPLINE_LABEL.get(discipline, discipline),
        "spacing_m": spacing,
        "hurdles_detected": len(hurdles),
        "contacts_detected": len(contacts),
        "interval_mean_s": round(iv_stats["mean"], 3) if iv_stats["mean"] else None,
        "interval_sd_s": round(iv_stats["sd"], 3) if iv_stats["sd"] is not None else None,
        "interval_min_s": iv_stats["min"], "interval_max_s": iv_stats["max"],
        "interval_trend_pct": trend_pct,
        "steps_pattern": steps_pattern,
        "steps_pattern_text": "-".join(str(s) for s in steps_pattern) if steps_pattern else None,
        "flight_ms_mean": round(float(np.mean(flights_s)) * 1000, 1) if flights_s else None,
        "flight_ms_sd": (round(float(np.std(flights_s, ddof=1)) * 1000, 1)
                         if len(flights_s) > 1 else None),
        "landing_contact_ms_mean": round(float(np.mean(land_ct)), 1) if land_ct else None,
        "takeoff_contact_ms_mean": round(float(np.mean(take_ct)), 1) if take_ct else None,
        "run_contact_ms_mean": round(float(np.mean(run_ct)), 1) if run_ct else None,
        "run_step_time_ms_mean": round(float(np.mean(run_steps)), 1) if run_steps else None,
        "run_cadence_spm": (round(60000.0 / float(np.mean(run_steps)), 1) if run_steps else None),
        "speed_between_ms_mean": round(float(np.mean(speeds)), 2) if speeds else None,
        "speed_between_kmh_mean": round(float(np.mean(speeds)) * 3.6, 1) if speeds else None,
        "takeoff_sides": sides,
        "takeoff_left": sides.count("L"), "takeoff_right": sides.count("R"),
        "touchdown_rel_s": touchdown_rel,
        "hip_rise_cm_mean": (round(float(np.mean([h["hip_rise_cm"] for h in hurdles
                                                  if h["hip_rise_cm"] is not None])), 1)
                             if any(h["hip_rise_cm"] is not None for h in hurdles) else None),
        "m_per_px": m_per_px,
    }

    # --- kroky (tabuľka všetkých kontaktov) --------------------------------
    steps_out = []
    hurdle_of_contact = {}
    for h in hurdles:
        hurdle_of_contact[h["takeoff_contact"] - 1] = ("odraz", h["index"])
        hurdle_of_contact[h["landing_contact"] - 1] = ("dopad", h["index"])
    for i, c in enumerate(contacts):
        nxt = contacts[i + 1] if i + 1 < len(contacts) else None
        role = hurdle_of_contact.get(i)
        steps_out.append({
            "index": i + 1,
            "side": "Ľavá" if c["side"] == "L" else "Pravá",
            "side_code": c["side"],
            "touchdown_s": round(c["td_time"], 3),
            "contact_ms": round(c["contact_s"] * 1000, 1),
            "flight_ms": round((nxt["td_time"] - c["to_time"]) * 1000, 1) if nxt else None,
            "step_time_ms": round((nxt["td_time"] - c["td_time"]) * 1000, 1) if nxt else None,
            "role": f"{role[0]} P{role[1]}" if role else "",
            "role_code": role[0] if role else "",
            "hurdle": role[1] if role else None,
            "confidence": round(c.get("confidence", 0), 3),
        })

    result: Dict[str, Any] = {
        "kind": "hurdles",
        "discipline": discipline,
        "video": {
            "fps": round(fps, 2),
            "fps_container": round(float(info.get("fps") or 0), 2),
            "frames": lm["n_frames"],
            "duration_s": round(lm["n_frames"] / fps, 2),
            "width": lm["width"], "height": lm["height"],
            "detected_ratio": round(lm["detected_ratio"], 3),
            "crop_ratio": round(float(lm.get("crop_ratio") or 0.0), 3),
        },
        "accuracy": {"frame_ms": round(frame_ms, 2), "uncertainty_ms": round(frame_ms * 0.5, 1)},
        "summary": summary,
        "hurdles": hurdles,
        "intervals": intervals,
        "steps": steps_out,
        "warnings": warnings,
    }

    # --- hodnotenie techniky -----------------------------------------------
    try:
        from technique_rules import hurdle_feedback
        result["technique"] = hurdle_feedback(result)
    except Exception as e:
        result["technique"] = {"error": f"Hodnotenie techniky zlyhalo: {e}", "findings": []}

    # --- kľúčové snímky: odraz, vrchol letu, dopad každej prekážky ---------
    keyframes: List[Dict[str, Any]] = []
    if keyframes_dir:
        prog(0.79, "Ukladám kľúčové snímky")
        events = []
        for h in hurdles[:4]:
            a = contacts[h["takeoff_contact"] - 1]
            b = contacts[h["landing_contact"] - 1]
            events.append({"frame": int(round(a["to_frame"])), "side": a["side"],
                           "label": f"Prekážka {h['index']} – odraz ({h['takeoff_side_text']})",
                           "event": "toeoff", "hurdle": h["index"]})
            if h["apex_s"] is not None:
                events.append({"frame": int(round(h["apex_s"] * fps)), "side": b["side"],
                               "label": f"Prekážka {h['index']} – nad prekážkou",
                               "event": "apex", "hurdle": h["index"]})
            events.append({"frame": int(round(b["td_frame"])), "side": b["side"],
                           "label": f"Prekážka {h['index']} – dopad ({h['landing_side_text']})",
                           "event": "touchdown", "hurdle": h["index"]})
        try:
            keyframes = va.render_keyframes(path, sig, contacts, fps, keyframes_dir, keyframes_prefix,
                                            extra_events=events, max_contacts=0)
        except Exception:
            keyframes = []
    result["keyframes"] = keyframes

    # --- prekryvné video ---------------------------------------------------
    overlay_out = None
    if overlay_path:
        prog(0.80, "Vykresľujem video")
        ev = []
        for h in hurdles:
            a = contacts[h["takeoff_contact"] - 1]
            b = contacts[h["landing_contact"] - 1]
            ev.append({"lo": int(math.floor(a["td_frame"])), "hi": int(math.ceil(b["to_frame"])),
                       "label": f"PREKAZKA {h['index']}"})
        for iv in intervals:
            h1 = hurdles[iv["from_hurdle"] - 1]
            h2 = hurdles[iv["to_hurdle"] - 1]
            ev.append({"lo": int(math.ceil(contacts[h1["landing_contact"] - 1]["to_frame"])) + 1,
                       "hi": int(math.floor(contacts[h2["takeoff_contact"] - 1]["td_frame"])) - 1,
                       "label": (f"medzi P{iv['from_hurdle']} a P{iv['to_hurdle']}: "
                                 f"{iv['interval_s']:.2f} s, {iv['steps_between']} krokov")})
        try:
            overlay_out = va.render_overlay(path, overlay_path, sig, contacts, fps, prog,
                                            draw_angles=True, events=ev)
        except Exception:
            overlay_out = None
    result["overlay_path"] = overlay_out

    prog(0.99, "Hotovo")
    return result
