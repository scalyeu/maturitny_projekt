# Zmeny „stopky zo záznamu“ (september 2026)

- Fotobunka beží aj nad videom zo záznamu: tlačidlo **Video zo záznamu** (súbor zo zariadenia) a pre
  prihláseného ponuka videí z jeho analýz (šprint aj prekážky, cez chránenú routu `/media/<súbor>`).
- Pri zázname sú hodinami stopiek časová stopa videa (`video.currentTime`, pri
  `requestVideoFrameCallback` `mediaTime` snímky): Štart označí aktuálny snímok ako čas 0, pretnutie
  čiary zastaví čas na snímku cieľa a záznam pozastaví; pauza ani spomalené prehrávanie čas nemenia.
- Prehrávanie: posuvník, krok o snímok (← →; dĺžka snímku sa meria z časovej stopy), Prehrať / Pauza
  (P), rýchlosť 0,25× / 0,5× / 1×. Štart na zvuk a otáčanie kamery sú pri zázname vypnuté; funguje aj
  bez https (kameru nepotrebuje).
- Nové spôsoby zastavenia `zaznam` / `zaznam-rucne` → poznámka „Stopky zo záznamu videa – zastavené
  fotobunkou (±33 ms)“; `<video>` už nemá `autoplay` (kamera sa púšťa z kódu ako doteraz).
- Test `test_stopky_zaznam.mjs`: syntetické video (pruh pretne čiaru v 3,406 s) v headless Google
  Chrome cez DevTools protokol – načítanie, krokovanie, Štart + fotobunka (2,40 s ± 1 snímok), pauza,
  0,25×, koniec záznamu, uvoľnenie pri zapnutí kamery.

# Zmeny „prekážky – kontrola vierohodnosti“ (september 2026)

Podnet: televízny zostrih finále 400 m prekážok (92 s, strihy, osem bežkýň, kostra na 45 % snímok)
dal rytmus 13-3-5-5-4-8-3 – trinástka bola náhoda cez 22-sekundový úsek s dierami v sledovaní,
ďalšie „prekážky“ boli obyčajné kroky s vypadnutým kontaktom. Rovnaká chyba (jeden vypadnutý
kontakt → falošná prekážka, 13 sa rozpadne na 8-3) hrozila aj na dobrom videu.

## `hurdle_analysis.py`
- Kandidát na prekážku (dlhý let) prejde kontrolou: odraz a dopad z rôznych nôh, dôvera kostry pri
  oboch kontaktoch ≥ 0,30, zdvih panvy 0,10 – 1,2 trupu. Zdvih sa meria voči výške panvy počas
  odrazu a dopadu (nie voči 0,8 s vyhladenej referencii, ktorá pohltila polovicu zdvihu): skutočná
  prekážka dá ~0,25 – 0,6, bežný krok s vypadnutým kontaktom ~0,03 – 0,15. Dve prekážky nemôžu byť
  bližšie než najrýchlejší medzičas disciplíny (400 m: 3,2 s, 110 m: 0,85 s, 100 m: 0,80 s, iné:
  0,5 s – prejde aj 1-krokový dril) – z bližšej dvojice ostane vierohodnejšia. Zamietnuté lety sú
  v `rejected` a v upozornení s dôvodmi. Keď pravidlo „príliš blízko“ zahodí viac prechodov, než
  ostane (110 m analyzované ako 400 m), analýza video odmietne s vysvetlením, že disciplína nesedí.
- Každý úsek medzi prekážkami má `valid` (medzičas a rýchlosť použiteľné: žiadna medzera nad 0,9 s,
  medzičas v rozsahu disciplíny 3,2 – 9,5 s / 0,85 – 2,5 s / 0,80 – 2,5 s, najviac 19 / 4 / 4 krokov,
  žiadny zamietnutý prechod so zdvihom panvy vnútri úseku – inak úsek spája dva medzičasy) a
  `steps_valid` (aj počet krokov: žiadny krok pod 130 ms ani 1,6× dlhší než medián úseku, žiadny
  zamietnutý dlhý let vnútri úseku, najmenej 11 / 3 / 3 krokov). Pre neúplný úsek je `steps_between`
  `None` (surová hodnota v `steps_between_raw`), v rytme je „?“; priemery, trend, rýchlosť a
  hodnotenie techniky berú len platné úseky.
- Video s kostrou na menej než 50 % snímok v úseku od prvého po posledný nájdený snímok
  (`video.detected_span_ratio`; prázdny rozbeh a dobeh sa nepočítajú) sa odmietne s návodom, aký
  záber natočiť.
- Nové pole `reliability` (`ok` / `low` / `unusable` + dôvody) a v súhrne `intervals_total`,
  `intervals_valid`, `intervals_steps_valid`, `rejected_candidates`.

## Stránka `/prekazky`, hodnotenie, AI tréner
- Pás nad výsledkom pri nespoľahlivom (volt) alebo nepoužiteľnom (červená linka) výsledku
  s dôvodmi a návodom na záber; dlaždice bez platných úsekov stlmené; v tabuľke a grafe „?“
  a sivý stĺpec pre neúplný úsek, dôvod po nabehnutí myšou.
- `technique_rules.hurdle_feedback` hodnotí len platné úseky; pri nepoužiteľnom zázname sa rytmus
  nehodnotí. Kontext pre AI trénera dostane varovanie a neúplné úseky označené.
- Staré uložené výsledky bez nových polí sa berú ako platné a vykreslia sa ako doteraz.

## Testy
- `test_hurdle_analysis.py`: vypadnutý kontakt uprostred 13-krokového úseku (bez kontrol by vznikla
  4. prekážka a rytmus 8-3; teraz 3 prekážky, rytmus `?-13`, spoľahlivosť `low`, zamietnutý let
  v upozornení); to isté na 110 m (`3-?-3-3`, nie „3-2-3-3“); prekážka bez dopadu / so zamenenými
  nohami na 400 m (zlúčený 7 s úsek je neplatný, nejde do priemeru); 3-krokový rytmus analyzovaný
  ako 400 m sa odmietne; 1-krokový dril pri „iné“ nájde 6/6 prekážok; brány dôvery kostry,
  nereálneho zdvihu a minimálneho odstupu (obe vetvy); odmietnutie videa s kostrou na 40 % snímok
  v úseku s bežcom.

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
