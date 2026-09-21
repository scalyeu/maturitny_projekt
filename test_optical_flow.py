# -*- coding: utf-8 -*-
"""
test_optical_flow.py
====================
Test detekcie kontaktu so zemou pomocou OPTICKÉHO TOKU (čistý OpenCV).

Na rozdiel od test_video_analysis.py tu ide o SKUTOČNÉ VIDEO – vykreslí sa
textúrovaná scéna (zem so vzorom, pozadie, pohybujúce sa chodidlo) a beží na
nej naozajstný Lucas-Kanade tok.  MediaPipe sa nepoužíva: polohy chodidiel
sú známe, takže sa testuje presne to, čo má – tok a kompenzácia pohybu kamery.

Testuje sa aj to najdôležitejšie: či detekcia funguje, keď kamera UHÝBA
za bežcom (panning).  Vtedy sa v obraze hýbe úplne všetko a bez kompenzácie
pohybu kamery by chodidlo počas opory vôbec nevyzeralo nehybné.

Spustenie:   python test_optical_flow.py
"""

import os

import numpy as np
import cv2

import video_analysis as va


W, H = 960, 540
GROUND_Y = 430


def _texture(w, h, seed=0):
    """Náhodná, ale ostrá textúra – optický tok potrebuje čo sledovať."""
    rng = np.random.default_rng(seed)
    small = rng.integers(60, 200, size=(h // 8 + 2, w // 8 + 2, 3), dtype=np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def make_scene(contact_s=0.110, step_s=0.230, duration_s=2.0, fps=240,
               speed_px_s=700.0, panning=False, path="/tmp/_flow_scene.mp4"):
    """
    Vykreslí video: textúrovaná zem + dve chodidlá, ktoré striedavo stoja
    (kontakt) a preletia dopredu (švih).  Vracia (cesta, fps, pravda, boxy).
    """
    n = int(duration_s * fps)
    stride_s = 2 * step_s
    world = _texture(W * 6, H, seed=1)           # dlhé pozadie, po ktorom sa "ide"
    foot_tex = {"L": _texture(70, 26, seed=2), "R": _texture(70, 26, seed=3)}

    events = {}
    for side, phase in (("L", 0.0), ("R", step_s)):
        ev, k = [], 0
        while phase + k * stride_s < duration_s + stride_s:
            ev.append((phase + k * stride_s, phase + k * stride_s + contact_s))
            k += 1
        events[side] = ev

    def foot_pos(side, t):
        """Vráti (x, y) chodidla vo svete a či je v kontakte."""
        for td, to in events[side]:
            if td <= t <= to:
                return speed_px_s * (td + 0.45 * contact_s), GROUND_Y, True
        prev = max([e for e in events[side] if e[1] < t], key=lambda e: e[1], default=None)
        nxt = min([e for e in events[side] if e[0] > t], key=lambda e: e[0], default=None)
        if prev is None:
            prev = (events[side][0][0] - stride_s, events[side][0][0] - stride_s + contact_s)
        if nxt is None:
            nxt = (events[side][-1][0] + stride_s, events[side][-1][0] + stride_s + contact_s)
        x0 = speed_px_s * (prev[0] + 0.45 * contact_s)
        x1 = speed_px_s * (nxt[0] + 0.45 * contact_s)
        f = np.clip((t - prev[1]) / max(nxt[0] - prev[1], 1e-6), 0, 1)
        x = x0 + (x1 - x0) * (f * f * (3 - 2 * f))
        y = GROUND_Y - 150.0 * np.sin(np.pi * f)
        return x, y, False

    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    truth, boxes = [], {"L": [], "R": []}
    body_px = 300.0                                   # "dĺžka trupu" pre normalizáciu

    for i in range(n):
        t = i / fps
        hip_world = speed_px_s * t
        cam = hip_world - W * 0.4 if panning else 0.0

        frame = world[:, int(cam) % (W * 5): int(cam) % (W * 5) + W].copy()
        cv2.line(frame, (0, GROUND_Y + 14), (W, GROUND_Y + 14), (40, 40, 40), 3)

        for side in ("L", "R"):
            fx, fy, on = foot_pos(side, t)
            sx, sy = int(fx - cam), int(fy)
            tex = foot_tex[side]
            th, tw = tex.shape[:2]
            x0, y0 = sx - tw // 2, sy - th // 2
            x1, y1 = x0 + tw, y0 + th
            # orež na obraz
            cx0, cy0 = max(0, x0), max(0, y0)
            cx1, cy1 = min(W, x1), min(H, y1)
            if cx1 > cx0 and cy1 > cy0:
                frame[cy0:cy1, cx0:cx1] = tex[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
            boxes[side].append((x0 - 8, y0 - 8, x1 + 8, y1 + 8))

        writer.write(frame)

    writer.release()
    for side in ("L", "R"):
        truth += [{"side": side, "td": td, "to": to}
                  for td, to in events[side] if to < duration_s]
    truth.sort(key=lambda d: d["td"])
    return path, fps, truth, boxes, body_px


def run_flow(path, fps, boxes, body_px):
    """Prejde video a spočíta rýchlosť chodidiel voči zemi (dĺžky trupu / s)."""
    cap = cv2.VideoCapture(path)
    tracker = va._FlowTracker()

    class FakeLm:
        """Nahrádza výstup MediaPipe – tokový modul potrebuje len polohy bodov."""
        def __init__(self, box_l, box_r):
            self.pts = [(0.5, 0.5, 1.0)] * 33
            for idx, box in ((va.L_HEEL, box_l), (va.L_TOE, box_l), (va.L_ANKLE, box_l),
                             (va.R_HEEL, box_r), (va.R_TOE, box_r), (va.R_ANKLE, box_r)):
                cx = (box[0] + box[2]) / 2 / W
                cy = (box[1] + box[3]) / 2 / H
                self.pts[idx] = (cx, cy, 1.0)

    i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        lm = FakeLm(boxes["L"][i], boxes["R"][i]).pts
        tracker.step(gray, lm, W, H)
        i += 1
    cap.release()

    out = {}
    for side in ("L", "R"):
        disp = np.array([r[side] for r in tracker.records], dtype=float)
        out[side] = disp * fps / body_px          # dĺžky trupu za sekundu
    out["camera"] = np.array([r["camera_px"] for r in tracker.records], dtype=float)
    return out


def evaluate(label, panning, fps=240, contact_s=0.110, speed_px_s=700.0):
    path, fps, truth, boxes, body_px = make_scene(
        fps=fps, contact_s=contact_s, panning=panning, speed_px_s=speed_px_s)
    speeds = run_flow(path, fps, boxes, body_px)

    # Pri statickej kamere bežec časom vybehne zo záberu – kontakty, ktoré
    # v obraze vôbec nie sú vidieť, sa do hodnotenia nezapočítavajú.
    def visible(g):
        mid = (g["td"] + g["to"]) / 2
        x_world = speed_px_s * (g["td"] + 0.45 * contact_s)
        cam = speed_px_s * mid - W * 0.4 if panning else 0.0
        return 40 < (x_world - cam) < W - 40
    truth = [g for g in truth if visible(g)]

    total_err, matched, found = [], 0, 0
    for side in ("L", "R"):
        ivs = va.flow_contacts(speeds[side], fps)
        found += len(ivs)
        gts = [g for g in truth if g["side"] == side]
        for iv in ivs:
            t0, t1 = iv[0] / fps, iv[1] / fps
            mid = (t0 + t1) / 2
            g = min(gts, key=lambda g: abs((g["td"] + g["to"]) / 2 - mid), default=None)
            if g and abs((g["td"] + g["to"]) / 2 - mid) < 0.08:
                matched += 1
                total_err.append(((t1 - t0) - (g["to"] - g["td"])) * 1000)

    cam = speeds["camera"]
    cam_med = float(np.nanmedian(cam)) if np.isfinite(cam).any() else float("nan")
    bias = float(np.mean(total_err)) if total_err else float("nan")
    print(f"  {label:<28} kontaktov {found}/{len(truth)}  spárovaných {matched}  "
          f"chyba dĺžky {bias:+6.1f} ms  posun kamery {cam_med:6.1f} px/snímok")
    try:
        os.remove(path)
    except OSError:
        pass
    return {"found": found, "truth": len(truth), "matched": matched, "bias": bias}


def main():
    print("\n=== Detekcia kontaktu optickým tokom (skutočné video, OpenCV) ===\n")
    a = evaluate("statická kamera", panning=False)
    b = evaluate("kamera uhýba za bežcom", panning=True)
    c = evaluate("statická, kontakt 85 ms", panning=False, contact_s=0.085)

    print("\n=== Kontrola podmienok ===")
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [OK]   " if cond else "  [CHYBA] ") + msg)
        ok = ok and cond

    check(a["matched"] >= a["truth"] - 1,
          f"statická kamera: nájde takmer všetky kontakty ({a['matched']}/{a['truth']})")
    check(abs(a["bias"]) < 25,
          f"statická kamera: dĺžka kontaktu do 25 ms (je {a['bias']:+.1f} ms)")
    check(b["matched"] >= b["truth"] - 2,
          f"pohyblivá kamera: kompenzácia funguje ({b['matched']}/{b['truth']})")
    check(abs(b["bias"]) < 35,
          f"pohyblivá kamera: dĺžka kontaktu do 35 ms (je {b['bias']:+.1f} ms)")
    check(abs(c["bias"]) < 25,
          f"kratší kontakt 85 ms sa tiež zmeria (chyba {c['bias']:+.1f} ms)")

    print("\n" + ("VŠETKY KONTROLY PREŠLI" if ok else "NIEKTORÉ KONTROLY ZLYHALI") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
