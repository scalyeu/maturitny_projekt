#!/bin/bash
# ============================================================
#  400m Sprint AI Predictor — spustenie na macOS
#  Dvojklikom v Finderi (alebo z Terminálu: ./run.command)
# ============================================================

cd "$(dirname "$0")" || exit 1

echo "=============================================="
echo " STARTUJEM APLIKACIU"
echo "=============================================="
echo

# ── Nájdi vhodný Python ──────────────────────────────────────
# mediapipe (analýza videa) má wheely len pre Python 3.9 - 3.12,
# preto hľadáme v tomto poradí a 3.13+ berieme len ako poslednú možnosť.
PY=""
for cand in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver=$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
        major=${ver%%.*}
        minor=${ver##*.}
        if [ "$major" = "3" ] && [ "$minor" -ge 9 ] && [ "$minor" -le 12 ]; then
            PY="$cand"
            break
        fi
        [ -z "$PY" ] && PY="$cand"   # záloha, keby nič vhodnejšie nebolo
    fi
done

if [ -z "$PY" ]; then
    echo "[CHYBA] Python 3 sa nenasiel."
    echo "Nainstaluj ho: brew install python@3.12"
    echo "alebo stiahni z https://www.python.org/downloads/"
    read -r -p "Stlac Enter pre ukoncenie..."
    exit 1
fi

PYVER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "[INFO] Pouzivam $PY (verzia $PYVER)"

case "$PYVER" in
    3.9|3.10|3.11|3.12) ;;
    *)
        echo
        echo "[POZOR] Python $PYVER je novsi nez 3.12."
        echo "        Kniznica mediapipe pre neho zatial nema wheel, takze"
        echo "        ANALYZA VIDEA nebude fungovat. Zvysok appky (Strava,"
        echo "        biometrika, AI chat) pobezi normalne."
        echo
        echo "        Ak chces aj analyzu videa:  brew install python@3.12"
        echo "        potom zmaz priecinok venv a spusti tento subor znova."
        echo
        ;;
esac

# ── Virtuálne prostredie ─────────────────────────────────────
if [ ! -d venv ]; then
    echo "[INFO] Vytvaram virtualne prostredie..."
    "$PY" -m venv venv || {
        echo "[CHYBA] Nepodarilo sa vytvorit virtualne prostredie."
        read -r -p "Stlac Enter pre ukoncenie..."
        exit 1
    }
fi

echo "[INFO] Aktivujem prostredie..."
# shellcheck disable=SC1091
source venv/bin/activate

python -m pip install --upgrade pip --quiet

# ── Inštalácia balíkov ───────────────────────────────────────
echo "[INFO] Instalujem moduly (prvykrat to trva aj par minut - mediapipe"
echo "       a opencv su velke balicky)..."
if ! pip install -r requirements.txt --quiet; then
    echo
    echo "[CHYBA] Problem pri instalacii kniznic."
    echo "        Ak zlyhal len mediapipe alebo opencv, zvysok appky sa da"
    echo "        spustit aj bez nich - nebude fungovat len stranka /video."
    read -r -p "Stlac Enter pre ukoncenie..."
    exit 1
fi

echo "[OK] Vsetko nainstalovane."

# ── Spustenie servera ────────────────────────────────────────
echo "[INFO] Spustam server na http://127.0.0.1:5001 ..."
echo "       Server bezi v tomto okne. Ukonci ho klavesovou skratkou Ctrl+C."
echo

sleep 2 && open "http://127.0.0.1:5001" &

python app.py
