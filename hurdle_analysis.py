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

Každý „dlhý let“ je kandidát na prechod prekážky.  Kontakt pred ním je odraz
(odrazová noha), kontakt po ňom dopad (švihová noha).  Dlhý let ale vznikne aj
vtedy, keď detekcii vypadne jeden kontakt uprostred bežných krokov, preto
kandidát prejde ešte kontrolou: odraz a dopad musia byť z rôznych nôh, kostra
pri oboch kontaktoch viditeľná, panva sa počas letu musí zdvihnúť a dve
prekážky nemôžu byť bližšie, než dovoľuje najrýchlejší medzičas disciplíny.
Rovnako každý úsek medzi prekážkami prejde kontrolou (žiadna diera v sledovaní,
medzičas v rozsahu disciplíny, žiadny chýbajúci ani prekrývajúci sa kontakt);
pre neúplný úsek sa počet krokov neudáva.  Z toho sa dá vyčítať všetko, čo
tréneri prekážkarov bežne stopujú ručne:

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

# Rozsah medzičasu dopad → dopad (s) podľa disciplíny.  Spodná hranica je pod
# najrýchlejšími úsekmi, aké sa kedy zabehli (400 m: 35 m / 3,2 s = 10,9 m/s;
# 110 m: 9,14 m / 0,85 s = 10,8 m/s), horná pokrýva aj školských začiatočníkov
# (400 m: 35 m / 9,5 s = 3,7 m/s; 110 m: 9,14 m / 2,5 s = 3,7 m/s).  Kratší
# „medzičas“ znamená falošnú prekážku, dlhší výpadok sledovania alebo zlé fps.
HURDLE_INTERVAL_S = {
    "400mH": (3.2, 9.5),
    "110mH": (0.85, 2.5),
    "100mH": (0.80, 2.5),
}
# Počet krokov medzi prekážkami, aký sa dá v disciplíne vôbec zabehnúť.  Viac =
# úsek spája dva medzičasy (prekážka medzi nimi sa nenašla), menej = chýbajú kontakty.
HURDLE_STEPS_RANGE = {
    "400mH": (11, 19),
    "110mH": (3, 4),
    "100mH": (3, 4),
}
# Tréning / iné: aj 1-krokový rytmus cez nízke prekážky (~3–4 m) trvá dopad → dopad
# ~0,6–0,8 s (let 0,3 + kontakt 0,1 + jeden krok 0,25).
MIN_HURDLE_SEP_OTHER_S = 0.5

# Prechod prekážky musí vyzerať ako prechod prekážky, nielen ako dlhší let:
MIN_CONTACT_CONF = 0.30         # kontakt odrazu aj dopadu musí stáť na videnej kostre
# Zdvih panvy sa meria voči výške panvy počas odrazu a dopadu (v dĺžkach trupu).
# Skutočný prechod prekážky (0,68–1,07 m) zdvihne panvu o 0,25–0,6 trupu, bežný
# krok s vypadnutým kontaktom dá len kmitanie panvy pri behu (~0,05–0,15).
MIN_HIP_RISE_TORSO = 0.10
MAX_HIP_RISE_TORSO = 1.20       # väčší „zdvih“ (> ~55 cm) = kostra preskočila inam, nie skok
MAX_STEP_RATIO = 1.6            # krok 1,6× dlhší než medián = chýbajúci kontakt
# Pod polovicou snímok s kostrou (v úseku, kde bežec v zábere je) nejde o záber bežca.
MIN_DETECTED_RATIO = 0.50


# Zlúčenie prekrývajúcich sa kontaktov (zámena ľavej a pravej nohy) je
# spoločné so šprintom – definované vo video_analysis.
dedupe_contacts = va.dedupe_contacts


def find_hurdle_flights(contacts: List[Dict[str, Any]], sig: Dict[str, Any], fps: float,
                        min_flight_s: Optional[float] = None,
                        min_sep_s: Optional[float] = None,
                        rejected: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Nájde lety medzi kontaktmi, ktoré sú výrazne dlhšie než bežný krok, a z nich
    nechá len tie, ktoré vyzerajú ako prechod prekážky: odraz a dopad z rôznych
    nôh, viditeľná kostra pri oboch kontaktoch a zdvih panvy počas letu.
    Dlhý let sám osebe nestačí – vznikne aj vtedy, keď detekcii vypadne jeden
    kontakt uprostred bežných krokov.

    `min_sep_s` – dve prekážky tej istej disciplíny nemôžu byť bližšie než
    najrýchlejší možný medzičas; z bližšej dvojice ostane vierohodnejšia.
    `rejected` – ak je zadaný zoznam, pribudnú doň zamietnutí kandidáti s dôvodmi.
    Vracia zoznam {i_takeoff, i_landing, flight_s, hip_rise_torso, ...}.
    """
    if len(contacts) < 2:
        return []

    def reject(i: int, f: float, rise: Optional[float], reasons: List[str],
               sep_s: Optional[float] = None) -> None:
        if rejected is not None:
            rejected.append({
                "i_takeoff": i, "i_landing": i + 1,
                "t_s": round(float(contacts[i]["to_time"]), 2),
                "flight_s": round(float(f), 3),
                "hip_rise_torso": round(rise, 3) if rise is not None else None,
                "sep_s": round(sep_s, 3) if sep_s is not None else None,   # len pri „príliš blízko“
                "reasons": reasons,
            })
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

    out: List[Dict[str, Any]] = []
    for i, f in enumerate(flights):
        if not (thr <= f < MAX_HURDLE_FLIGHT_S):
            continue
        a, b = contacts[i], contacts[i + 1]
        # Dva dlhé lety za sebou = chýbajúci kontakt v detekcii, nie dve prekážky
        if out and out[-1]["i_landing"] >= i:
            reject(i, f, None, ["dva dlhé lety za sebou – v detekcii chýba kontakt"])
            continue
        lo = int(math.floor(a["to_frame"]))
        hi = int(math.ceil(b["td_frame"]))
        lo, hi = max(0, lo), min(n - 1, hi)
        rise = None
        apex = None
        if hi > lo:
            # Základňa = výška panvy počas kontaktu odrazu a dopadu.  Vyhladená
            # referencia (0,8 s) by pohltila asi polovicu zdvihu, lokálna základňa
            # meria skutočný zdvih a nezávisí od fps ani od dĺžky okna.
            def hip_during(c):
                s0 = max(0, int(math.floor(c["td_frame"])))
                e0 = min(n - 1, int(math.ceil(c["to_frame"])))
                v = hip_y[s0:e0 + 1]
                return float(np.nanmean(v)) if (v.size and np.isfinite(v).any()) else float("nan")
            base_vals = [hip_during(a), hip_during(b)]
            base = float(np.nanmean(base_vals)) if np.isfinite(base_vals).any() else float("nan")
            if not np.isfinite(base):
                base = float(np.nanmean(hip_ref[lo:hi + 1]))
            seg = (base - hip_y[lo:hi + 1]) / torso   # + = panva vyššie než pri odraze/dopade
            if np.isfinite(seg).any():
                k = int(np.nanargmax(seg))
                rise = float(np.nanmax(seg))
                apex = lo + k

        # --- vyzerá to ako prechod prekážky? ----------------------------------
        reasons = []
        conf = float(min(a.get("confidence", 0), b.get("confidence", 0)))
        if conf < MIN_CONTACT_CONF:
            reasons.append(f"nízka dôvera kostry pri odraze alebo dopade ({conf:.2f})")
        if a["side"] == b["side"]:
            # Kontakty so zemou sa pri behu vždy striedajú: odraz je z jednej
            # nohy, dopad za prekážkou z druhej.  Rovnaká noha = vypadnutý kontakt.
            reasons.append("odraz a dopad z tej istej nohy")
        if rise is None:
            reasons.append("zdvih panvy sa nedá odčítať")
        elif rise < MIN_HIP_RISE_TORSO:
            reasons.append(f"panva sa počas letu nezdvihla ({rise:.2f} trupu)")
        elif rise > MAX_HIP_RISE_TORSO:
            reasons.append(f"nereálny zdvih panvy ({rise:.2f} trupu) – kostra preskočila")
        if reasons:
            reject(i, f, rise, reasons)
            continue

        cand = {
            "i_takeoff": i, "i_landing": i + 1, "flight_s": float(f),
            "hip_rise_torso": rise, "apex_frame": apex,
            "normal_flight_s": normal_flight, "threshold_s": thr,
            "score": rise * conf,
        }
        # --- príliš blízko k predchádzajúcej prekážke -> ostane vierohodnejšia ---
        if out and min_sep_s:
            prev = out[-1]
            sep = float(b["td_time"] - contacts[prev["i_landing"]]["td_time"])
            if sep < min_sep_s:
                if cand["score"] > prev["score"]:
                    reject(prev["i_takeoff"], prev["flight_s"], prev["hip_rise_torso"],
                           [f"príliš blízko k nasledujúcej prekážke ({sep:.2f} s)"], sep_s=sep)
                    out[-1] = cand
                else:
                    reject(i, f, rise, [f"príliš blízko k predchádzajúcej prekážke ({sep:.2f} s)"],
                           sep_s=sep)
                continue
        out.append(cand)
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
    min_detected_ratio: Optional[float] = None,
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
    # Podiel snímok s kostrou sa berie len v úseku od prvého po posledný nájdený
    # snímok – prázdny rozbeh a dobeh statickej kamery nie sú chyba záberu.
    pts = np.asarray(lm["points"])
    hip_seen = np.isfinite(pts[:, va.L_HIP, 0]) | np.isfinite(pts[:, va.R_HIP, 0])
    seen_idx = np.flatnonzero(hip_seen)
    span_ratio = float(hip_seen[seen_idx[0]:seen_idx[-1] + 1].mean()) if seen_idx.size else 0.0
    ratio_limit = MIN_DETECTED_RATIO if min_detected_ratio is None else float(min_detected_ratio)
    if span_ratio < ratio_limit:
        raise va.VideoAnalysisError(
            f"Bežca som našiel len na {span_ratio * 100:.0f} % snímok v úseku, kde je v zábere, takže "
            "z tohto videa sa medzičasy nedajú zmerať. Analýza potrebuje jeden súvislý záber z boku "
            "na jedného bežca: telefón kolmo na dráhu, bežec celý v obraze, aspoň dve prekážky "
            "s krokmi pred nimi aj za nimi, bez strihov a bez záznamu z televízie. Ak je bežec "
            "ďaleko, klikni naň v prvom snímku."
        )
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
    band = HURDLE_INTERVAL_S.get(discipline)
    min_sep_s = band[0] if band else MIN_HURDLE_SEP_OTHER_S
    rejected: List[Dict[str, Any]] = []
    flights = find_hurdle_flights(contacts, sig, fps, min_hurdle_flight_s,
                                  min_sep_s=min_sep_s, rejected=rejected)
    disc_label = DISCIPLINE_LABEL.get(discipline, discipline)
    # Keď pravidlo „príliš blízko“ zahodilo viac prechodov, než ostalo, video je
    # z inej disciplíny (napr. 110 m analyzované ako 400 m) – radšej to povedať,
    # než z každej tretej prekážky poskladať vierohodne vyzerajúci nezmysel.
    close = [r for r in rejected if r.get("sep_s") is not None]
    if len(close) >= 2 and len(close) > len(flights):
        seps = sorted(r["sep_s"] for r in close)
        raise va.VideoAnalysisError(
            f"Zvolená disciplína ({disc_label}) nesedí na video: našiel som "
            f"{len(close) + len(flights)} prechodov prekážok, ale väčšina je od seba len "
            f"~{seps[len(seps) // 2]:.1f} s, čo je pod najrýchlejším možným medzičasom "
            f"{disc_label} ({min_sep_s:.2g} s). Skontroluj, či je zvolená správna disciplína."
        )
    if not flights:
        if rejected:
            why = "; ".join(f"t = {r['t_s']:.1f} s: {', '.join(r['reasons'])}" for r in rejected[:5])
            raise va.VideoAnalysisError(
                f"Našiel som {len(rejected)} dlhých letov, ale žiadny nevyzerá ako prechod prekážky "
                f"({why}). Skontroluj, či je bežec celý v zábere z boku a či záber nie je strihaný."
            )
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
    # Každý úsek prejde kontrolou vierohodnosti.  `valid` = medzičas a rýchlosť
    # sa dajú použiť (žiadna diera v sledovaní, trvanie v rozsahu disciplíny);
    # `steps_valid` = aj počet krokov je dôveryhodný (žiadny prekrývajúci sa
    # kontakt ani krok dvakrát dlhší než ostatné, čo je chýbajúci kontakt).
    # Pre neúplný úsek sa počet krokov neudáva – radšej „?“ než nesprávne číslo.
    steps_range = HURDLE_STEPS_RANGE.get(discipline)
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

        reasons: List[str] = []
        gaps = [st for st in step_times if st > va.MAX_STEP_S * 1000]
        if gaps:
            reasons.append(f"výpadok sledovania ({len(gaps)}× medzera až {max(gaps) / 1000:.1f} s bez kroku)")
        if band and not (band[0] <= interval <= band[1]):
            reasons.append(f"medzičas {interval:.2f} s je mimo rozsahu {disc_label} "
                           f"({band[0]:.2g}–{band[1]:.2g} s) – skontroluj disciplínu a fps videa")
        # Zamietnuté dlhé lety vnútri úseku: so zdvihom panvy ako pri skutočnej
        # prekážke (padli len na zámene nôh) úsek asi spája dva medzičasy; bez
        # zdvihu je to chýbajúci kontakt, ktorý kazí len počet krokov.
        inner = [r for r in rejected if land_idx <= r["i_takeoff"] < take_idx]
        merged = [r for r in inner if (r["hip_rise_torso"] or 0) >= MIN_HIP_RISE_TORSO]
        if merged:
            reasons.append("vnútri úseku je zamietnutý prechod prekážky so zdvihom panvy (t = "
                           + ", ".join(f"{r['t_s']:.1f} s" for r in merged)
                           + ") – medzičas asi spája dva úseky")
        if steps_range and steps_between > steps_range[1]:
            reasons.append(f"{steps_between} krokov je na {disc_label} priveľa (najviac "
                           f"{steps_range[1]}) – medzičas asi spája dva úseky")
        valid = not reasons
        if valid:
            short = [st for st in step_times if st < va.MIN_STEP_S * 1000]
            if short:
                reasons.append(f"prekrývajúce sa kontakty ({len(short)}× krok pod "
                               f"{va.MIN_STEP_S * 1000:.0f} ms)")
            if len(step_times) >= 3:
                med = float(np.median(step_times))
                if med > 0 and max(step_times) > MAX_STEP_RATIO * med:
                    reasons.append(f"pravdepodobne chýba kontakt (krok {max(step_times):.0f} ms je "
                                   f"{max(step_times) / med:.1f}× dlhší než ostatné)")
            if inner:
                reasons.append("pravdepodobne chýba kontakt (dlhý let v t = "
                               + ", ".join(f"{r['t_s']:.1f} s" for r in inner) + " nie je prekážka)")
            if steps_range and steps_between < steps_range[0]:
                reasons.append(f"{steps_between} krokov je na {disc_label} primálo (najmenej "
                               f"{steps_range[0]}) – asi chýbajú kontakty")
        steps_valid = valid and not reasons
        speed = (spacing / interval) if (spacing and interval > 0 and valid) else None
        intervals.append({
            "from_hurdle": h1["index"], "to_hurdle": h2["index"],
            "interval_s": round(interval, 3),
            "valid": valid,
            "steps_valid": steps_valid,
            "invalid_reason": "; ".join(reasons) if reasons else None,
            "steps_between": int(steps_between) if steps_valid else None,
            "steps_between_raw": int(steps_between),
            "step_times_ms": step_times,
            "contact_times_ms": contact_times,
            "step_time_mean_ms": (round(float(np.mean(step_times)), 1)
                                  if (step_times and steps_valid) else None),
            "speed_ms": round(speed, 2) if speed else None,
            "speed_kmh": round(speed * 3.6, 1) if speed else None,
            "takeoff_side": h2["takeoff_side"],
        })
    valid_intervals = [iv for iv in intervals if iv["valid"]]
    n_steps_valid = sum(1 for iv in intervals if iv["steps_valid"])

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

    # --- súhrn (len z úplných úsekov) ---------------------------------------
    iv_times = [iv["interval_s"] for iv in valid_intervals]
    iv_stats = va._nan_stats(iv_times)
    steps_pattern = [iv["steps_between"] for iv in intervals if iv["steps_valid"]]
    steps_pattern_text = ("-".join(str(iv["steps_between"]) if iv["steps_valid"] else "?"
                                   for iv in intervals) if steps_pattern else None)
    flights_s = [h["flight_s"] for h in hurdles]
    land_ct = [h["landing_contact_ms"] for h in hurdles]
    take_ct = [h["takeoff_contact_ms"] for h in hurdles]
    sides = [h["takeoff_side"] for h in hurdles]
    speeds = [iv["speed_ms"] for iv in valid_intervals if iv["speed_ms"]]

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
    bad_ivs = [iv for iv in intervals if not iv["steps_valid"]]
    if bad_ivs:
        warnings.append(
            f"{len(bad_ivs)} z {len(intervals)} úsekov medzi prekážkami je neúplných, počet krokov "
            "sa pre ne neudáva: " + "; ".join(
                f"P{iv['from_hurdle']}→P{iv['to_hurdle']}: {iv['invalid_reason']}" for iv in bad_ivs))
    if rejected:
        shown = rejected[:6]
        warnings.append(
            f"Zamietol som {len(rejected)} dlhých letov, ktoré nevyzerali ako prechod prekážky: "
            + "; ".join(f"t = {r['t_s']:.1f} s ({', '.join(r['reasons'])})" for r in shown)
            + (" …" if len(rejected) > len(shown) else "") + ".")

    # --- spoľahlivosť výsledku ako celku ----------------------------------------
    rel_reasons: List[str] = []
    rel_level = "ok"
    if span_ratio < 0.70:
        rel_level = "low"
        rel_reasons.append(f"kostra rozpoznaná len na {span_ratio * 100:.0f} % snímok v úseku s bežcom")
    if bad_ivs:
        rel_level = "low"
        rel_reasons.append(f"{len(bad_ivs)} z {len(intervals)} úsekov medzi prekážkami je neúplných")
    if len(hurdles) < 2:
        rel_level = "unusable"
        rel_reasons.append("v zábere je len jedna prekážka")
    elif not valid_intervals:
        rel_level = "unusable"
        rel_reasons.append("žiadny úsek medzi prekážkami neprešiel kontrolou vierohodnosti")
    reliability = {"level": rel_level, "reasons": rel_reasons}

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
        "steps_pattern_text": steps_pattern_text,
        "intervals_total": len(intervals),
        "intervals_valid": len(valid_intervals),
        "intervals_steps_valid": n_steps_valid,
        "rejected_candidates": len(rejected),
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
            "detected_span_ratio": round(span_ratio, 3),
            "crop_ratio": round(float(lm.get("crop_ratio") or 0.0), 3),
        },
        "accuracy": {"frame_ms": round(frame_ms, 2), "uncertainty_ms": round(frame_ms * 0.5, 1)},
        "summary": summary,
        "hurdles": hurdles,
        "intervals": intervals,
        "rejected": rejected,
        "steps": steps_out,
        "warnings": warnings,
        "reliability": reliability,
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
                                 + (f"{iv['interval_s']:.2f} s, " if iv["valid"] else "")
                                 + (f"{iv['steps_between']} krokov" if iv["steps_valid"]
                                    else "neuplny zaznam"))})
        try:
            overlay_out = va.render_overlay(path, overlay_path, sig, contacts, fps, prog,
                                            draw_angles=True, events=ev)
        except Exception:
            overlay_out = None
    result["overlay_path"] = overlay_out

    prog(0.99, "Hotovo")
    return result
