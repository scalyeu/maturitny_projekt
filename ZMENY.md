# Zmeny vo verzii „video 2“

## Oprava dĺžky kroku (30 cm)
- Dĺžka kroku sa už nepočíta ako *rýchlosť × čas*, ale **geometricky** – ako vzdialenosť
  medzi miestami dopadu dvoch po sebe idúcich krokov (`estimate_step_geometry`).
  Pri pohyblivej kamere sa polohy prevedú do „sveta“ odčítaním posunu pozadia
  z optického toku (`camera_track`). Každý krok má kontroly (stojné chodidlo sa vo svete
  nesmie hýbať, dĺžka 0,6–3,2 m, poradie chodidiel, výpadok sledovania pozadia).
- Rýchlosť = súčet dĺžok krokov / čas; pôvodný odhad zo stojnej nohy ostal ako kontrola
  a pri nezhode nad 25 % sa zobrazí upozornenie.
- Mierka (metre na pixel) sa odhaduje zo štyroch segmentov tela (medián), nie len z nohy.
- **Otočené video** (telefón na výšku): OpenCV 4.10 značku otočenia ignorovalo a bežec bol
  „položený“ – teraz sa zapína `CAP_PROP_ORIENTATION_AUTO` a rozmery sa berú zo skutočnej
  snímky (`open_capture`).
- Prekrývajúce sa kontakty ľavej a pravej nohy (zámena strán v MediaPipe) sa zlučujú
  (`dedupe_contacts`).
- Nezmyselná dĺžka kroku alebo rýchlosť sa nahlási v upozorneniach namiesto tichého zobrazenia.

## Presnejšia kostra
- **Zoom na bežca** (`_RoiTracker`): keď je bežec v zábere malý, model dostáva výrez
  z plného rozlíšenia, ktorý sa plynulo posúva za ním.
- **Výber bežca kliknutím** do prvého snímku (pri viacerých ľuďoch v zábere) – prvý snímok
  vykreslí prehliadač do `<canvas>`, súradnice idú ako `click_x`/`click_y`.
- Podpora `visibility=None` v novom Tasks API MediaPipe, prísne rastúce časové značky.

## Uhly vo videu a kľúčové snímky
- Do prekryvného videa sa kreslia oblúky uhlov v kolenách, náklon trupu voči zvislici,
  uhol holene stojnej nohy a popisky DOPAD / ODRAZ.
- Kľúčové snímky (dopad a odraz vybraných krokov, pri prekážkach odraz / nad prekážkou /
  dopad) sa ukladajú ako JPEG s uhlami a zobrazujú na stránke.
- Opravené znamienko uhla stehna (`thigh_deg`: + = koleno pred bedrom, − = za bedrom).

## Hodnotenie techniky (`technique_rules.py`)
- 13 pravidiel pre šprint (kontakt, let/kontakt, frekvencia, dĺžka kroku k výške, holeň pri
  dopade, trup, koleno pri dopade a jeho prepadnutie, odraz, stehno, asymetria, stabilita,
  vertikálna oscilácia) s referenčnými pásmami, stavom *ok / sledovať / riešiť*, vysvetlením
  a tipom do tréningu. Fáza behu (max. rýchlosť / rozbeh) sa dá zvoliť alebo sa rozpozná.
- 4 pravidlá pre prekážky (počet krokov medzi prekážkami, trend a stabilita medzičasov,
  najpomalší úsek, striedanie odrazovej nohy).
- **AI tréner**: `POST /api/coach/<sprint|hurdles>/<id>` pošle čísla + vyhodnotenie do Groq a
  vráti komentár s cvičeniami (uloží sa k meraniu).

## Prekážky (`/prekazky`, `hurdle_analysis.py`)
- Prekážka sa rozpozná z letu výrazne dlhšieho než bežný krok; kontakt pred ním je odraz,
  po ňom dopad.
- Stránka ukazuje len to, čo je spoľahlivé aj z bežného videa: medzičasy dopad → dopad, počet
  krokov medzi prekážkami, rýchlosť medzi prekážkami z pravidlami danej vzdialenosti
  (400 m: 35 m, 110 m: 9,14 m, 100 m: 8,50 m), časy dopadov od prvej prekážky a odrazovú nohu.
  Spomalený záber netreba (±1 snímok pri 30 fps = chyba pod 1 %).
- Milisekundové veličiny (let nad prekážkou, kontakty) sa počítajú a ukladajú do JSON-u, ale
  stránka ich nezobrazuje a hodnotenie ich nepoužíva.
- Hodnotenie rytmu: počet krokov medzi prekážkami, trend a stabilita medzičasov, najpomalší
  úsek, striedanie odrazovej nohy (400 m).
- Nová tabuľka `HurdleAnalysis`, história meraní, graf medzičasov, video s označením prekážok,
  snímky odraz / nad prekážkou / dopad na kontrolu detekcie.
- Výsledky prekážok vidí aj AI chat a AI tréner v kontexte.

## Testy
- `test_video_analysis.py`: nové testy dĺžky kroku a rýchlosti (statická kamera, panning,
  šum, panning bez optického toku sa správne neuzná) a test otočeného videa.
- `test_hurdle_analysis.py`: syntetický prekážkar s rytmom 13 (400 m) a 3 (110 m) krokov pri
  30–240 fps, so šumom a panorámovaním + celý reťazec `analyze_hurdle_video()`.
- Všetky tri testovacie sady prechádzajú aj s verziami knižníc z `requirements.txt`
  (mediapipe 0.10.21, opencv 4.10, numpy 1.26).
