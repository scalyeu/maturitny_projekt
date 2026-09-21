# -*- coding: utf-8 -*-
"""
technique_rules.py
==================
Hodnotenie techniky z nameraných hodnôt – „čo je zlé a čo zlepšiť“.

Je to expertný systém založený na pravidlách: každé pravidlo porovná
nameranú veličinu s referenčným pásmom zo šprintérskej biomechaniky a vráti
stav (ok / pozor / problém), vysvetlenie a konkrétny tip do tréningu.
Pravidlá bežia lokálne, deterministicky a bez internetu – AI tréner (Groq)
ich potom môže rozviesť do súvislého komentára.

Referenčné pásma vychádzajú z literatúry o šprinte (Mann: The Mechanics of
Sprinting and Hurdling; Haugen et al. 2019; Weyand et al. 2000) a sú
zámerne mierne široké – ide o orientáciu pre mladého šprintéra, nie o
laboratórnu normu.  Všetky uhly sú v rovine obrazu, platia len pri zábere
presne z boku.

Konvencie uhlov (rovnaké ako vo video_analysis.py):
    knee_deg        180° = úplne vystreté koleno
    trunk_lean_deg  + = trup naklonený dopredu (v smere behu)
    shank_deg       + = koleno pred členkom (chodidlo dopadá pod telo),
                    - = chodidlo pred kolenom (dopad pred telom = brzdenie)
    thigh_deg       + = koleno pred bedrom, - = koleno za bedrom
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

OK, WATCH, BAD, INFO = "ok", "pozor", "problem", "info"
SEVERITY = {BAD: 0, WATCH: 1, INFO: 2, OK: 3}


def _mean(values) -> Optional[float]:
    v = [x for x in values if x is not None and np.isfinite(x)]
    return float(np.mean(v)) if v else None


def _finding(key: str, label: str, status: str, value: Optional[float], unit: str,
             reference: str, text: str, tip: str = "", value_fmt: str = "{:.0f}") -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "value": None if value is None else float(value),
        "value_text": ("—" if value is None else value_fmt.format(value) + unit),
        "reference": reference,
        "text": text,
        "tip": tip,
    }


# ---------------------------------------------------------------------------
# ŠPRINT
# ---------------------------------------------------------------------------

def sprint_feedback(summary: Dict[str, Any], steps: List[Dict[str, Any]], fps: float,
                    athlete_height_cm: Optional[float] = None,
                    phase: str = "auto") -> Dict[str, Any]:
    """
    `phase`: "max" (maximálna rýchlosť), "accel" (rozbeh / akcelerácia) alebo
    "auto" – odhadne sa z náklonu trupu a dĺžky kontaktu.
    """
    findings: List[Dict[str, Any]] = []
    s = summary or {}
    steps = steps or []

    td = [st.get("angles_touchdown") or {} for st in steps]
    to = [st.get("angles_toeoff") or {} for st in steps]
    trunk_td = _mean([a.get("trunk_lean_deg") for a in td])
    shank_td = _mean([a.get("shank_deg") for a in td])
    knee_td = _mean([a.get("knee_deg") for a in td])
    knee_to = _mean([a.get("knee_deg") for a in to])
    thigh_to = _mean([a.get("thigh_deg") for a in to])
    knee_min = _mean([st.get("knee_min_stance_deg") for st in steps])
    knee_drop = (knee_td - knee_min) if (knee_td is not None and knee_min is not None) else None

    contact = s.get("contact_ms_mean")
    flight = s.get("flight_ms_mean")
    cadence = s.get("cadence_spm")
    step_len = s.get("step_length_m")
    asym = s.get("asymmetry_pct")
    ct_sd = s.get("contact_ms_sd")
    vosc = s.get("vertical_oscillation_cm")

    # --- fáza behu ----------------------------------------------------------
    if phase not in ("max", "accel"):
        if trunk_td is not None and trunk_td > 22 and (contact or 0) > 125:
            phase = "accel"
        else:
            phase = "max"
    accel = phase == "accel"

    low_fps = fps < 100
    timing_note = " (pri < 100 fps je to orientačné)" if low_fps else ""

    # --- 1. kontakt so zemou ------------------------------------------------
    if contact is not None:
        if accel:
            ref = "rozbeh 120–180 ms (skracuje sa s každým krokom)"
            if contact <= 180:
                st, txt = OK, "Kontakt zodpovedá akceleračnej fáze."
            elif contact <= 210:
                st, txt = WATCH, "Kontakt je na rozbeh dlhší – odraz trvá príliš dlho."
            else:
                st, txt = BAD, "Veľmi dlhý kontakt aj na rozbeh."
            tip = "Sily do zeme rýchlejšie: odrazy z polodrepu, skoky na bedňu, sánky s ľahkou záťažou."
        else:
            ref = "max. rýchlosť: elita 80–100 ms, dobrá úroveň 100–125 ms"
            if contact <= 100:
                st, txt = OK, "Výborne krátky kontakt – reaktívny odraz na úrovni elity."
            elif contact <= 125:
                st, txt = OK, "Kontakt v dobrom pásme pre rýchlostného bežca."
            elif contact <= 145:
                st, txt = WATCH, ("Kontakt je dlhší, než by pri maximálnej rýchlosti mal byť – "
                                  "noha „sedí“ na zemi.")
            else:
                st, txt = BAD, ("Dlhý kontakt: každý krok brzdí. Typicky dopad ďaleko pred telom "
                                "alebo mäkký, prepadávajúci členok.")
            tip = ("Reaktívna sila: skákanie cez švihadlo, poskoky na jednej nohe, drop jumps z nízkej "
                   "výšky; pri behu mysli na „rýchlo zo zeme“, nie na silu odrazu.")
        findings.append(_finding("contact", "Kontakt so zemou", st, contact, " ms", ref,
                                 txt + timing_note, tip))

    # --- 2. pomer let / kontakt -------------------------------------------
    if contact and flight:
        ratio = flight / contact
        if accel:
            ref = "rozbeh 0.5–1.0 (postupne rastie)"
            st = OK if 0.4 <= ratio <= 1.2 else WATCH
            txt = ("Pomer letu a opory sedí na akceleráciu." if st == OK
                   else "Na rozbeh nezvyčajný pomer letu a opory.")
        else:
            ref = "max. rýchlosť 1.1–1.5"
            if 1.1 <= ratio <= 1.5:
                st, txt = OK, "Vyvážený pomer letovej fázy a opory."
            elif ratio < 1.1:
                st, txt = WATCH, ("Málo letu na dĺžku opory – rýchlosť stojí na frekvencii, "
                                  "odraz nedáva dosť impulzu.")
            elif ratio <= 1.8:
                st, txt = WATCH, "Dlhý let – možno „skáčeš“ hore namiesto dopredu."
            else:
                st, txt = BAD, "Príliš dlhý let: beh je poskakovaný, energia ide hore."
        findings.append(_finding("ratio", "Let / kontakt", st, ratio, "", ref, txt,
                                 "Cieľ je hnať panvu dopredu, nie hore: behy s dôrazom na nízky let "
                                 "a rýchly dopad pod telo.", "{:.2f}"))

    # --- 3. frekvencia ------------------------------------------------------
    if cadence is not None:
        if accel:
            ref = "rozbeh 220–270 krokov/min"
            st = OK if cadence >= 215 else WATCH
            txt = "Frekvencia v rozbehu v poriadku." if st == OK else "Nízka frekvencia už v rozbehu."
        else:
            ref = "max. rýchlosť 260–300 krokov/min (4.3–5.0 Hz)"
            if cadence >= 260:
                st, txt = OK, "Vysoká frekvencia krokov."
            elif cadence >= 240:
                st, txt = OK, "Frekvencia v normálnom pásme."
            elif cadence >= 225:
                st, txt = WATCH, "Nižšia frekvencia – ak je krok dlhý, je to voľba; inak rezerva."
            else:
                st, txt = BAD, "Nízka frekvencia krokov na šprint."
        findings.append(_finding("cadence", "Frekvencia krokov", st, cadence, " /min", ref,
                                 txt + timing_note,
                                 "Frekvenčné cvičenia: skipping s vysokou frekvenciou, behy z kopca "
                                 "(mierny sklon), zrýchľované úseky s dôrazom na rýchle nohy."))

    # --- 4. dĺžka kroku -----------------------------------------------------
    if step_len is not None and athlete_height_cm:
        ratio = step_len / (athlete_height_cm / 100.0)
        if accel:
            ref = "rozbeh: rastie z ~0.6 na ~1.2 × výška"
            st = OK if 0.5 <= ratio <= 1.3 else WATCH
            txt = "Dĺžka kroku zodpovedá rozbehu." if st == OK else "Nezvyčajná dĺžka kroku na rozbeh."
        else:
            ref = "max. rýchlosť 1.15–1.35 × telesná výška"
            if 1.15 <= ratio <= 1.35:
                st, txt = OK, "Dĺžka kroku sedí na výšku postavy."
            elif ratio < 1.15:
                st, txt = WATCH, ("Kratší krok – buď beh stojí na frekvencii, alebo odraz nedáva "
                                  "dosť impulzu dopredu.")
            elif ratio <= 1.45:
                st, txt = WATCH, "Dlhý krok – skontroluj, či nedopadáš pred telo (holeň pri dopade)."
            else:
                st, txt = BAD, "Krok je príliš dlhý na výšku – typický overstriding."
        findings.append(_finding("step_length", "Dĺžka kroku", st, step_len, " m",
                                 ref + f" (u teba {ratio:.2f}×)", txt,
                                 "Dĺžku kroku nezväčšuj natiahnutím nohy dopredu, ale silnejším "
                                 "odrazom a vyšším kolenom – odrazy, výbehy do kopca.", "{:.2f}"))
    elif step_len is not None:
        findings.append(_finding("step_length", "Dĺžka kroku", INFO, step_len, " m",
                                 "zadaj výšku pre porovnanie", "Bez výšky atléta sa dĺžka kroku nedá "
                                 "porovnať s referenčným pásmom.", "", "{:.2f}"))

    # --- 5. holeň pri dopade (brzdenie) -----------------------------------
    if shank_td is not None:
        ref = "dopad: −10° až +12° (holeň takmer zvislo, chodidlo pod telom)"
        if -10 <= shank_td <= 12:
            st, txt = OK, "Chodidlo dopadá pod telo – minimálne brzdenie."
        elif shank_td < -20:
            st, txt = BAD, ("Chodidlo dopadá výrazne pred koleno – každý dopad brzdí "
                            "(overstriding). Najčastejšia technická chyba.")
        elif shank_td < -10:
            st, txt = WATCH, "Chodidlo dopadá mierne pred koleno – jemné brzdenie pri každom kroku."
        else:
            st, txt = WATCH, ("Koleno je pri dopade výrazne pred členkom – dopad je „ťahaný“ "
                              "dozadu, môže ísť o krátky krok.")
        findings.append(_finding("shank_td", "Holeň pri dopade", st, shank_td, "°", ref, txt,
                                 "Cvič dopad pod telo: „padavý“ štart, skipping s aktívnym "
                                 "kladením chodidla dole-dozadu, behy s krátkym rýchlym krokom.",
                                 "{:+.0f}"))

    # --- 6. náklon trupu ----------------------------------------------------
    if trunk_td is not None:
        if accel:
            ref = "rozbeh 20–45° (klesá s každým krokom)"
            if 15 <= trunk_td <= 45:
                st, txt = OK, "Náklon trupu zodpovedá akcelerácii."
            elif trunk_td < 15:
                st, txt = WATCH, "Na rozbeh je trup dosť vzpriamený – skoro vstávaš."
            else:
                st, txt = WATCH, "Veľmi veľký náklon – pozor na predklon v páse namiesto celého tela."
        else:
            ref = "max. rýchlosť 0–12° (vzpriamený, mierne dopredu)"
            if 0 <= trunk_td <= 12:
                st, txt = OK, "Trup vzpriamený s miernym náklonom – správne."
            elif trunk_td < -4:
                st, txt = BAD, "Záklon trupu pri dopade – panva uteká dozadu, noha dopadá pred telo."
            elif trunk_td < 0:
                st, txt = WATCH, "Trup mierne v záklone."
            elif trunk_td <= 20:
                st, txt = WATCH, "Trup naklonený viac než treba – pri maximálnej rýchlosti by mal byť vzpriamený."
            else:
                st, txt = BAD, "Výrazný predklon pri maximálnej rýchlosti – bráni vysokému kolenu."
        findings.append(_finding("trunk", "Náklon trupu pri dopade", st, trunk_td, "°", ref, txt,
                                 "Postoj „vysoké boky“: panva pod trupom, pohľad dopredu; posilňuj "
                                 "stred tela (plank, vzpory, hollow hold).", "{:+.0f}"))

    # --- 7. koleno pri dopade a amortizácia --------------------------------
    if knee_td is not None:
        ref = "dopad 145–168°"
        if 145 <= knee_td <= 168:
            st, txt = OK, "Koleno pri dopade mierne pokrčené – pružný dopad."
        elif knee_td > 168:
            st, txt = WATCH, "Takmer vystretá noha pri dopade – tvrdý dopad a brzdenie."
        elif knee_td >= 130:
            st, txt = WATCH, "Dosť pokrčené koleno pri dopade – dopad je mäkký, sedíš do neho."
        else:
            st, txt = BAD, "Veľmi pokrčené koleno pri dopade – noha „prepadáva“."
        findings.append(_finding("knee_td", "Koleno pri dopade", st, knee_td, "°", ref, txt,
                                 "Pri dopade mysli na tuhé koleno a členok („pružina“), nie na "
                                 "tlmenie."))
    if knee_drop is not None:
        ref = "pokrčenie počas opory do 15°"
        if knee_drop <= 15:
            st, txt = OK, "Koleno počas opory drží – noha je tuhá, sila sa nestráca."
        elif knee_drop <= 25:
            st, txt = WATCH, "Koleno sa počas opory dosť prepadáva – slabšia tuhosť nohy."
        else:
            st, txt = BAD, "Koleno sa v opore výrazne prepadáva – „kolaps“, strata odrazu."
        findings.append(_finding("knee_drop", "Prepadnutie kolena v opore", st, knee_drop, "°", ref, txt,
                                 "Silový základ: drep, výpady, výstupy; reaktívne: poskoky, "
                                 "odrazy z krátkeho kontaktu."))

    # --- 8. odraz -----------------------------------------------------------
    if knee_to is not None:
        ref = "odraz 150–172° (nie úplne prepnuté koleno)"
        if 150 <= knee_to <= 172:
            st, txt = OK, "Odraz bez prepínania kolena – správne."
        elif knee_to > 172:
            st, txt = WATCH, ("Koleno sa pri odraze úplne prepína – odraz trvá dlhšie a noha "
                              "sa neskoro dostáva dopredu.")
        else:
            st, txt = WATCH, "Nedokončený odraz – noha odchádza zo zeme pokrčená."
        findings.append(_finding("knee_to", "Koleno pri odraze", st, knee_to, "°", ref, txt,
                                 "Odraz končí, keď panva prejde nad chodidlo; potom hneď „koleno hore“."))
    if thigh_to is not None:
        ref = "stehno pri odraze −15° až −35° za bedrom"
        if -35 <= thigh_to <= -12:
            st, txt = OK, "Rozsah extenzie bedra pri odraze je v norme."
        elif thigh_to > -12:
            st, txt = WATCH, "Malá extenzia bedra pri odraze – krátky, nedotiahnutý odraz."
        else:
            st, txt = WATCH, ("Noha ostáva dlho za telom (veľká zadná mechanika) – spomaľuje to "
                              "prenos nohy dopredu.")
        findings.append(_finding("thigh_to", "Stehno pri odraze", st, thigh_to, "°", ref, txt,
                                 "Dôraz na „predné“ mechaniky: koleno rýchlo dopredu-hore hneď po "
                                 "odraze; cvičenia A-skip, B-skip.", "{:+.0f}"))

    # --- 9. symetria a stabilita ------------------------------------------
    if asym is not None:
        ref = "do 5 % bežné, 5–10 % sleduj, nad 10 % rieš"
        if asym <= 5:
            st, txt = OK, "Ľavá a pravá noha sú v opore rovnako dlho."
        elif asym <= 10:
            st, txt = WATCH, "Mierny rozdiel medzi nohami – sleduj, či je trvalý."
        else:
            st, txt = BAD, "Výrazná asymetria kontaktov – jedna noha „sedí“ na zemi dlhšie."
        findings.append(_finding("asymmetry", "Asymetria Ľ/P", st, asym, " %", ref, txt,
                                 "Jednostranné cvičenia (výpady, poskoky na jednej nohe), "
                                 "kontrola pohyblivosti bedra a členka na slabšej strane.", "{:.1f}"))
    if contact and ct_sd is not None:
        cv = ct_sd / contact * 100
        ref = "rozptyl kontaktov do 10 %"
        if cv <= 10:
            st, txt = OK, "Kontakty sú krok po kroku rovnaké – stabilná technika."
        elif cv <= 18:
            st, txt = WATCH, "Kontakty kolíšu – technika nie je úplne stabilná (alebo šum merania)."
        else:
            st, txt = BAD, "Veľký rozptyl kontaktov – buď nestabilný beh, alebo slabá detekcia."
        findings.append(_finding("consistency", "Stabilita krokov", st, cv, " %", ref, txt,
                                 "Behy v rovnomernom tempe so zameraním na rytmus; skontroluj aj "
                                 "kvalitu videa.", "{:.0f}"))

    # --- 10. vertikálna oscilácia -----------------------------------------
    if vosc is not None:
        ref = "šprint 4–8 cm"
        if vosc <= 8:
            st, txt = OK, "Panva ide dopredu, nie hore."
        elif vosc <= 11:
            st, txt = WATCH, "Väčší zdvih panvy – časť energie ide hore."
        else:
            st, txt = BAD, "Veľký zdvih panvy – poskakovaný beh."
        findings.append(_finding("vosc", "Vertikálna oscilácia", st, vosc, " cm", ref, txt,
                                 "Nízky let: behy „cez“ nízke prekážky (kužele), dôraz na rýchly dopad.",
                                 "{:.1f}"))

    findings.sort(key=lambda f: SEVERITY.get(f["status"], 9))
    n_bad = sum(1 for f in findings if f["status"] == BAD)
    n_watch = sum(1 for f in findings if f["status"] == WATCH)
    n_ok = sum(1 for f in findings if f["status"] == OK)

    if not findings:
        overall = "Na hodnotenie techniky je málo dát."
    elif n_bad == 0 and n_watch == 0:
        overall = "Technika vyzerá čisto – žiadna z meraných veličín nie je mimo pásma."
    else:
        top = [f["label"].lower() for f in findings if f["status"] == BAD][:2] or \
              [f["label"].lower() for f in findings if f["status"] == WATCH][:2]
        overall = (f"{n_bad} vec{'' if n_bad == 1 else 'i'} na riešenie, {n_watch} na sledovanie, "
                   f"{n_ok} v norme. Najprv sa pozri na: {', '.join(top)}.")

    return {
        "phase": phase,
        "phase_text": "rozbeh / akcelerácia" if accel else "maximálna rýchlosť",
        "low_fps": low_fps,
        "findings": findings,
        "counts": {"problem": n_bad, "pozor": n_watch, "ok": n_ok},
        "overall": overall,
    }


# ---------------------------------------------------------------------------
# PREKÁŽKY
# ---------------------------------------------------------------------------

def hurdle_feedback(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pravidlá pre prekážkový beh – zámerne len to, čo sa dá spoľahlivo odčítať
    z bežného videa: počet krokov medzi prekážkami, medzičasy a odrazová noha.
    `result` je výstup hurdle_analysis.analyze_hurdle_video
    (kľúče: discipline, hurdles, intervals, summary, video).
    """
    findings: List[Dict[str, Any]] = []
    hurdles = result.get("hurdles") or []
    intervals = result.get("intervals") or []
    disc = result.get("discipline") or "other"
    long_race = disc == "400mH"

    if not hurdles:
        return {"findings": [], "overall": "Vo videu sa nenašla žiadna prekážka.", "counts": {}}

    # --- 1. počet krokov medzi prekážkami ---------------------------------
    counts = [iv.get("steps_between") for iv in intervals if iv.get("steps_between")]
    if counts:
        pattern = "-".join(str(c) for c in counts)
        if long_race:
            ref = "400 m: 13–15 krokov, zmena najviac o 1 medzi susednými prekážkami"
            jumps = [abs(a - b) for a, b in zip(counts, counts[1:])]
            if all(j <= 1 for j in jumps):
                st, txt = OK, f"Rytmus {pattern} – zmeny počtu krokov sú plynulé."
            else:
                st, txt = WATCH, (f"Rytmus {pattern} – skok o 2 kroky medzi susednými prekážkami "
                                  "znamená stratu rytmu.")
            tip = "Trénuj prechod 13→14 (alebo 14→15) krokov vedome, na vopred určenej prekážke."
        elif disc in ("110mH", "100mH"):
            ref = "3 kroky medzi prekážkami"
            if all(c == 3 for c in counts):
                st, txt = OK, "Trojkrokový rytmus medzi všetkými prekážkami."
            else:
                st, txt = BAD, f"Rytmus {pattern} – medzi prekážkami má byť vždy 3 kroky."
            tip = ("Skráť vzdialenosť prekážok na tréningu, kým rytmus 3 krokov nie je istý, "
                   "potom ju vracaj.")
        else:
            ref = "rovnaký počet medzi prekážkami"
            st = OK if len(set(counts)) == 1 else WATCH
            txt = f"Počet krokov medzi prekážkami: {pattern}."
            tip = ""
        findings.append(_finding("steps_between", "Kroky medzi prekážkami", st, None, "", ref, txt, tip))

    # --- 2. medzičasy: trend a stabilita -----------------------------------
    times = [iv.get("interval_s") for iv in intervals if iv.get("interval_s")]
    if len(times) >= 2:
        first, last = times[0], times[-1]
        change = (last - first) / first * 100
        if long_race:
            ref = "400 m: postupné spomalenie medzičasov do ~12 % je bežné"
            if change <= 6:
                st, txt = OK, f"Medzičasy držia tempo (zmena {change:+.0f} %)."
            elif change <= 14:
                st, txt = WATCH, (f"Medzičasy sa predlžujú o {change:.0f} % – bežná únava, "
                                  "ale sleduj rytmus.")
            else:
                st, txt = BAD, f"Výrazné spomalenie ({change:+.0f} %) – druhá polovica sa rozpadá."
            tip = "Špeciálna vytrvalosť: úseky 300–350 m cez prekážky v pretekovom rytme."
        else:
            ref = "medzičasy rovnaké ±3 %"
            sd = float(np.std(times, ddof=1)) if len(times) > 1 else 0.0
            cv = sd / float(np.mean(times)) * 100
            if cv <= 3:
                st, txt = OK, f"Medzičasy medzi prekážkami sú rovnomerné (±{cv:.1f} %)."
            elif cv <= 6:
                st, txt = WATCH, f"Medzičasy mierne kolíšu (±{cv:.1f} %)."
            else:
                st, txt = BAD, f"Medzičasy výrazne kolíšu (±{cv:.1f} %) – nestabilný rytmus."
            tip = ("Rytmické behy cez 5–8 prekážok s dôrazom na rovnaký zvuk krokov medzi "
                   "prekážkami.")
        findings.append(_finding("interval_trend", "Medzičasy", st, None, "", ref, txt, tip))

    # --- 3. najpomalší úsek -------------------------------------------------
    if len(times) >= 3:
        mean_t = float(np.mean(times))
        worst_i = int(np.argmax(times))
        worst = times[worst_i]
        iv = intervals[worst_i]
        dev = (worst - mean_t) / mean_t * 100
        ref = "žiadny úsek viac než 5 % nad priemerom"
        if dev <= 5:
            st, txt = OK, "Žiadny úsek výrazne nevybočuje z priemeru."
        else:
            st, txt = WATCH, (f"Úsek P{iv['from_hurdle']}→P{iv['to_hurdle']} je o {dev:.0f} % pomalší "
                              f"než priemer ({worst:.2f} s) – tam sa stráca najviac.")
        findings.append(_finding("worst_interval", "Najpomalší úsek", st, worst, " s", ref, txt,
                                 "Pozri si tento úsek vo videu: či nechýba krok, či je odraz z priveľkej "
                                 "diaľky alebo dopad ďaleko za prekážkou.", "{:.2f}"))

    # --- 4. striedanie odrazovej nohy (400 m) --------------------------------
    sides = [h.get("takeoff_side") for h in hurdles if h.get("takeoff_side")]
    if long_race and len(sides) >= 2:
        if len(set(sides)) == 1:
            findings.append(_finding("takeoff_leg", "Odrazová noha", INFO, None, "",
                                     "400 m: ideálne z oboch nôh",
                                     f"Všetky prekážky z {'ľavej' if sides[0] == 'L' else 'pravej'} nohy. "
                                     "Ak vieš ísť aj z druhej, nemusíš pri únave pridávať krok.",
                                     "Na tréningu prechádzaj prekážky striedavo z oboch nôh."))
        else:
            findings.append(_finding("takeoff_leg", "Odrazová noha", OK, None, "",
                                     "400 m: ideálne z oboch nôh",
                                     "Prekážky prechádzaš z oboch nôh – veľká výhoda pri zmene rytmu.", ""))

    findings.sort(key=lambda f: SEVERITY.get(f["status"], 9))
    n_bad = sum(1 for f in findings if f["status"] == BAD)
    n_watch = sum(1 for f in findings if f["status"] == WATCH)
    n_ok = sum(1 for f in findings if f["status"] == OK)
    if not findings:
        overall = "Na hodnotenie rytmu treba aspoň dve prekážky v zábere."
    elif n_bad == 0 and n_watch == 0:
        overall = "Rytmus medzi prekážkami je čistý – kroky aj medzičasy držia."
    else:
        top = [f["label"].lower() for f in findings if f["status"] == BAD][:2] or \
              [f["label"].lower() for f in findings if f["status"] == WATCH][:2]
        overall = (f"{n_bad} vec{'' if n_bad == 1 else 'i'} na riešenie, {n_watch} na sledovanie, "
                   f"{n_ok} v norme. Najprv: {', '.join(top)}.")
    return {"findings": findings, "counts": {"problem": n_bad, "pozor": n_watch, "ok": n_ok},
            "overall": overall}
