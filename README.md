# Sprint Predictor AI

Webová aplikácia pre šprintérov a prekážkarov, ktorá **meria bežeckú techniku z videa** — čas
kontaktu so zemou, frekvenciu a dĺžku krokov, asymetriu a uhly v kĺboch — a z nameraných hodnôt
**vyhodnotí, čo je na technike zlé a čo zlepšiť**. Pre prekážkarov stopuje medzičasy medzi
prekážkami a počet krokov medzi nimi. Okolo tohto jadra je postavené zázemie: biometrické dáta
(HRV, recovery, RHR), tréningový denník, Strava aktivity a AI asistent, ktorý všetko vidí naraz.

**Autor:** Tomáš Žigo · **Trieda:** III.C · **SPSE Bratislava**

---

## O projekte

**Jadro projektu je počítačové videnie.** Čas kontaktu so zemou je pri šprinte jeden z mála
priamo merateľných ukazovateľov techniky — no meria sa buď na tenzometrickej doske, alebo
laserovou bránou, čo si stredoškolský atlét nekúpi. Táto aplikácia to isté zmeria z videa
natočeného mobilom v spomalenom režime, s presnosťou v jednotkách milisekúnd.

Meranie stojí na **dvoch nezávislých metódach**, ktoré sa navzájom kontrolujú:

1. **Rozpoznávanie kostry** (MediaPipe Pose) — sleduje sa poloha chodidla voči panve.
2. **Optický tok** (OpenCV, Lucas-Kanade) — sleduje sa, kedy je chodidlo nehybné voči zemi,
   pričom sa najprv odpočíta pohyb kamery.

Keď sa obe metódy zhodnú, výsledok je dôveryhodný. Keď nie, aplikácia to povie namiesto toho,
aby predstierala presnosť, ktorú nemá. Rovnako sa správa pri nízkej snímkovej frekvencii.

Zvyšok aplikácie rieši druhý problém: šprintér zbiera dáta z viacerých zdrojov (WHOOP, Strava,
vlastný tréningový denník), ktoré žijú v oddelených aplikáciách. Sprint Predictor AI ich spája
na jednom mieste, pridáva readiness skóre na základe HRV trendu a AI asistenta, ktorý odpovedá
s plným kontextom posledných 60 dní — vrátane nameranej techniky.

Cieľová skupina je úzka: šprintéri na 100–800m, ktorí potrebujú detailnejšiu analytiku ako
poskytujú generické fitness aplikácie.

---

## Funkcionality

### Biometrika a regenerácia
- **Manuálny zápis HRV / recovery / RHR** cez webový formulár (`/log`).
- **Hromadný import WHOOP CSV** (`/import-whoop`) — automaticky deteguje hlavičku, podporuje viacero kódovaní (`utf-8-sig`, `utf-8`, `latin-1`, `cp1250`) a viacero názvov stĺpcov (slovenských, anglických, oficiálnych WHOOP exportov).
- **Historické grafy** s 7-dňovým kĺzavým priemerom, porovnaním aktuálneho obdobia s predošlým, a rozdelením dní na zelené/žlté/červené podľa recovery skóre.

### Readiness skóre
Vlastný algoritmus (`/api/readiness`) ktorý kombinuje:
- **HRV ratio** (60% váha) — aktuálne HRV vs. 30-dňový priemer, normalizované na rozsah 0.5–1.5
- **Recovery skóre z WHOOP** (40% váha)

Výsledok je rozdelený do 4 zón s konkrétnymi odporúčaniami:

| Skóre | Odporúčanie |
|-------|-------------|
| 80–100 | Trénuj naplno — ideálne na intenzívny tréning alebo preteky |
| 60–79 | Stredný tréning — tempové behy, stredné intervaly |
| 40–59 | Ľahký tréning — klus, strečing |
| 0–39 | Oddychuj — aktívny odpočinok |

### Strava integrácia
- **OAuth 2.0 prihlásenie** so správnym refresh token flow a uložením v DB.
- **Dashboard aktivít** (`/dashboard`) s posledných 10 behov, pace prepočtom (min/km pre beh, km/h pre bike, min/100m pre plávanie) a heart rate priemerom.
- **Osobné rekordy** (`/osobaky`) — automatické čítanie cez Strava `best_efforts` endpoint pre štandardné distance (400m, 800m, 1km, 1míľa, 2km, 5km, 10km, polmaratón, maratón), s lokálnym cache na disku.
- **Heatmapa behov** (`/heatmap`) — dekódovanie Strava polyline formátu, vykreslenie cez Leaflet.

### AI asistent
- **Chat rozhranie** (`/ai`) napojené na **Groq API** s fallback medzi dvomi modelmi: `deepseek-r1-distill-llama-70b` → `llama-3.3-70b-versatile`.
- **Kontextová injekcia** — pred každou otázkou sa do system promptu automaticky vloží:
  - Profil atléta (meno, vek, disciplína, cieľ)
  - Osobné rekordy zo Stravy
  - Sumár biometriky za posledných 60 dní (priemery, weekly trend, recovery distribúcia)
  - Posledných 7 dní záznam-po-zázname
  - Posledných 20 tréningov
- **Slovenský system prompt** — model je nastavený ako tréner šprintu s domain knowledge (HRV pre šprint, recovery zóny, 400m periodizácia, výživa).
- **História konverzácie** v DB (posledných 20 správ ide spolu s každým requestom).
- Automatické **odstránenie `<think>` blokov** z DeepSeek odpovedí.

### Tréningový denník
- Zápis tréningov s typom, distance, časom, intervalmi (textové pole) a poznámkami.
- Filter podľa obdobia.
- Použitý ako vstup pre AI kontext.

### Profil atléta
- Meno, vek, výška, hlavná disciplína, cieľ.
- Slúži ako kontext pre AI asistenta a na kalibráciu mierky pri analýze videa.

### Analýza techniky z videa (`/video`)
Nahrá sa video behu a aplikácia z neho vypočíta **čas kontaktu so zemou** a ďalšie
parametre techniky — samostatne pre ľavú a pravú nohu.

**Čo to meria**

| Veličina | Význam |
|----------|--------|
| Kontakt so zemou | Ako dlho je noha na zemi. Pri šprinte 0,09 – 0,13 s. |
| Letová fáza | Čas medzi odrazom jednej a dopadom druhej nohy. |
| Kadencia / frekvencia krokov | Počet krokov za minútu. |
| Duty factor | Podiel opory v celom cykle — nižší = šprintérskejší beh. |
| Asymetria Ľ/P | Rozdiel v čase kontaktu medzi nohami. |
| Zhoda metód | O koľko sa líši výsledok z kostry a z optického toku. |
| Rýchlosť, dĺžka kroku a dvojkroku | Po kalibrácii z výšky atléta. Dĺžka kroku = vzdialenosť medzi miestami dopadu. |
| Uhly v kĺboch | Koleno, bedro, holeň a náklon trupu pri dopade aj odraze. |
| Vertikálna oscilácia | Zdvih panvy hore-dole. |
| Hodnotenie techniky | Každá veličina sa porovná s referenčným pásmom: ok / sledovať / riešiť + tip do tréningu. |

**Ako to funguje**

1. **Kostra** — každý snímok prejde cez MediaPipe Pose (33 bodov tela).
2. **Normalizácia** — poloha chodidla sa počíta voči panve a delí sa dĺžkou trupu,
   takže meranie funguje aj pri zoome a pohyblivej kamere. Od výšky chodidla sa
   odčítava *vyhladená* výška panvy — samotné kmitanie panvy v rytme krokov by
   inak detekciu rozbilo.
3. **Optický tok (OpenCV)** — nezávisle od kostry sa Lucas-Kanadeho metódou
   (`cv2.calcOpticalFlowPyrLK`) sleduje pohyb bodov vnútri chodidla. Z bodov pozadia sa
   cez `cv2.estimateAffinePartial2D` (RANSAC) odhadne pohyb kamery a odpočíta sa — výsledkom
   je rýchlosť chodidla **voči zemi**, ktorá počas opory klesne takmer na nulu. Funguje to
   aj keď kamera uhýba za bežcom. Táto metóda nevie o kostre vôbec nič, takže jej zhoda
   s metódou kostry je poctivá kontrola, nie sebapotvrdenie.
4. **Detekcia krokov dvoma nezávislými metódami:**
   - *polohová* (Zeni et al., 2008) — dopad = maximum vzdialenosti päty pred panvou,
   - *vertikálna* — noha je v kontakte, kým je jej najnižší bod v pásme pri zemi.

   Prvá nájde udalosti hrubo a bez prahu, druhá ich spresní. Rozdiel medzi nimi
   sa vypisuje ako kontrola kvality merania.
5. **Sub-frame spresnenie** — presný okamih dopadu sa určí ako priesečník priamky
   klesania chodidla a priamky „na zemi". Vďaka tomu je presnosť lepšia než jeden
   snímok a bez systematického nadhodnotenia, ktoré má obyčajný prah.
6. **Kalibrácia** — z výšky atléta sa určí mierka (metre na pixel). Výška sa
   odhaduje zo štyroch segmentov tela nezávisle (noha, stehno, predkolenie, trup)
   a berie sa medián – jeden zle rozpoznaný bod kostry mierku nepokazí.
7. **Dĺžka kroku a rýchlosť** — dĺžka kroku sa meria **geometricky** ako
   vzdialenosť medzi miestami dopadu dvoch po sebe idúcich krokov (chodidlo
   počas opory stojí, jeho poloha sa spriemeruje cez strednú časť opory). Pri
   pohyblivej kamere sa polohy prevedú „do sveta" odčítaním posunu pozadia
   z optického toku. Rýchlosť = súčet dĺžok krokov / čas. Nezávisle sa počíta aj
   rýchlosť z posunu panvy voči stojnej nohe; keď sa obe metódy líšia o viac
   než 25 %, appka to povie. Každý krok má kontrolu: stojné chodidlo sa vo
   svete nesmie hýbať a dĺžka musí byť v rozumnom rozsahu (0,6–3,2 m), inak sa
   krok neuzná a použije sa označený náhradný odhad.
8. **Zoom na bežca** — keď je bežec v zábere malý (celá dráha v obraze, záber
   z tribúny), modelu kostry sa podáva výrez okolo bežca z plného rozlíšenia,
   ktorý sa plynulo posúva za ním. Body nôh sú tak výrazne presnejšie. Ak je v
   zábere viac ľudí, stačí kliknúť na svojho bežca v prvom snímku.
9. **Uhly priamo vo videu** — do prekryvného videa sa kreslia oblúky uhlov v
   kolenách, náklon trupu voči zvislici a uhol holene stojnej nohy; pri každom
   dopade a odraze bliká popisok. Z vybraných krokov sa uložia kľúčové snímky
   (dopad, odraz) s uhlami ako obrázky.
10. **Hodnotenie techniky** (`technique_rules.py`) — expertný systém: kontakt,
    pomer let/kontakt, frekvencia, dĺžka kroku k výške, uhol holene pri dopade
    (brzdenie), náklon trupu, koleno pri dopade a jeho prepadnutie v opore,
    odraz, asymetria, stabilita krokov a vertikálna oscilácia. Každé pravidlo
    má referenčné pásmo (Mann, Haugen a kol.), stav *ok / sledovať / riešiť*,
    vysvetlenie a konkrétny tip do tréningu. Fáza behu (max. rýchlosť vs.
    rozbeh) sa dá zvoliť alebo sa rozpozná z náklonu trupu.
11. **AI tréner** — tlačidlo pošle namerané čísla aj vyhodnotenie do Groq API
    a vráti súvislý komentár s cvičeniami (funguje len s `GROQ_API_KEY`;
    hodnotenie podľa pravidiel funguje aj bez neho).

**Prečo predtým vychádzala dĺžka kroku 30 cm**

Pôvodne sa dĺžka kroku počítala ako *rýchlosť × čas kroku*, pričom rýchlosť sa
brala z posunu panvy voči stojnej nohe v niekoľkých snímkoch opory. Keď
MediaPipe v tých pár snímkoch nakreslí chodidlo nepresne (malý bežec, rozmazané
chodidlá, prehodené strany), rýchlosť vyjde niekoľkonásobne menšia a dĺžka kroku
s ňou. Pri reálnych videách to dávalo 3–7× kratšie kroky. Nový výpočet meria
priamo vzdialenosť medzi dopadmi, nezávisí od fps ani od času kroku a má
kontroly, ktoré nezmyselnú hodnotu odhalia. Okrem toho sa rozmery snímky berú zo
skutočnej snímky, nie z hlavičky videa – pri telefóne otočenom na výšku OpenCV
snímku otočí, ale hlavička hlási pôvodné rozmery a všetky vzdialenosti by boli
prehodené.

**Presnosť — dôležité**

Kontakt so zemou pri šprinte trvá okolo 0,11 s. Pri 30 fps sú to 3 snímky, čo je
menej než rozdiel medzi dobrým a zlým krokom. **Natáčaj v spomalenom režime
(120 alebo 240 fps).** Aplikácia si snímkovú frekvenciu prečíta sama a ak je
nízka, výsledok viditeľne označí ako orientačný a vypíše neistotu merania.

Overenie **metódy kostry** na syntetických dátach so známym časom kontaktu
(`python test_video_analysis.py`):

| fps | 1 snímok | systematická chyba | RMS chyba |
|-----|----------|--------------------|-----------|
| 240 | 4,2 ms | −0,7 ms | 0,8 ms |
| 120 | 8,3 ms | −1,3 ms | 1,4 ms |
| 60 | 16,7 ms | −3,6 ms | 3,8 ms |
| 30 | 33,3 ms | −24,9 ms | 25,8 ms |

Overenie **optického toku** na vykreslenom videu s textúrami, kde tok naozaj beží
(`python test_optical_flow.py`, 240 fps, kontakt 110 ms):

| Scéna | Nájdené kontakty | Chyba dĺžky kontaktu |
|-------|------------------|----------------------|
| statická kamera | 5 / 5 | −7,5 ms |
| kamera uhýba za bežcom (3 px/snímok) | 9 / 9 | −6,8 ms |
| kratší kontakt 85 ms | 5 / 5 | −6,7 ms |

Overenie **dĺžky kroku a rýchlosti** na tej istej syntetickej kostre so známou
rýchlosťou (statická aj pohyblivá kamera, šum ±2 px): geometrická dĺžka kroku
sedí na 2 %, rýchlosť z geometrie a zo stojnej nohy sa zhodujú.

Overenie **prekážkovej analýzy** (`python test_hurdle_analysis.py`) na
syntetickom prekážkarovi s rytmom 13 krokov (400 m) a 3 krokov (110 m): nájdu
sa všetky prekážky, správny počet krokov medzi nimi, medzičas na ±20 ms a let
nad prekážkou na ±15 ms – pri 30 až 240 fps, so šumom aj s panorámovaním.

Tieto čísla platia pre samotné algoritmy. V reálnom videe sa k nim pripočíta
chyba rozpoznávania kostry, ktorá závisí od svetla, pozadia a ostrosti záberu —
a práve na to slúži porovnanie oboch metód priamo v aplikácii.

**Ako natáčať**
- spomalene (Slo-mo), 120 alebo 240 fps
- presne z boku, kamera v úrovni bokov, na statíve
- celé telo vrátane chodidiel v zábere po celý čas
- 6 – 8 krokov v rovnomernom tempe
- kontrastné pozadie a dosť svetla

### Prekážky (`/prekazky`)
Podstránka pre prekážkarov. Nahrá sa záber s aspoň dvoma prekážkami a appka
odstopuje to, čo tréneri bežne merajú ručne so stopkami – zámerne len veličiny,
ktoré sa dajú spoľahlivo odčítať z bežného videa:

| Veličina | Význam |
|----------|--------|
| Medzičas medzi prekážkami | Dopad za prekážkou N → dopad za prekážkou N+1 (touchdown-to-touchdown). |
| Kroky medzi prekážkami | Rytmus 3 / 13 / 14 / 15… a zmeny rytmu počas behu. |
| Rýchlosť medzi prekážkami | Vzdialenosť prekážok je daná pravidlami (35 / 9,14 / 8,50 m), takže rýchlosť vyjde bez kalibrácie. |
| Dopady za prekážkami | Čas dopadu za každou prekážkou a od dopadu za prvou (ako medzičasy zo stopiek). |
| Odrazová noha | Ľavá / pravá pri každej prekážke – či atlét strieda. |

**Spomalený záber netreba.** Medzičas medzi prekážkami trvá sekundy, takže chyba
±1 snímok je pri 30 fps pod 1 % – bežné video z telefónu alebo záznam z pretekov
stačí. Krátke veličiny (let nad prekážkou, kontakty v milisekundách) sa síce
počítajú a ukladajú do JSON-u, ale stránka ich nezobrazuje – pri bežnom videu
by boli len orientačné a pre rytmus medzi prekážkami nie sú potrebné.

**Ako to funguje:** prekážku netreba vo videu hľadať ako predmet – prezradí ju
samotný beh. Medzi dvoma kontaktmi je pri behu let ~0,12 s, pri prechode
prekážky 0,3–0,5 s. Každý let výrazne dlhší než bežný krok (viac než 1,7×
medián) je prechod prekážky; kontakt pred ním je odraz, kontakt po ňom dopad.
Zvyšok (medzičasy, počet krokov) je aritmetika nad zoznamom kontaktov.
Hodnotenie rytmu má vlastné pravidlá (počet krokov medzi prekážkami, trend a
stabilita medzičasov, najpomalší úsek, striedanie odrazovej nohy). Pri pretekoch
z tribúny sa svoj bežec vyberie kliknutím do prvého snímku.

---

## Tech stack

| Vrstva | Technológia |
|--------|-------------|
| Backend | Python 3.x, Flask 3.1.3 |
| ORM | Flask-SQLAlchemy 3.1.1 |
| Databáza | SQLite |
| Strava | stravalib 2.4, OAuth 2.0, raw `requests` pre `best_efforts` |
| AI | Groq API (DeepSeek R1 70B, LLaMA 3.3 70B fallback) |
| Konfigurácia | python-dotenv |
| Frontend | Tailwind CSS (CDN), Lexend + Inter (Google Fonts), Material Symbols |
| Grafy | Chart.js |
| Mapy | Leaflet.js + polyline decoder |

---

## Štruktúra projektu

```
Zigo_sportovy_prediktor/
├── app.py                  # Hlavná aplikácia — modely, routy, helpery
├── video_analysis.py       # Analýza behu z videa — kostra, kontakt so zemou, dĺžka kroku, uhly, video
├── hurdle_analysis.py      # Prekážky — medzičasy, kroky medzi prekážkami, let, odrazová noha
├── technique_rules.py      # Hodnotenie techniky (pravidlá + referenčné pásma) pre šprint a prekážky
├── test_video_analysis.py  # Testy detektora a dĺžky kroku na syntetických dátach so známou pravdou
├── test_hurdle_analysis.py # Testy prekážkovej analýzy na syntetickom prekážkarovi
├── test_optical_flow.py    # Testy optického toku na vykreslenom videu s textúrami
├── requirements.txt        # Python závislosti
├── run.bat                 # Windows launcher
├── run.command             # macOS launcher (dvojklik v Finderi)
├── README.md
├── .env                    # API kľúče (negitované)
├── database.db             # SQLite (auto-generovaná)
├── cache/                  # Cache osobných rekordov zo Stravy
├── models/                 # Model kostry pre MediaPipe (sťahuje sa sám, negitovaný)
├── static/
│   ├── css/                # Vlastné CSS poverené Tailwindom
│   ├── js/                 # Klientske skripty
│   └── uploads/videos/     # Nahrané videá, videá s kostrou a kľúčové snímky (negitované)
└── templates/
    ├── base.html           # Layout, navigácia, Tailwind config
    ├── index.html          # Domovská stránka — weekly summary
    ├── log.html            # Formulár pre HRV zápis + WHOOP import
    ├── trainings.html      # Tréningový denník
    ├── charts.html         # HRV / recovery trendy
    ├── biometrics.html     # Pokročilý biometric dashboard
    ├── dashboard.html      # Strava aktivity
    ├── osobaky.html        # Osobné rekordy
    ├── heatmap.html        # Leaflet heatmapa
    ├── ai.html             # AI chat
    ├── video.html          # Analýza techniky z videa + hodnotenie techniky
    ├── prekazky.html       # Prekážky — medzičasy, rytmus, prechody
    └── profile.html        # Profil atléta
```

---

## Účty a úrovne prístupu

Aplikácia má štyri úrovne prístupu (`auth.py`, `models.py`):

| Rola | Čo vidí |
|---|---|
| neprihlásený | úvodná stránka, Stopky (bez ukladania) |
| **zverenec** | len svoje údaje: video, prekážky, tréningy, plán od trénera, výsledky, grafy, AI, Strava |
| **tréner** | to isté pre seba + údaje svojich zverencov (`user.coach_id`), správa výsledkov a plánu |
| **administrátor** | všetko + správa používateľov (`/admin/pouzivatelia`) |

- Pri prvom spustení sa vytvorí účet **`admin` / `admin`** (`must_change_password=True`) —
  appka ho po prihlásení pustí len na zmenu hesla. Nápoveda na prihlasovacej stránke zmizne
  po zmene hesla.
- Registrácia (`/registracia`) ponúka roly zverenec a tréner; admina vie vytvoriť len admin.
- Heslá sú hašované (`werkzeug.security`). Session podpisuje `SECRET_KEY` z `.env`; ak chýba,
  appka si ho raz vygeneruje do `instance/secret_key`.
- Každý dotaz na údaje je filtrovaný cez `user_id` (`auth.athlete_ids_visible_to`). Staré riadky
  bez `user_id` (z verzie pred účtami) vidí len admin.

Premenné prostredia pre spúšťanie: `ATLETCOACH_PORT` (5001), `ATLETCOACH_RELOAD=0` vypne reloader,
`FLASK_DEBUG=0` vypne debug (pri nasadení povinné), `ATLETCOACH_DB_URI` prepíše cestu k databáze.

---

## Databázové modely

```python
User            # username, password_hash, role (zverenec|trener|admin), full_name,
                # coach_id -> user.id, world_athletics_id, must_change_password
RaceResult      # user_id, discipline, result_s, date, competition, place, wind,
                # source (manual|world_athletics|stopky), note, created_by
PlannedTraining # athlete_id, coach_id, date, title, description, completed,
                # completed_at, athlete_note, training_log_id
# Všetky pôvodné modely majú navyše user_id -> user.id (vlastník riadku):
BiometricLog    # date, hrv, recovery, rhr
TrainingLog     # date, training_type, distance_km, duration_min, duration_sec,
                # intervals_data, notes
StravaToken    # athlete_id, access_token, refresh_token, expires_at
ChatMessage    # role, content, created_at
UserProfile    # name, age, height_cm, sport, goal
VideoAnalysis  # created_at, label, stored_name, overlay_name, fps, duration_s,
               # steps_detected, contact_ms_mean, contact_ms_sd, flight_ms_mean,
               # cadence_spm, duty_factor_pct, asymmetry_pct, speed_ms,
               # stride_length_m, result_json (celý výsledok vrátane krokov, uhlov,
               # hodnotenia techniky, kľúčových snímok a komentára AI trénera)
HurdleAnalysis # created_at, label, discipline, stored_name, overlay_name, fps,
               # hurdles_detected, interval_mean_s, interval_sd_s, steps_pattern,
               # flight_ms_mean, landing_contact_ms_mean, speed_between_ms_mean,
               # result_json
```

Vytvárajú sa automaticky pri prvom spustení (`db.create_all()`).
Chýbajúce stĺpce v už existujúcej databáze doplní `_ensure_columns()`.

---

## Inštalácia

### 1. Klon repozitára
```bash
git clone https://github.com/SPSE-2526-III-C/Zigo_sportovy_prediktor.git
cd Zigo_sportovy_prediktor
```

### 2. Virtuálne prostredie (odporúčané)

> **Dôležité — verzia Pythonu.** Knižnica `mediapipe`, ktorú používa analýza
> videa, má oficiálne wheely len pre **Python 3.9 – 3.12**. Na Pythone 3.13+
> sa nenainštaluje a stránka `/video` to oznámi; zvyšok aplikácie funguje ďalej.
> Verziu overíš cez `python3 --version`.
>
> Ak máš 3.13+, na macOS: `brew install python@3.12` a potom
> `python3.12 -m venv venv`.

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 3. Inštalácia závislostí
```bash
pip install -r requirements.txt
```

Prvá inštalácia trvá aj pár minút — `mediapipe` a `opencv-python` sú veľké balíky.

### 4. Vytvorenie `.env` súboru
V koreni projektu vytvor súbor `.env`:

```env
# Flask – podpisuje prihlasovacie cookie; ak chýba, vygeneruje sa do instance/secret_key
SECRET_KEY=tvoj-tajny-kluc-zmen-ma

# Strava OAuth — registruj appku na https://www.strava.com/settings/api
# (appka beží na porte 5001, rovnaký port nastav aj v Strave ako callback)
STRAVA_CLIENT_ID=tvoje_client_id
STRAVA_CLIENT_SECRET=tvoj_client_secret
STRAVA_REDIRECT_URI=http://localhost:5001/strava/callback

# Groq API — získaj na https://console.groq.com/keys
GROQ_API_KEY=gsk_tvoj_groq_kluc
```

### 5. Spustenie
```bash
python app.py
```

Testy (bez videa, na syntetických dátach so známou pravdou):
```bash
python test_video_analysis.py     # kontakt so zemou, mierka, dĺžka kroku, celý reťazec
python test_hurdle_analysis.py    # prekážky: detekcia, kroky medzi prekážkami, medzičasy
python test_optical_flow.py       # optický tok na vykreslenom videu
```

Aplikácia sa automaticky otvorí v prehliadači na `http://127.0.0.1:5001`.

**Jednoduchšie — hotové spúšťače**, ktoré si samé vytvoria `venv`, doinštalujú
balíky a otvoria prehliadač:

| Systém | Súbor | Ako |
|--------|-------|-----|
| macOS | `run.command` | dvojklik v Finderi |
| Windows | `run.bat` | dvojklik |

Ak macOS pri prvom spustení `run.command` odmietne otvoriť súbor, povoľ ho
v *Nastavenia → Súkromie a bezpečnosť*, alebo raz spusti v Termináli:

```bash
chmod +x run.command
./run.command
```

### 6. Natáčanie videí na iPhone

Analýza videa potrebuje **spomalený záber (Slo-mo, 120 alebo 240 fps)** — dôvod
je vysvetlený v sekcii o presnosti vyššie. Pri prenose do počítača na tom záleží:

- **AirDrop originálu z Fotiek** zachová pôvodných 240 fps a appka si ich načíta
  sama. Toto je odporúčaný postup.
- **Export cez „Uložiť ako video"** alebo cez niektoré cloudové služby môže záber
  prerenderovať na 30 fps *už spomaleného* obrazu. Časy potom vyjdú približne
  8× dlhšie, než v skutočnosti sú.

Ak si nie si istý, čo appka načítala, skontroluj údaj **fps** v paneli presnosti
nad výsledkami. Ak nesedí, správnu hodnotu sa dá prepísať v poli *Skutočné fps*.

---

## API endpointy

### Web routy
| Metóda | URL | Popis |
|--------|-----|-------|
| GET | `/` | Domovská stránka s týždenným prehľadom |
| GET, POST | `/log` | Zápis HRV / recovery / RHR |
| POST | `/import-whoop` | Hromadný import WHOOP CSV |
| GET, POST | `/trainings` | Tréningový denník |
| GET | `/charts` | Trendové grafy |
| GET | `/biometrics` | Biometric dashboard |
| GET | `/ai` | AI chat portál |
| GET, POST | `/profile` | Profil atléta |
| GET | `/video` | Analýza techniky z videa |
| GET | `/prekazky` | Prekážky — medzičasy a rytmus |
| GET | `/dashboard` | Strava aktivity (vyžaduje login) |
| GET | `/osobaky` | Osobné rekordy (vyžaduje login) |
| GET | `/heatmap` | Leaflet heatmapa (vyžaduje login) |
| GET | `/strava/login` | Štart OAuth flow |
| GET | `/strava/callback` | OAuth callback |
| GET | `/logout` | Odhlásenie + revoke Strava tokenu |

### JSON API
| Metóda | URL | Popis |
|--------|-----|-------|
| GET | `/api/readiness` | Readiness skóre na dnes |
| GET | `/api/biometric-history?days=30` | Historické dáta + štatistiky |
| GET | `/api/heatmap-data` | Polyline body pre heatmapu |
| POST | `/api/chat` | Pošli správu AI |
| GET | `/api/chat/history` | História konverzácie |
| POST | `/api/chat/clear` | Vymaž históriu |
| GET | `/api/debug-prs` | Debug Strava best_efforts |
| GET | `/profile/api` | Profil ako JSON |
| POST | `/api/video/analyze` | Nahranie videa, spustí analýzu na pozadí |
| GET | `/api/video/job/<job_id>` | Stav prebiehajúcej analýzy (progres v %) |
| GET | `/api/video/result/<id>` | Uložený výsledok analýzy |
| POST | `/api/video/delete/<id>` | Zmazanie merania aj videosúborov |
| POST | `/api/hurdles/analyze` | Nahranie prekážkového videa (form: `discipline`, `fps_override`, `click_x/click_y` = bežec v prvom snímku) |
| GET | `/api/hurdles/result/<id>` | Uložený výsledok prekážkovej analýzy |
| POST | `/api/hurdles/delete/<id>` | Zmazanie prekážkového merania |
| POST | `/api/coach/<sprint\|hurdles>/<id>` | Komentár AI trénera k meraniu (Groq) |

---

## Poznámky k implementácii

- **Session sa neuchováva permanentne** — po zatvorení prehliadača sa Strava login zruší. Tokeny ostávajú v DB a obnovia sa pri ďalšom prihlásení toho istého atléta.
- **Strava PR caching** — výsledky `best_efforts` sa cachujú do `cache/prs_*.json` pretože Strava API má rate limit (100 requestov / 15 min). Refresh sa dá vynútiť cez `?refresh=1`.
- **WHOOP import je tolerantný k formátu** — toleruje rôzne kódovania, hľadá hlavičku CSV aj keď nie je na prvom riadku, mapuje viacero variant názvov stĺpcov.
- **Polyline decoder** je vlastná implementácia Google Encoded Polyline algoritmu bez závislosti na externej knižnici.
- **AI fallback model** — keď DeepSeek R1 zlyhá (timeout, 5xx), automaticky sa skúsi LLaMA 3.3.
- **Analýza videa beží na pozadí** vo vlastnom vlákne, stránka sa na stav pýta každých 0,7 s.
  Rozpoznávanie kostry pri spomalenom videu trvá desiatky sekúnd.
- **Dve verzie MediaPipe** — staršie API `mp.solutions` aj novšie Tasks API. Modul si
  vyberie, čo je nainštalované; pre novšie si pri prvom spustení stiahne model do `models/`.
- **Výsledky videa idú do AI kontextu** — posledné tri merania šprintu aj prekážok vrátane
  vyhodnotenia techniky vidí aj chat asistent.
- **Hodnotenie techniky je deterministické** — beží lokálne bez internetu; AI tréner (Groq) je
  nadstavba, ktorá z rovnakých čísel napíše súvislý komentár.
- **Prehliadač vykreslí prvý snímok videa** do plátna (`<canvas>`), takže výber bežca kliknutím
  nepotrebuje žiadny server-side endpoint.
- **Migrácia stĺpcov** — `_ensure_columns()` pri štarte doplní nové stĺpce do existujúcej
  `database.db`, takže staršia databáza zostane funkčná.

---

## Známe obmedzenia

- Nahrané videá, prekryvné videá a kľúčové snímky sa servírujú zo `static/uploads/videos`
  bez kontroly prihlásenia. Názvy súborov sú náhodné (UUID), takže sa nedajú uhádnuť, ale kto
  má odkaz, video si otvorí. Pri nasadení mimo lokálnej siete to treba riešiť (napr. presun
  mimo `static/` a route s kontrolou oprávnení).
- Tréner pridáva zverenca podľa používateľského mena bez potvrdenia zo strany zverenca.
- Strava token sa ukladá v DB v plaintexte (na lokálnom použití OK, pre produkciu by mal byť šifrovaný).
- AI predikčný model (klasické ML na predpoveď pretekového času) je v plánoch — aktuálne ho nahrádza Groq chat asistent.
- Analýza videa počíta uhly v rovine obrazu (2D). Platia len ak je kamera naozaj z boku;
  pri šikmom zábere sú skreslené. Časy kontaktu sú na uhol kamery oveľa menej citlivé.
- Presnosť merania je zdola ohraničená snímkovou frekvenciou videa — pri 30 fps je
  výsledok len orientačný.
- Analýza beží v pamäti procesu: po reštarte servera sa rozpracovaná úloha stratí
  (uložené výsledky v databáze zostávajú).
- MediaPipe sleduje jedného človeka. Pri pretekoch treba svojho bežca vybrať kliknutím;
  keď sa bežci prekrývajú, kostra môže na chvíľu preskočiť na iného.
- Prekážka sa rozpoznáva z dĺžky letu, nie z obrazu – pri veľmi nízkych tréningových
  prekážkach (let kratší než ~0,22 s) ju appka neodlíši od bežného kroku.
- Prekážková stránka ukazuje len medzičasy, kroky a odrazovú nohu; let nad prekážkou a
  kontakty (milisekundy) sú v uloženom JSON-e, ale bez spomaleného videa sú orientačné.
- Referenčné pásma v hodnotení techniky sú orientačné (zdroje: Mann – The Mechanics of
  Sprinting and Hurdling; Haugen a kol. 2019), nie individuálna norma.

---

## Ďalší vývoj

- Klasický ML model na predikciu pretekového času na základe HRV trendu, objemu tréningu a histórie výkonov.
- Detekcia drop-off vzoru pri intervaloch (napr. 3×300m — porovnanie 1. a 3. úseku).
- Export tréningov do CSV/PDF.
- Push notifikácie pri varovaní (HRV pokles > 15 %, RHR rast > 5 bpm).
- Porovnanie dvoch videí vedľa seba (napr. začiatok a koniec sezóny).
- Ručná korekcia rozpoznaných prekážok (pridať / zrušiť prechod) priamo v tabuľke.
- Odhad vzdialenosti odrazu a dopadu od prekážky (potrebuje rozpoznať prekážku v obraze).
- Prepojenie času kontaktu so zemou s výsledkami z pretekov a s HRV trendom.

---

*Sprint Predictor AI — školský projekt, SPSE Bratislava, 2025/2026.*
