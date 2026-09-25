# AtletCoach

Webová aplikácia pre trénera a jeho bežcov: analyzuje bežeckú techniku z videa, stopuje
prekážkové medzičasy, meria časy kamerou s virtuálnou fotobunkou a drží na jednom mieste
zverencov, ich výsledky, osobné rekordy, plán tréningov a grafy výkonnosti.

**Autor:** Tomáš Žigo · **Trieda:** III.C · **SPŠE Bratislava** · maturitný projekt 2025/2026

## Zadanie

> Cieľom projektu je navrhnúť, naprogramovať a nasadiť webovú aplikáciu, ktorá pomôže vyhodnocovať a sledovať výkony
> atlétov, špeciálne bežcov. Aplikácia pracuje so štyrmi úrovňami prístupu, neprihláseným používateľom, prihláseným
> zverencom (atlétom), prihláseným trénerom a prihláseným administrátorom. Tréner v nej spravuje zverencov, ich výsledky
> z pretekov a osobné rekordy, ktoré sa dajú automaticky načítať aj zo stránky World Athletics. V kalendári plánuje
> tréningy a sleduje, ako ich zverenci splnili, vývoj výkonnosti zobrazujú grafy. Zverenec má vlastné prihlásenie,
> zapisuje si tréningy a vidí len svoje údaje. Jadrom aplikácie je analýza behu z videa, v ktorom softvér rozpozná
> postavu bežca, sleduje pohyb chodidiel voči zemi a vyhodnotí parametre techniky. Pri behu cez prekážky odmeria
> medzičasy a počet krokov medzi prekážkami. Súčasťou sú aj stopky s kamerou a virtuálnou fotobunkou, ktorá zastaví
> čas keď bežec pretne čiaru v obraze.

---

## Obsah

1. [Čo appka robí](#1-čo-appka-robí)
2. [Ako to funguje technicky](#2-ako-to-funguje-technicky)
3. [Spustenie](#3-spustenie)
4. [Nasadenie](#4-nasadenie)
5. [Bezpečnosť a známe obmedzenia](#5-bezpečnosť-a-známe-obmedzenia)
6. [Testy](#6-testy)
7. [Technické parametre](#7-technické-parametre)

---

## 1. Čo appka robí

### Úroveň prístupu × čo vidí

| | neprihlásený | zverenec | tréner | administrátor |
|---|---|---|---|---|
| Domov `/` | úvodná stránka | osobný prehľad (týždeň, HRV, posledné video) | prehľad zverencov: počet, plán na tento týždeň, % splnenia, posledné výsledky | ako tréner, navyše odkaz na Používateľov |
| Stopky `/stopky` | áno, bez ukladania | áno, čas si uloží medzi výsledky | áno, uloží čas vybranému zverencovi | áno, komukoľvek |
| Video `/video`, Prekážky `/prekazky` | presmerovanie na prihlásenie | vlastné analýzy | vlastné + analýzy potvrdených zverencov (pri nahrávaní vyberie, komu meranie patrí) | všetky |
| Tréningy, Logovanie, AI, Profil, Strava | – | vlastné údaje | vlastné údaje | vlastné údaje |
| Plán `/plan` | – | vlastný plán, označuje splnenie | kalendár každého zverenca, plánuje / upravuje / maže, prehľad splnenia | ako tréner, všetci zverenci |
| Výsledky a osobné rekordy | – | `/zverenec/vysledky`: svoje výsledky a PB, rieši vzťah s trénerom | `/trener/zverenci/<id>`: výsledky zverenca, PB, import z World Athletics | ako tréner |
| Zverenci `/trener/zverenci` | – | 403 | vlastní zverenci a čakajúce žiadosti | všetci zverenci zoskupení podľa trénera |
| Grafy `/grafy` | – | svoje grafy | výber zverenca → jeho grafy | ktorýkoľvek zverenec |
| Používatelia `/admin/pouzivatelia` | – | 403 | 403 | správa účtov, prehľad systému `/admin/prehlad` |

Kľúčové pravidlo: **zverenec vidí len svoje údaje** a **tréner vidí údaje zverenca až po tom, čo ho zverenec
potvrdí** (alebo mu tréner účet sám vytvoril). Administrátor vidí všetko.

### Analýza techniky z videa (`/video`)

Nahráš video behu (ideálne spomalené, 120 – 240 fps, z boku) a aplikácia z neho **automaticky** rozpozná postavu
bežca, sleduje chodidlá voči zemi a pre každý krok vypočíta:

| Veličina | Význam |
|----------|--------|
| Kontakt so zemou | Ako dlho je noha na zemi (pri šprinte 0,09 – 0,13 s), zvlášť ľavá a pravá. |
| Letová fáza | Čas medzi odrazom jednej a dopadom druhej nohy. |
| Kadencia | Počet krokov za minútu. |
| Duty factor | Podiel opory v celom cykle – nižší = šprintérskejší beh. |
| Asymetria Ľ/P | Rozdiel v čase kontaktu medzi nohami. |
| Rýchlosť, dĺžka kroku | Po kalibrácii z výšky atléta (z profilu alebo z formulára). |
| Uhly v kĺboch | Koleno, bedro, holeň a náklon trupu pri dopade a odraze, kreslené priamo do videa. |
| Vertikálna oscilácia | Zdvih panvy hore-dole. |
| Hodnotenie techniky | Každá veličina sa porovná s referenčným pásmom: *ok / sledovať / riešiť* + tip do tréningu. |

Výstupom je prekryvné video s kostrou a uhlami, kľúčové snímky dopadu a odrazu, tabuľka krokov a hodnotenie
techniky. Hodnotenie je deterministické (pravidlá v `technique_rules.py`, referenčné pásma podľa Manna a Haugena
a kol.) a funguje bez internetu. Tlačidlo **AI tréner** navyše pošle namerané čísla do Groq API a vráti súvislý
komentár s cvičeniami – to funguje len s nastaveným `GROQ_API_KEY`.

Analýza beží na pozadí a pri spomalenom videu trvá desiatky sekúnd. Ak je vo videu viac ľudí, svojho bežca vyberieš
kliknutím do prvého snímku. Pri nízkej snímkovej frekvencii aplikácia výsledok označí ako orientačný.

### Prekážky (`/prekazky`)

Nahráš jeden nestrihaný záber z boku na jedného bežca s aspoň dvoma prekážkami (stačí bežné video 30 fps, aj
z tribúny) a aplikácia **automaticky** odmeria to, čo tréner bežne stopuje ručne:

| Veličina | Význam |
|----------|--------|
| Medzičas medzi prekážkami | Dopad za prekážkou N → dopad za prekážkou N+1. |
| Kroky medzi prekážkami | Rytmus 3 / 13 / 14 / 15… a jeho zmeny počas behu. |
| Rýchlosť medzi prekážkami | Vzdialenosť je daná pravidlami (35 / 9,14 / 8,50 m), takže netreba kalibráciu. |
| Časy dopadov | Za každou prekážkou, od dopadu za prvou – ako medzičasy zo stopiek. |
| Odrazová noha | Ľavá / pravá pri každej prekážke. |

Hodnotenie rytmu má vlastné pravidlá (počet krokov, trend a stabilita medzičasov, najpomalší úsek, striedanie
odrazovej nohy pri 400 m prekážkach). Let nad prekážkou a kontakty v milisekundách sa počítajú a ukladajú, ale
stránka ich nezobrazuje – bez spomaleného videa sú len orientačné.

Každý nájdený prechod prekážky a každý úsek medzi prekážkami prejde **kontrolou vierohodnosti**: odraz a dopad
musia byť z rôznych nôh, panva sa počas letu musí zdvihnúť, kostra musí byť pri oboch kontaktoch viditeľná, dve
prekážky nemôžu byť bližšie než najrýchlejší možný medzičas disciplíny a v úseku nesmie byť diera v sledovaní,
zamietnutý dlhý let, priveľa či primálo krokov na disciplínu ani krok 1,6× dlhší než medián úseku (vypadnutý
kontakt). Úsek s neistým počtom krokov dostane namiesto počtu „?“ a do hodnotenia rytmu sa nezapočíta (medzičas
ostáva, ak je sám vierohodný); úsek s nevierohodným medzičasom vypadne aj z priemerov a rýchlosti. Stránka to
ohlási pásom nad výsledkom. Video, kde je kostra na menej než polovici snímok v úseku s bežcom (strihaný
televízny záznam, viac ľudí v zábere), analýza odmietne s vysvetlením, aký záber natočiť; rovnako video, na ktoré
nesedí zvolená disciplína (prekážky bližšie, než dovoľuje jej najrýchlejší medzičas).

### Stopky s fotobunkou (`/stopky`)

Stopky s kamerou a virtuálnou fotobunkou, ktorá zastaví čas, keď bežec pretne čiaru v obraze. Stránka je verejná –
merať môže aj neprihlásený návštevník; prihlásený zverenec si nameraný čas uloží medzi výsledky, tréner ho uloží
vybranému zverencovi.

**Ako merať**

1. Stlač **Zapnúť kameru** a povoľ prístup (na telefóne sa použije zadná kamera, **Otočiť kameru** ju prepne).
2. Kameru postav pevne, kolmo na dráhu, s pokojným pozadím za cieľom. Cieľovú čiaru potiahni myšou alebo prstom na
   miesto cieľa – ťahaním konca ju nakloníš, ťahaním stredu posunieš, ťahaním mimo čiary nakreslíš novú.
3. Pri prázdnom cieli stlač **Kalibrovať** (po zapnutí kamery a po presune čiary sa kalibruje aj sama).
4. **Štart** (alebo medzerník). Počas **ochrannej doby** (predvolene 1 s) fotobunka nereaguje, aby ju nezopla ruka
   štartéra. Keď bežec pretne čiaru, čas sa zastaví alebo sa zapíše medzičas – podľa voľby **Pri pretnutí**.
5. **Medzičas** (L), **Stop** (medzerník), **Reset** (R). **Štart na zvuk** odštartuje na tlesknutie alebo výstrel
   z mikrofónu.

Pod obrazom vidíš stav fotobunky, pruh aktivity (podiel zmenených bodov na čiare), posuvník **Citlivosť** a
nameranú presnosť, napr. „±33 ms pri 30 fps kamere“. Po zastavení sa prihlásenému zobrazí formulár na uloženie:
disciplína (alebo vlastný text), pre trénera výber zverenca, poznámka. Výsledok sa uloží s dnešným dátumom so
zdrojom *stopky*; medzičasy a spôsob zastavenia (fotobunka / ručne, ±ms) idú do poznámky. Pod stopkami je tabuľka
posledných 10 meraní, ktoré smieš vidieť. Poloha čiary a posuvníky sa pamätajú v prehliadači.

### Zverenci, výsledky z pretekov a osobné rekordy

**Tréner** spravuje zverencov v sekcii **Zverenci** (`/trener/zverenci`):

- **Pridať existujúceho zverenca** – zadá jeho používateľské meno. Zverenec dostane žiadosť, ktorú musí potvrdiť vo
  svojej sekcii **Výsledky**. Kým ju nepotvrdí, tréner jeho údaje nevidí (v zozname je „čaká na potvrdenie“ a
  žiadosť sa dá zrušiť).
- **Vytvoriť nový účet zverenca** – meno, dočasné heslo, voliteľne meno a priezvisko. Zverenec si pri prvom
  prihlásení heslo musí zmeniť; vzťah je potvrdený automaticky.
- **Odpojiť** – vzťah sa zruší, údaje zostávajú zverencovi.
- Zoznam ukazuje počet výsledkov, posledný výsledok a najbližší naplánovaný tréning.

**Detail zverenca** (`/trener/zverenci/<id>`) obsahuje osobné rekordy (najlepší čas na každú disciplínu – počítajú sa,
neukladajú), tabuľku výsledkov z pretekov s mazaním, formulár **Pridať výsledok** (disciplína zo zoznamu alebo
vlastný názov, čas ako minúty · sekundy · stotiny, dátum, preteky, umiestnenie, vietor, poznámka), tlačidlo
**Načítať z World Athletics**, odkazy na **Grafy** a **Plán** zverenca a prehľad jeho analýz videa.

**Zverenec** má stránku **Výsledky a osobné rekordy** (`/zverenec/vysledky`): vidí len svoje výsledky a rekordy, môže si
výsledok pridať alebo zmazať. Hore rieši vzťah s trénerom – prijme alebo odmietne žiadosť a kedykoľvek sa môže
**odpojiť od trénera**.

Časy sa všade zobrazujú rovnako: do minúty `11.23`, nad minútu `2:05.40`, nad hodinu `1:02:05.50`. Výsledok má
zdroj *ručne* / *World Athletics* / *stopky*.

### Import osobných rekordov z World Athletics

Tréner (alebo administrátor) môže zverencovi **automaticky** načítať osobné rekordy zo stránky worldathletics.org:

1. V detaile zverenca klikne **Načítať z World Athletics** (`/wa/hladat/<id>`).
2. Zadá meno atléta (predvyplní sa meno zverenca), alebo priamo číselné World Athletics ID či odkaz na profil
   (`https://worldathletics.org/athletes/...-14511687`). Pri odkaze alebo čísle sa hľadanie preskočí.
3. Vo výsledkoch skontroluje krajinu a rok narodenia (hľadanie je približné) a klikne **Importovať**.
4. Zobrazí sa **náhľad**: disciplína, výkon, dátum, miesto, vietor a stav (*nové* / *už evidované*). Nič sa neuloží
   bez potvrdenia.
5. **Potvrdiť import** uloží nové výsledky so zdrojom `world_athletics` a zverencovi priradí World Athletics ID. Neskôr
   stačí **Obnoviť z WA** – pridajú sa len výsledky, ktoré ešte nie sú evidované.

Importujú sa len bežecké disciplíny s platným časom (šprinty, prekážky, stredné a dlhé trate, chôdza, maratón);
názvy sa prevedú na kódy aplikácie (`100 Metres` → `100m`, `400 Metres Hurdles` → `400mH`, halové `Short Track` →
`200m (hala)`). Skoky, vrhy, viacboje a štafety sa preskočia a v náhľade sú označené ako *preskočené*. Výkony
s nelegálnym vetrom sa neukladajú, aby neskreslili osobný rekord. Pôvodný zápis (`10.13=`, `h` – ručný čas) a
rekordné značky idú do poznámky.

### Plán tréningov (`/plan`)

Jeden kalendár pre trénera aj zverenca: tréner v ňom plánuje, zverenec zapisuje, čo naozaj odbehol.

**Tréner / admin**
- V kalendári (Po – Ne, dnešok zvýraznený) vyberie zverenca (len potvrdených). Kliknutím na deň otvorí detail a
  formulárom **Naplánovať tréning** pridá názov a popis. Plán môže upraviť (aj presunúť na iný dátum) alebo zmazať.
- V detaile dňa vidí aj skutočné zápisy zverenca z denníka a jeho poznámku „ako to išlo“.
- **Prehľad splnenia** (`/plan/prehlad`): pre každého zverenca počet naplánovaných, splnených a nesplnených tréningov za
  30 dní s percentom, zoznam nesplnených po termíne a program na najbližších 7 dní.

**Zverenec**
- Vidí len svoj plán. V kalendári sú tri druhy položiek: obrysový čip = naplánované, vyplnený čip so ✓ = splnené,
  červený čip s ! = nesplnené po termíne; drobným písmom zápisy z denníka.
- V detaile dňa klikne **Splniť** – vyplní vzdialenosť, čas a poznámku; vznikne zápis v denníku (sekcia Tréningy)
  prepojený s plánom. Alebo prepojí už existujúci zápis z toho dňa. **Označiť ako nesplnené** splnenie zruší.

### Grafy výkonnosti (`/grafy`)

Zverenec vidí svoje grafy, tréner si vyberie zverenca, administrátor ktoréhokoľvek. Päť častí:

1. **Vývoj výsledkov** – časy z pretekov vo vybranej disciplíne, os Y obrátená (*nižší čas = vyššie*), osobný rekord
   má väčšiu značku. Nad grafom dlaždice PB / posledný / prvý výsledok.
2. **Osobné rekordy** – tabuľka PB pre všetky disciplíny a graf *posun PB* (bežiace minimum).
3. **Tréningový objem** – kilometre za posledných 12 týždňov z denníka tréningov.
4. **Plnenie plánu** – posledných 8 týždňov, naplánované vs. splnené tréningy a percento plnenia.
5. **Technika z videa** – priemerný kontakt so zemou (ms) a kadencia z uložených analýz videa.

Pod každým grafom je **Zobraziť ako tabuľku** s rovnakými číslami; stránka funguje aj bez JavaScriptu. Rovnaké dáta
vracia `GET /grafy/api/<metrika>?athlete_id=&discipline=` pre `vysledky`, `rekordy`, `objem`, `plan`, `technika`.

### Administrátor – správa používateľov

Sekcia **Používatelia** (`/admin/pouzivatelia`) je dostupná len administrátorovi:

- **Zoznam účtov** s hľadaním a filtrom podľa roly; pri zverencovi tréner (s príznakom „čaká“), pri trénerovi počet
  zverencov, počty tréningov / výsledkov / videí.
- **Nový účet** – zverenec, tréner alebo ďalší admin, počiatočné heslo, voliteľne tréner; predvolene „pri ďalšom
  prihlásení si musí zmeniť heslo“.
- **Upraviť** – meno, rola, tréner (priradenie adminom platí ako potvrdený súhlas), World Athletics ID, vynútená
  zmena hesla. Zmena roly z trénera odpojí jeho zverencov. Posledného administrátora nemožno preradiť a admin si
  vlastnú rolu odobrať nemôže.
- **Reset hesla** – nové dočasné heslo, ktoré si používateľ musí zmeniť.
- **Zmazať** – potvrdzovacia stránka so súhrnom dát, treba opísať používateľské meno. S účtom sa zmažú jeho tréningy,
  výsledky, plány, biometrika, AI správy, profil, Strava pripojenie a analýzy videí vrátane súborov na disku. Nedá sa
  zmazať vlastný účet, posledný administrátor ani tréner, ktorý má priradených zverencov.
- **Prehľad systému** (`/admin/prehlad`) – počty účtov, množstvo dát, veľkosť videí a databázy, verzie Pythonu a
  Flasku, dostupnosť MediaPipe, či sú v `.env` nastavené kľúče (len áno / nie).

### Ďalšie moduly (z prvej verzie projektu)

- **Tréningový denník** (`/trainings`) – ručný zápis tréningov s typom, vzdialenosťou, časom, intervalmi a poznámkou.
- **Logovanie biometriky** (`/log`) – ručný zápis HRV / recovery / RHR a import CSV z WHOOP; **readiness skóre**
  (`/api/readiness`) z pomeru HRV k 30-dňovému priemeru a recovery.
- **Strava** – OAuth 2.0 prihlásenie, posledné aktivity (`/dashboard`), osobné rekordy zo Stravy (`/osobaky`) a
  heatmapa behov na mape (`/heatmap`). Vyžaduje vlastnú Strava aplikáciu (kľúče v `.env`).
- **AI chat** (`/ai`) – Groq API, do kontextu dostane profil, biometriku, tréningy a posledné analýzy videa.
  Vyžaduje `GROQ_API_KEY`.
- **Profil** (`/profile`) – meno, vek, výška (slúži na kalibráciu mierky pri videu), disciplína, cieľ.

---

## 2. Ako to funguje technicky

### Architektúra

Flask aplikácia s plochou štruktúrou modulov. `app.py` drží konfiguráciu, migráciu databázy, prvotné vytvorenie
admina a pôvodné routy (video, prekážky, tréningy, biometrika, Strava, AI, profil, chránené médiá). Nové funkcie sú
v blueprintoch:

| Súbor | Blueprint | URL prefix | Obsah |
|---|---|---|---|
| `app.py` | – | `/` | Flask app, config, `_ensure_columns()`, `_seed_admin()`, video, prekážky, `/media`, Strava, AI, tréningy, biometrika, profil |
| `models.py` | – | – | `db = SQLAlchemy()` a všetky modely (importujú ich blueprinty bez kruhového importu) |
| `auth.py` | `auth` | `/` | `/prihlasenie`, `/odhlasenie`, `/registracia`, `/zmena-hesla`; `g.user`, dekorátory, viditeľnosť údajov |
| `trener.py` | `trener` | `/trener` | zverenci, žiadosti, výsledky a osobné rekordy zverenca |
| `zverenec.py` | `zverenec` | `/zverenec` | moje výsledky a PB, prijatie / odmietnutie trénera |
| `planovanie.py` | `planovanie` | `/plan` | kalendár, plánovanie, splnenie, prehľad splnenia |
| `grafy.py` | `grafy` | `/grafy` | grafy výkonnosti + JSON API |
| `stopky.py` | `stopky` | `/stopky` | stránka stopiek, uloženie času (meranie beží v `static/js/stopky.js`) |
| `admin_panel.py` | `admin_panel` | `/admin` | správa používateľov, prehľad systému |
| `world_athletics.py` | `wa` | `/wa` | hľadanie, náhľad, import a obnovenie PB z World Athletics; servisné funkcie testovateľné offline |
| `video_analysis.py` | – | – | analýza šprintu (MediaPipe + OpenCV) |
| `hurdle_analysis.py` | – | – | prekážky nad výstupom analýzy videa |
| `technique_rules.py` | – | – | pravidlá hodnotenia techniky a rytmu |

Žiadny blueprint neimportuje `app.py`; všetky berú `db` a modely z `models.py` a prístupové pomôcky z `auth.py`.
Navigácia v `templates/base.html` sa skladá podľa roly; názvy endpointov sú kontrakt medzi modulmi.

### Prístupové práva (`auth.py`)

- `g.user` sa načíta pred každým requestom zo `session['user_id']`. Heslá sú hašované
  (`werkzeug.security`), session cookie je `HttpOnly`, `SameSite=Lax` a nie je trvalá.
- `login_required` presmeruje na prihlásenie (JSON cestám vráti 401), `roles_required(*roly)` vráti 403; admin
  prejde všade.
- **Viditeľnosť údajov** je na jednom mieste: `athlete_ids_visible_to(user)` vráti množinu id, ktorých údaje smie
  používateľ vidieť – zverenec seba, tréner seba + zverencov s `coach_id == user.id` **a** `coach_confirmed = True`,
  admin všetkých. Nad ňou stoja `require_visible_athlete(id)` (403/404), `row_visible(user_id)` a `scope_query()`,
  ktoré používa každá routa s id.
- Účet s `must_change_password` sa dostane len na zmenu hesla (aj JSON volania dostanú 403).
- Prihlásenie je obmedzené: po 5 neúspešných pokusoch za minútu pre dvojicu (meno, IP) odpoveď 429.
- Odhlásenie je len cez POST, aby cudzia stránka nemohla používateľa odhlásiť cez `<img src>`.

### Dátový model (`models.py`)

| Tabuľka | Kľúčové stĺpce |
|---|---|
| `user` | `username` (unikátne), `password_hash`, `role` (`zverenec` / `trener` / `admin`), `full_name`, `coach_id` → `user.id`, **`coach_confirmed`** (tréner vidí údaje až keď je `True`), `world_athletics_id`, `must_change_password`, `created_at` |
| `race_result` | `user_id` (atlét), `discipline`, `result_s` (sekundy), `date`, `competition`, `place`, `wind`, `source` (`manual` / `world_athletics` / `stopky`), `note`, `created_by` (kto zapísal). Osobný rekord = `min(result_s)` na disciplínu, počíta sa, neukladá. |
| `planned_training` | `athlete_id`, `coach_id`, `date`, `title`, `description`, `completed`, `completed_at`, `athlete_note`, `training_log_id` → `training_log.id` |
| `training_log` | `user_id`, `date`, `training_type`, `distance_km`, `duration_min`, `duration_sec`, `intervals_data`, `notes` |
| `biometric_log` | `user_id`, `date`, `hrv`, `recovery`, `rhr` |
| `video_analysis` | `user_id`, `stored_name`, `overlay_name`, `fps`, `steps_detected`, `contact_ms_mean`, `flight_ms_mean`, `cadence_spm`, `duty_factor_pct`, `asymmetry_pct`, `speed_ms`, `stride_length_m`, `result_json` (celý výsledok vrátane krokov, uhlov, hodnotenia a komentára AI trénera) |
| `hurdle_analysis` | `user_id`, `discipline`, `hurdles_detected`, `interval_mean_s`, `interval_sd_s`, `steps_pattern`, `flight_ms_mean`, `speed_between_ms_mean`, `result_json` |
| `user_profile` | `user_id`, `name`, `age`, `height_cm`, `sport`, `goal` |
| `chat_message` | `user_id`, `role`, `content`, `created_at` |
| `strava_token` | `user_id`, `athlete_id`, `access_token`, `refresh_token`, `expires_at` |

Každý riadok s údajmi má **`user_id` = vlastník**, a každý dotaz je ním filtrovaný. Tabuľky vytvorí
`db.create_all()`; chýbajúce stĺpce v staršej databáze (`user_id`, `coach_confirmed`, `height_cm`) doplní
`_ensure_columns()` cez `ALTER TABLE ADD COLUMN`, takže databáza z verzie pred účtami zostane funkčná. Riadky bez
vlastníka (z verzie pred účtami) vidí len administrátor.

### Analýza videa (MediaPipe → OpenCV → metriky)

1. **Kostra** – každý snímok prejde cez MediaPipe Pose (33 bodov tela). Modul podporuje staršie API `mp.solutions`
   aj novšie Tasks API a vyberie si podľa nainštalovanej verzie (model kostry si prípadne stiahne do `models/`).
2. **Normalizácia** – poloha chodidla sa počíta voči panve a delí dĺžkou trupu, takže meranie funguje aj pri zoome a
   pohyblivej kamere. Od výšky chodidla sa odčítava *vyhladená* výška panvy – jej kmitanie v rytme krokov by inak
   detekciu rozbilo.
3. **Optický tok (OpenCV)** – nezávisle od kostry sa Lucas-Kanadeho metódou (`cv2.calcOpticalFlowPyrLK`) sleduje pohyb
   bodov vnútri chodidla. Z bodov pozadia sa cez `cv2.estimateAffinePartial2D` (RANSAC) odhadne pohyb kamery
   a odpočíta sa – výsledkom je rýchlosť chodidla **voči zemi**, ktorá počas opory klesne takmer na nulu. Táto
   metóda o kostre nič nevie, takže jej zhoda s kostrou je poctivá kontrola, nie sebapotvrdenie.
4. **Detekcia krokov dvoma metódami** – *polohová* (Zeni et al., 2008: dopad = maximum vzdialenosti päty pred panvou)
   nájde udalosti hrubo a bez prahu, *vertikálna* (noha je v kontakte, kým je jej najnižší bod v pásme pri zemi) ich
   spresní. Rozdiel medzi nimi sa vypisuje ako kontrola kvality.
5. **Sub-frame spresnenie** – okamih dopadu je priesečník priamky klesania chodidla a priamky „na zemi“, takže
   presnosť je lepšia než jeden snímok a bez systematického nadhodnotenia.
6. **Kalibrácia** – z výšky atléta sa určí mierka (metre na pixel) zo štyroch segmentov tela nezávisle (medián), aby
   jeden zle rozpoznaný bod kostry mierku nepokazil.
7. **Dĺžka kroku a rýchlosť** – dĺžka kroku sa meria **geometricky** ako vzdialenosť medzi miestami dopadu dvoch po
   sebe idúcich krokov; pri pohyblivej kamere sa polohy prevedú „do sveta“ odčítaním posunu pozadia. Nezávisle sa
   počíta aj rýchlosť z posunu panvy voči stojnej nohe; pri nezhode nad 25 % to appka povie. Rozmery snímky sa berú
   zo skutočnej snímky, nie z hlavičky videa (telefón otočený na výšku).
8. **Zoom na bežca** – keď je bežec v zábere malý, model dostáva výrez z plného rozlíšenia, ktorý sa plynulo posúva
   za ním. Pri viacerých ľuďoch v zábere sa bežec vyberie kliknutím do prvého snímku (prehliadač ho vykreslí do
   `<canvas>`, súradnice idú ako `click_x` / `click_y`).
9. **Uhly vo videu a kľúčové snímky** – do prekryvného videa sa kreslia oblúky uhlov v kolenách, náklon trupu a uhol
   holene stojnej nohy; snímky dopadu a odrazu sa ukladajú ako JPEG.
10. **Hodnotenie techniky** (`technique_rules.py`) – 13 pravidiel pre šprint a 4 pre prekážky, každé s referenčným
    pásmom, stavom *ok / sledovať / riešiť*, vysvetlením a tipom do tréningu.

**Prekážky** (`hurdle_analysis.py`) prekážku vo videu nehľadajú ako predmet – prezradí ju beh. Medzi dvoma
kontaktmi je pri behu let ~0,12 s, pri prechode prekážky 0,3 – 0,5 s. Každý let výrazne dlhší než bežný krok (viac
než 1,7× medián) je kandidát na prechod prekážky; kandidát ešte prejde kontrolou (odraz a dopad z rôznych nôh,
zdvih panvy voči odrazu a dopadu, viditeľná kostra, minimálny odstup prekážok). Kontakt pred ním je odraz, po
ňom dopad. Zvyšok (medzičasy, počet krokov,
rýchlosť z pravidlami danej vzdialenosti) je aritmetika nad zoznamom kontaktov.

**Presnosť.** Kontakt so zemou pri šprinte trvá okolo 0,11 s; pri 30 fps sú to 3 snímky, čo je menej než rozdiel
medzi dobrým a zlým krokom. Preto **natáčaj spomalene (120 alebo 240 fps)** – aplikácia si fps prečíta sama a pri
nízkej hodnote výsledok označí ako orientačný. Overenie metódy kostry na syntetických dátach so známym časom
kontaktu (`python test_video_analysis.py`): RMS chyba 0,8 ms pri 240 fps, 1,4 ms pri 120 fps, 3,8 ms pri 60 fps,
25,8 ms pri 30 fps. Optický tok na vykreslenom videu (`python test_optical_flow.py`): všetky kontakty nájdené,
chyba dĺžky kontaktu do −7,5 ms, aj pri kamere uhýbajúcej za bežcom. Prekážková analýza
(`python test_hurdle_analysis.py`): všetky prekážky, správny počet krokov, medzičas na ±20 ms pri 30 – 240 fps;
vypadnutý kontakt uprostred úseku nevytvorí falošnú prekážku a úsek sa označí ako neúplný (`?-13`); video
s kostrou na 30 % snímok sa odmietne.
Tieto čísla platia pre algoritmy; v reálnom videu sa pripočíta chyba rozpoznávania kostry (svetlo, pozadie,
ostrosť) – práve na to slúži porovnanie oboch metód v aplikácii. Pri prenose videa z iPhonu zachová 240 fps
**AirDrop originálu**; export „Uložiť ako video“ môže záber prerenderovať na 30 fps a časy vyjdú ~8× dlhšie.
Údaj fps sa dá v paneli presnosti prepísať.

**Ako natáčať:** spomalene (Slo-mo), presne z boku, kamera v úrovni bokov na statíve, celé telo vrátane chodidiel
v zábere, 6 – 8 krokov v rovnomernom tempe, kontrastné pozadie a dosť svetla.

### Virtuálna fotobunka (`static/js/stopky.js`)

Celé meranie beží v prehliadači, server dostane až hotový čas:

1. Obraz z kamery sa každých ~33 ms zmenší na 320 px a **po cieľovej čiare sa odčíta jas 64 bodov** (pás ±2 px
   kolmo na čiaru, aby šum kamery nevadil).
2. **Kalibrácia** uloží jas týchto bodov ako základ; kým sa nič nehýbe, základ sa pomaly dolaďuje (EMA, α = 0,02),
   aby mraky a tiene nespôsobili falošný stop.
3. Každá snímka sa porovná so základom: bod je „zmenený“, ak sa jeho jas líši o viac než **prah citlivosti**
   (predvolene 40 z 255, posuvník).
4. Pretnutie = aspoň **25 % bodov zmenených v 2 snímkach po sebe**; berie sa čas prvej z nich. Ďalšie pretnutie sa
   uzná najskôr po 700 ms a až keď zmena medzitým zmizla.
5. Fotobunka je **odzbrojená počas ochrannej doby** po štarte (predvolene 1 s), aby ju nezopla ruka štartéra.

Presnosť je daná snímkovou frekvenciou kamery (±33 ms pri 30 fps) a oneskorením kamery v prehliadači; ak prehliadač
poskytne `captureTime`, latencia sa odpočíta. Štart na zvuk je jednoduchý RMS prah hlasitosti z mikrofónu.

### World Athletics (`world_athletics.py`)

World Athletics **nemá verejné API**. Stránka worldathletics.org však volá GraphQL endpoint (AWS AppSync) s kľúčom,
ktorý posiela každému návštevníkovi vo svojom JavaScript bundli – modul používa ten istý:

- **Objavenie endpointu a kľúča** – `discover_credentials()` stiahne stránku `/athletes`, prejde jej JS chunky a
  regulárnym výrazom nájde `endpoint` a `apiKey`; výsledok sa uloží do `cache/` na 7 dní. V kóde je aj záložná
  hodnota kľúča.
- **Rotácia kľúča** – ak GraphQL odpovie HTTP 503 (presne to vráti CloudFront pri neplatnom kľúči), kľúč sa raz
  znova vyhľadá v bundli a dotaz sa zopakuje.
- **Fallback bez kľúča** – ak GraphQL zlyhá úplne, profil atléta sa prečíta z HTML stránky
  (`<script id="__NEXT_DATA__">`), ktorá nesie osobné rekordy v JSON-e. Hľadanie podľa mena bez kľúča nefunguje –
  tréner vtedy vloží odkaz na profil.
- Importujú sa **len bežecké (časové) individuálne disciplíny** – `RaceResult.result_s` sú sekundy; skoky, vrhy,
  viacboje a štafety sa preskočia. Parsovanie výkonov (`10.23`, `1:45.67`, `2:05:11`, prípony `=`, `A`, `h`,
  `DNF/DQ/NM` → nič), dátumov, vetra a názvov disciplín je v čistých funkciách testovaných offline.
- Úspešné odpovede sa cachujú 24 h (`cache/wa_*.json`), **Obnoviť z WA** cache obchádza. Import sa spúšťa
  **výlučne ručne** trénerom, nič sa nesťahuje automaticky. Ide o neoficiálne použitie údajov vhodné pre školský
  projekt; každá chyba siete skončí slovenskou správou, stránky nikdy nepadnú.

### Chránené médiá

Nahrané videá, prekryvné videá a kľúčové snímky ležia v `instance/videos/` (mimo `static/`) a podáva ich routa
`/media/<súbor>` s kontrolou prihlásenia a vlastníka: súbor vidí len ten, kto smie vidieť samotnú analýzu. Názvy
súborov sú náhodné (UUID); staré súbory zo `static/uploads/videos` sa pri štarte presunú.

---

## 3. Spustenie

**Požiadavky:** Python **3.9 – 3.12** (knižnica `mediapipe` nemá wheely pre 3.13+; bez nej beží všetko okrem analýzy
videa a stránka `/video` to oznámi). Na macOS: `brew install python@3.12`.

### Hotové spúšťače

| Systém | Súbor | Ako |
|--------|-------|-----|
| macOS | `run.command` | dvojklik v Finderi (alebo `chmod +x run.command && ./run.command`) |
| Windows | `run.bat` | dvojklik |

Spúšťač si sám vytvorí `venv`, nainštaluje balíky z `requirements.txt` (prvýkrát aj pár minút – `mediapipe`
a `opencv` sú veľké) a otvorí prehliadač na `http://127.0.0.1:5001`. Ak macOS súbor odmietne otvoriť, povoľ ho
v *Nastavenia → Súkromie a bezpečnosť*.

### Ručne

```bash
git clone https://github.com/scalyeu/maturitny_projekt.git
cd maturitny_projekt
python3.12 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # voliteľné – kľúče pre Groq a Stravu
python app.py
```

### Prvé spustenie

Pri prvom spustení sa vytvorí databáza `instance/database.db` a účet **`admin` / `admin`** s vynútenou zmenou hesla
– po prihlásení ťa appka pustí len na stránku zmeny hesla. Nápoveda s predvolenými údajmi na prihlasovacej stránke
zmizne po zmene hesla. Admin potom vytvorí trénerov (alebo sa registrujú sami cez `/registracia`), tréner vytvorí
zverencov alebo si ich pridá podľa používateľského mena.

### Premenné prostredia (`.env`)

Vzor je v `.env.example`. Appka funguje aj bez `.env`; kľúče potrebujú len AI a Strava.

| Premenná | Význam | Predvolené |
|---|---|---|
| `SECRET_KEY` | podpisuje session cookie; ak chýba, vygeneruje sa raz do `instance/secret_key` | generuje sa |
| `FLASK_DEBUG` | `0` vypne debug režim (pri nasadení povinné) | `1` |
| `ATLETCOACH_PORT` | port servera | `5001` |
| `ATLETCOACH_RELOAD` | `0` vypne automatický reload pri zmene kódu | `1` |
| `ATLETCOACH_NO_BROWSER` | `1` neotvorí prehliadač pri štarte | – |
| `ATLETCOACH_DB_URI` | iná databáza (SQLAlchemy URI), napr. pre testy | `sqlite:///database.db` v `instance/` |
| `GROQ_API_KEY` | kľúč pre AI chat a AI trénera (console.groq.com) | – (AI vypnuté) |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REDIRECT_URI` | vlastná Strava aplikácia (strava.com/settings/api); callback `http://localhost:5001/strava/callback` | – (Strava vypnutá) |

### Kamera pri stopkách

Prehliadač povolí kameru len v **zabezpečenom kontexte**: `https://` alebo priamo na počítači cez
`http://localhost` / `http://127.0.0.1`. Z telefónu cez obyčajné `http://` na lokálnej sieti kamera nefunguje –
ostávajú ručné stopky a stránka to vysvetlí.

---

## 4. Nasadenie

Zadanie hovorí o nasadení; projekt sa **predvádza lokálne** (spúšťač + prehliadač na jednom počítači, prípadne
v školskej sieti). Toto by si vyžadovalo skutočné nasadenie:

- **Server** – VPS s približne 4 – 8 GB RAM. Analýza videa cez MediaPipe a OpenCV beží na CPU a pri spomalenom videu
  trvá desiatky sekúnd na jednu úlohu. Každá analýza beží vo vlastnom vlákne priamo v procese servera (žiadna
  fronta úloh), takže viac súbežných analýz si delí CPU a rozpracovaná úloha sa pri reštarte stratí (uložené
  výsledky ostávajú).
- **WSGI server** namiesto vývojového: `gunicorn app:app` (Linux) alebo `waitress-serve app:app` (Windows), s
  `FLASK_DEBUG=0` a `ATLETCOACH_RELOAD=0`. Vývojový server Flasku nie je určený pre verejnú prevádzku.
- **HTTPS** – kamera pre fotobunku funguje len cez zabezpečené spojenie, takže pred aplikáciou musí byť reverzná
  proxy (nginx / Caddy) s certifikátom.
- **`SECRET_KEY`** pevne nastavený v `.env`; inak sa pri zmene stroja vygeneruje nový a všetky prihlásenia padnú.
- **Trvalý adresár `instance/`** – databáza, `secret_key` aj nahrané videá (`instance/videos/`) musia prežiť
  reštart a aktualizáciu kódu; pri kontajneri je to zväzok (volume). Zálohovanie nie je súčasťou aplikácie.
- **Limity** – jedna SQLite databáza (na desiatky používateľov stačí, nie na súbežné zápisy stoviek), limit nahrania
  600 MB na video, `cache/` pre World Athletics a Stravu.

---

## 5. Bezpečnosť a známe obmedzenia

### Čo je urobené

- **Izolácia údajov** – každý riadok má vlastníka (`user_id`), viditeľnosť rieši jediná funkcia
  `athlete_ids_visible_to()` a routy s id ju používajú cez `require_visible_athlete` / `row_visible` /
  `scope_query`. Cudzí id → 403, neexistujúci → 404 (bez prezradenia, ktoré id existujú).
- **Súhlas zverenca** – tréner pridá zverenca podľa mena, ale kým zverenec žiadosť nepotvrdí
  (`coach_confirmed`), tréner nevidí ani jeden jeho údaj – ani na svojom prehľade, v pláne, grafoch, videu či
  stopkách. Zverenec sa môže kedykoľvek odpojiť.
- **Chránené médiá** – videá a snímky sú mimo `static/` a routa `/media` kontroluje prihlásenie aj vlastníka.
- **Heslá** hašované (`werkzeug.security`); predvolený admin musí heslo zmeniť, kým smie čokoľvek robiť (aj cez JSON
  API); trénerom a adminom vytvorené účty majú vynútenú zmenu dočasného hesla.
- **Prihlásenie** je obmedzené na 5 neúspešných pokusov za minútu na (meno, IP) → 429.
- **Odhlásenie len cez POST**, `next` parameter pripúšťa len relatívne cesty (bez open redirectu), session cookie
  `HttpOnly` + `SameSite=Lax`, nie trvalá.
- **Vstupy** – prípony videí sú na zoznam povolených a obsah sa overuje (`probe_video`); World Athletics ID musí byť
  číslo (žiadne cudzie URL → bez SSRF); časy výsledkov odmietajú nulu, záporné a nekonečné hodnoty; poznámka pri
  výsledku je obmedzená na 2000 znakov; WHOOP import zahodí nezmyselné hodnoty (HRV 0 – 500, recovery 0 – 100,
  RHR 20 – 250). Súbory z `/media` sa podávajú len podľa základného názvu (bez path traversal).
- **Administrátor** sa nemôže zamknúť: posledného admina nemožno zmazať ani preradiť, zmazanie účtu vyžaduje
  opísať meno.

### Ako sa to overovalo

Okrem jednotkových testov prešla aplikácia integračnou sadou 375 kontrol (všetky role, každá routa s id ako každý
aktér, migrácia starej databázy, navigácia bez chýb) a tromi nezávislými útočnými testami zameranými na
súbory / nahrávanie / vstupy, autentifikáciu / session / formuláre a IDOR / izoláciu údajov. Nájdené a opravené
boli: prehľad trénera zobrazoval posledný výsledok zverenca, ktorý žiadosť ešte nepotvrdil; vynútená zmena hesla sa
netýkala JSON volaní; `/stopky`, `/plan`, `/grafy` bez koncovej lomky odpovedali presmerovaním; neobmedzená dĺžka
poznámky pri výsledku; WHOOP import bez kontroly rozsahu; chýbajúce obmedzenie pokusov o prihlásenie; odhlásenie
cez GET. Žiadny test nenašiel čítanie ani zápis cudzích údajov, obídenie prihlásenia, path traversal ani XSS.

### Čo ostáva

- **Bez CSRF tokenov** vo formulároch. Zmierňuje to `SameSite=Lax` cookie, POST na všetky deštruktívne akcie a
  opísanie mena pri mazaní účtu, ale plná CSRF ochrana (napr. Flask-WTF) chýba.
- **World Athletics** – neoficiálne rozhranie; kľúč sa môže zmeniť (modul ho znova nájde a má HTML fallback, ale
  vyhľadávanie podľa mena bez kľúča nefunguje). Importujú sa len osobné rekordy, nie celé sezóny. Bez internetu
  import nefunguje.
- **Fotobunka** – presnosť ±33 ms pri 30 fps kamere; reaguje na akúkoľvek zmenu jasu na čiare (tieň, divák, pohnutá
  kamera), kamera musí stáť pevne; štart na zvuk môže v hluku odštartovať falošne. Nie je to na oficiálne výsledky.
  Kamera len cez https alebo localhost. Nastavenia čiary sú v prehliadači, nie na serveri.
- **Video** – uhly sa počítajú v rovine obrazu (2D), platia len pri zábere z boku; presnosť kontaktov je zdola
  ohraničená fps; MediaPipe sleduje jedného človeka a pri prekrývaní bežcov môže kostra preskočiť; veľmi nízke
  tréningové prekážky (let kratší než ~0,22 s) sa neodlíšia od kroku; referenčné pásma sú orientačné, nie
  individuálna norma. Prekážky potrebujú jeden nestrihaný záber z boku na jedného bežca – strihaný záznam
  z televízie alebo záber s viacerými bežcami analýza odmietne alebo označí úseky ako neúplné; ak MediaPipe zamení
  ľavú a pravú nohu presne pri odraze alebo dopade, prekážka sa zamietne a susedný úsek sa ohlási ako neúplný.
- **Výsledky** – výsledok sa nedá upraviť (len zmazať a pridať znova); vlastné názvy disciplín sa nezjednocujú
  („100 m“ a „100m“ sú dve disciplíny); časy na stotiny.
- **Plán** – zverenec plán neupravuje ani nemaže; tréner upravuje len plány, ktoré sám vytvoril; intervaly sa
  zapisujú len v denníku; percentá sa počítajú z tréningov, ktorých termín už nastal.
- **Grafy** – kilometre len z denníka tréningov (Strava sa nezapočítava); splnenie plánu určuje odškrtnutie; technika
  sa kreslí podľa dátumu analýzy, nie natáčania.
- **Administrácia** – prihlasovacie meno sa po vytvorení nemení; zoznam nemá stránkovanie; výsledky iných atlétov
  zapísané zmazaným používateľom ostávajú bez autora.
- **Infraštruktúra** – jedna SQLite databáza a videá v `instance/videos` bez zálohovania; Strava token v databáze
  v čitateľnej podobe; obmedzenie prihlásenia beží v pamäti (po reštarte sa zabudne); analýza videa beží vo vlákne
  procesu.
- **Session** nie je trvalá – zatvorením prehliadača sa odhlásiš (zámerne).

---

## 6. Testy

```bash
venv/bin/python -m pytest -q test_world_athletics.py   # 60 testov, offline (HTTP je nahradené)
python test_video_analysis.py     # kontakt so zemou, mierka, dĺžka kroku, celý reťazec – syntetické dáta
python test_hurdle_analysis.py    # prekážky: detekcia, kroky medzi prekážkami, medzičasy, kontrola vierohodnosti
python test_optical_flow.py       # optický tok na vykreslenom videu s textúrami
```

- `test_world_athletics.py` (pytest, 60 testov): parsovanie výkonov, dátumov, vetra a disciplín, filter bežeckých
  disciplín, extrakcia WA ID z URL, spracovanie reálnych dát profilu, hľadanie a načítanie s falošným HTTP, overenie
  ID pred sieťovým volaním, chybové stavy so slovenskými správami, znovuobjavenie kľúča pri 503, HTML fallback,
  expirácia cache.
- Tri skriptové testy analýzy videa (spúšťajú sa priamo Pythonom, nie cez pytest) overujú algoritmy na syntetických
  dátach so známou pravdou – čísla sú v časti [Analýza videa](#analýza-videa-mediapipe--opencv--metriky).
- Integračná sada (375 kontrol cez Flask `test_client` a živý server na dočasnej databáze) a útočné testy bežali
  mimo repozitára ako súčasť vývoja; ich výsledky sú zhrnuté v časti [Bezpečnosť](#5-bezpečnosť-a-známe-obmedzenia).

---

## 7. Technické parametre

| Oblasť | Technológia |
|---|---|
| Jazyk a server | Python 3.9 – 3.12, Flask 3.1, Jinja2 šablóny, python-dotenv |
| Databáza | SQLite cez Flask-SQLAlchemy 3.1 (aditívna migrácia `ALTER TABLE`) |
| Frontend | HTML, CSS, Tailwind CSS (CDN), JavaScript (bez frameworku), fonty Archivo / IBM Plex |
| Grafy a mapy | Chart.js, Leaflet (heatmapa behov, vlastný dekodér Google polyline) |
| Počítačové videnie | OpenCV 4.10 (optický tok Lucas-Kanade, RANSAC odhad pohybu kamery, kreslenie prekryvu), MediaPipe Pose 0.10 (33 bodov kostry), NumPy 1.26 |
| Fotobunka | Web API prehliadača: `getUserMedia`, `<canvas>` `getImageData`, Web Audio (štart na zvuk), `localStorage` |
| Externé služby | World Athletics GraphQL (neoficiálne, `requests`), Strava API (OAuth 2.0, `stravalib` + `requests`), Groq API (LLM chat completions, modely `llama-3.3-70b-versatile` → `openai/gpt-oss-120b` → `deepseek-r1-distill-llama-70b` ako záloha) |
| Bezpečnosť | `werkzeug.security` hašovanie hesiel, podpísaná session cookie, role a viditeľnosť v `auth.py` |
| Testy | pytest (World Athletics), skriptové testy na syntetických dátach (video, prekážky, optický tok) |
| Nástroje | Git / GitHub, VS Code, `venv`, spúšťače `run.command` (macOS) a `run.bat` (Windows) |

### Štruktúra projektu

```
maturitny_projekt/
├── app.py                  # Flask app, config, migrácia, admin seed, video / prekážky / Strava / AI / médiá
├── models.py               # db + všetky modely
├── auth.py                 # prihlásenie, registrácia, role, viditeľnosť údajov
├── trener.py               # zverenci, výsledky, osobné rekordy          /trener
├── zverenec.py             # moje výsledky, vzťah s trénerom              /zverenec
├── planovanie.py           # kalendár a plán tréningov                    /plan
├── grafy.py                # grafy výkonnosti + JSON API                  /grafy
├── stopky.py               # stopky s fotobunkou (uloženie času)          /stopky
├── admin_panel.py          # správa používateľov                          /admin
├── world_athletics.py      # import PB z World Athletics                  /wa
├── video_analysis.py       # analýza šprintu (MediaPipe + OpenCV)
├── hurdle_analysis.py      # prekážky
├── technique_rules.py      # hodnotenie techniky a rytmu
├── test_world_athletics.py # pytest
├── test_video_analysis.py  # skriptové testy na syntetických dátach
├── test_hurdle_analysis.py
├── test_optical_flow.py
├── requirements.txt
├── run.command / run.bat   # spúšťače
├── .env.example            # vzor konfigurácie
├── static/css, static/js   # style.css, stopky.js, grafy.js, main.js
├── templates/              # base.html + šablóny podľa modulu (auth/, trener/, zverenec/, planovanie/, grafy/, stopky/, admin/, wa/)
├── instance/               # database.db, secret_key, videos/  (negitované)
├── cache/                  # World Athletics a Strava cache  (negitované)
└── models/                 # model kostry MediaPipe, sťahuje sa sám  (negitované)
```

---

*AtletCoach – maturitný projekt, SPŠE Bratislava, 2025/2026.*
