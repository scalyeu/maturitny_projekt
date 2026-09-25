/*
 * Stopky s kamerou a virtuálnou fotobunkou (AtletCoach).
 *
 * Celé meranie beží v prehliadači, server dostane až hotový čas:
 *   1. kamera ide do <video>; nad ním je plátno s cieľovou čiarou (ťahá sa myšou / prstom),
 *   2. každá snímka sa zmenší na 320 px a po čiare sa odčíta jas ~64 bodov (pás ±2 px),
 *   3. jas sa porovná so základom z kalibrácie – keď sa naraz zmení aspoň štvrtina bodov
 *      v dvoch snímkach po sebe, bežec pretol čiaru → stop alebo medzičas.
 *
 * Zdrojom obrazu je živá kamera alebo video zo záznamu (súbor zo zariadenia / už nahraná
 * analýza). Pri zázname sú hodinami stopiek časová stopa videa: Štart označí aktuálny snímok
 * ako čas 0, pretnutie čiary zastaví čas na snímku cieľa; pauza ani spomalené prehrávanie
 * čas nemenia a presnosť je ±1 snímok videa.
 *
 * Čisté výpočty sú hore a bez DOM, aby sa dali spustiť v node teste; v prehliadači sa
 * export preskočí a pokračuje sa na ovládanie stránky.
 */
(function () {
    'use strict';

    // ── Parametre fotobunky ──────────────────────────────────────────────────
    const PROC_WIDTH = 320;            // šírka spracovacieho plátna – na detekciu stačí a je to rýchle
    const SAMPLES = 64;                // bodov na čiare
    const BAND = 2;                    // ±px kolmo na čiaru, jas sa priemeruje (šum kamery)
    const TRIGGER_FRACTION = 0.25;     // podiel zmenených bodov, ktorý znamená pretnutie
    const CONSECUTIVE_FRAMES = 2;      // zmena musí vydržať toľko snímok po sebe
    const DEBOUNCE_MS = 700;           // dve pretnutia tesne za sebou sú jedno
    const PROCESS_INTERVAL_MS = 1000 / 30;
    const BASELINE_ALPHA = 0.02;       // pomalé doladenie základu (mraky, tiene), nie bežec
    const QUIET_FRACTION = 0.05;       // pod týmto podielom „nič sa nehýbe“ – základ sa smie doladiť
    const MIN_LINE_PX = 24;            // kratšia čiara by mala všetky body na jednom mieste
    const SOUND_RMS = 0.3;             // hlasitosť tlesknutia / výstrelu (0–1)
    const SOUND_WARMUP_MS = 400;       // po zapnutí mikrofónu ignoruj cvaknutie pri štarte
    const VOLT = '#d7f24a';
    const DEFAULT_LINE = { x1: 0.6, y1: 0.06, x2: 0.6, y2: 0.94 };   // relatívne k obrazu kamery

    // ── Čisté funkcie (bez DOM) ──────────────────────────────────────────────

    /** ms → 'mm:ss.hh'. Nezmysly (NaN, záporné) ukážu nulu, nie 'NaN:NaN'. */
    function formatTime(ms) {
        if (!Number.isFinite(ms) || ms < 0) ms = 0;
        // Najprv na celé ms: rozdiel časových značiek videa (3,4 − 1,0 = 2,3999…) nesmie ukázať 2,39.
        const hundredths = Math.floor(Math.round(ms) / 10);
        const h = hundredths % 100;
        const s = Math.floor(hundredths / 100) % 60;
        const m = Math.floor(hundredths / 6000);
        return pad2(m) + ':' + pad2(s) + '.' + pad2(h);
    }

    function pad2(n) { return n < 10 ? '0' + n : String(n); }

    /**
     * Body na čiare v pixeloch spracovacieho plátna + jednotková normála.
     * Čiara nulovej dĺžky dá normálu (1,0) namiesto NaN z delenia nulou.
     */
    function samplePoints(line, width, height, count) {
        const x1 = line.x1 * width, y1 = line.y1 * height;
        const x2 = line.x2 * width, y2 = line.y2 * height;
        const dx = x2 - x1, dy = y2 - y1;
        const len = Math.hypot(dx, dy);
        const nx = len > 1e-6 ? -dy / len : 1;
        const ny = len > 1e-6 ? dx / len : 0;
        const xs = new Float32Array(count), ys = new Float32Array(count);
        for (let i = 0; i < count; i++) {
            const t = (i + 0.5) / count;      // body sú vnútri úsečky, nie presne na koncoch
            xs[i] = x1 + dx * t;
            ys[i] = y1 + dy * t;
        }
        return { xs: xs, ys: ys, nx: nx, ny: ny };
    }

    /** Jas každého bodu (0–255) ako priemer pásu ±band px; súradnice mimo plátna sa pritlačia na okraj. */
    function sampleLuminance(data, width, height, pts, band, out) {
        const maxX = width - 1, maxY = height - 1;
        const n = pts.xs.length;
        const div = 2 * band + 1;
        for (let i = 0; i < n; i++) {
            let sum = 0;
            for (let k = -band; k <= band; k++) {
                let px = Math.round(pts.xs[i] + pts.nx * k);
                let py = Math.round(pts.ys[i] + pts.ny * k);
                if (px < 0) px = 0; else if (px > maxX) px = maxX;
                if (py < 0) py = 0; else if (py > maxY) py = maxY;
                const idx = (py * width + px) * 4;
                sum += data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114;
            }
            out[i] = sum / div;
        }
        return out;
    }

    /** Podiel bodov, ktoré sa od základu líšia o viac než threshold; mask dostane 1/0 na kreslenie. */
    function compareToBaseline(lum, baseline, threshold, mask) {
        const n = lum.length;
        if (!n) return 0;
        let changed = 0;
        for (let i = 0; i < n; i++) {
            const hit = Math.abs(lum[i] - baseline[i]) > threshold ? 1 : 0;
            if (mask) mask[i] = hit;
            changed += hit;
        }
        return changed / n;
    }

    /** Základ sa pomaly posúva za aktuálnym jasom – vyrovná zmenu svetla, bežca nestihne. */
    function adaptBaseline(baseline, lum, alpha) {
        for (let i = 0; i < baseline.length; i++) {
            baseline[i] += alpha * (lum[i] - baseline[i]);
        }
    }

    /**
     * Stavový automat pretnutia: čaká na CONSECUTIVE_FRAMES snímok so zmenou po sebe a potom
     * vráti čas PRVEJ snímky série (odpočíta tak oneskorenie potvrdenia). Ďalšie pretnutie
     * uzná až po DEBOUNCE_MS a až keď zmena medzitým aspoň raz zmizla.
     */
    function createDetector() {
        let hits = 0, firstHitAt = 0, lastTriggerAt = -Infinity;
        return {
            reset: function () { hits = 0; firstHitAt = 0; lastTriggerAt = -Infinity; },
            step: function (fraction, armed, now) {
                if (!armed || !(fraction >= TRIGGER_FRACTION)) { hits = 0; return null; }
                if (hits === 0) firstHitAt = now;
                hits++;
                if (hits === CONSECUTIVE_FRAMES && now - lastTriggerAt > DEBOUNCE_MS) {
                    lastTriggerAt = now;
                    return firstHitAt;
                }
                return null;
            },
        };
    }

    const core = {
        formatTime: formatTime, samplePoints: samplePoints, sampleLuminance: sampleLuminance,
        compareToBaseline: compareToBaseline, adaptBaseline: adaptBaseline, createDetector: createDetector,
        SAMPLES: SAMPLES, BAND: BAND, TRIGGER_FRACTION: TRIGGER_FRACTION,
        CONSECUTIVE_FRAMES: CONSECUTIVE_FRAMES, DEBOUNCE_MS: DEBOUNCE_MS, PROC_WIDTH: PROC_WIDTH,
    };
    if (typeof module === 'object' && module !== null && module.exports) module.exports = core;
    if (typeof document === 'undefined') return;

    // ── DOM ──────────────────────────────────────────────────────────────────
    const $ = function (id) { return document.getElementById(id); };
    const root = $('stopky');
    if (!root) return;

    const loggedIn = root.dataset.loggedIn === '1';
    const otherDiscipline = root.dataset.otherDiscipline || 'iné';

    const camWrap = $('cam-wrap'), video = $('cam'), overlay = $('overlay');
    const camMsg = $('cam-msg'), camMsgTitle = camMsg.querySelector('p'), camMsgText = $('cam-msg-text');
    const stateEls = { ready: $('st-ready'), running: $('st-running'), crossed: $('st-crossed') };
    const activityPct = $('activity-pct'), activityFill = $('activity-fill');
    const cellStatus = $('cell-status'), precisionEl = $('precision');
    const btnCam = $('btn-cam'), btnCalib = $('btn-calib'), btnFlip = $('btn-flip'), btnSound = $('btn-sound');
    const thresholdInput = $('threshold'), thresholdVal = $('threshold-val');
    const guardInput = $('guard'), guardVal = $('guard-val');
    const actionSelect = $('cell-action');
    const timeEl = $('time'), timeLabel = $('time-label');
    const btnStart = $('btn-start'), btnStop = $('btn-stop'), btnLap = $('btn-lap'), btnReset = $('btn-reset');
    const lapsList = $('laps'), lapsEmpty = $('laps-empty'), lapsCount = $('laps-count');
    const saveBox = $('save-box');
    const btnFile = $('btn-file'), fileInput = $('file-input'), savedSelect = $('saved-video');
    const fileControls = $('file-controls'), fileName = $('file-name'), fileTime = $('file-time');
    const btnPlay = $('btn-play'), btnPrev = $('btn-prev'), btnNext = $('btn-next');
    const seekInput = $('seek'), speedSelect = $('speed');

    const mediaOk = !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

    // ── Nastavenia si prehliadač pamätá – tréner postaví kameru raz ──────────
    const STORAGE_KEY = 'atletcoach.stopky';
    function loadSettings() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch (e) { return {}; }
    }
    function saveSettings() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({
                line: line, threshold: thresholdInput.value, guard: guardInput.value, action: actionSelect.value,
            }));
        } catch (e) { /* súkromný režim alebo plné úložisko – nevadí */ }
    }
    function validLine(l) {
        return !!l && ['x1', 'y1', 'x2', 'y2'].every(function (k) {
            return typeof l[k] === 'number' && l[k] >= 0 && l[k] <= 1;
        });
    }
    const saved = loadSettings();
    const line = validLine(saved.line) ? saved.line : Object.assign({}, DEFAULT_LINE);
    if (saved.threshold) thresholdInput.value = saved.threshold;
    if (saved.guard !== undefined) guardInput.value = saved.guard;
    if (saved.action && actionSelect.querySelector('option[value="' + saved.action + '"]')) actionSelect.value = saved.action;

    function thresholdValue() { return Number(thresholdInput.value) || 40; }
    function guardMs() { return (Number(guardInput.value) || 0) * 1000; }

    // ── Časomiera ────────────────────────────────────────────────────────────
    const timer = {
        running: false,
        origin: 0,          // performance.now() zodpovedajúce času 0 (posúva sa pri pokračovaní)
        elapsed: 0,         // ms po zastavení
        runStartedAt: 0,    // posledné stlačenie Štart – od neho beží ochranná doba
        laps: [],           // celkové časy medzičasov v ms
        stopMode: null,     // 'rucne' | 'fotobunka'
    };
    let lastTimeText = '';

    // Zdroj obrazu: 'camera' = živá kamera (hodiny = performance.now()),
    // 'file' = video zo záznamu (hodiny = časová stopa videa, pauza ich zastaví).
    let source = 'camera';
    function mediaNow() { return video.currentTime * 1000; }
    function clockNow() { return source === 'file' ? mediaNow() : performance.now(); }
    function byPhotocell() { return timer.stopMode === 'fotobunka' || timer.stopMode === 'zaznam'; }

    function elapsedAt(now) { return timer.running ? Math.max(0, now - timer.origin) : timer.elapsed; }

    function renderTime(now) {
        const text = formatTime(elapsedAt(now === undefined ? clockNow() : now));
        if (text !== lastTimeText) { timeEl.textContent = text; lastTimeText = text; }
    }

    function startTimer(at) {
        if (timer.running) return;
        let now = Number.isFinite(at) ? at : clockNow();
        if (source === 'file') {
            // Záznam: Štart znamená „tento snímok je čas 0“ – vždy odznova, nie pokračovanie.
            // Čas 0 je presná časová značka zobrazeného snímku (z requestVideoFrameCallback),
            // nie poloha prehrávača, ktorá pri krokovaní leží kdesi vnútri snímku.
            if (cell.lastMediaTime >= 0 && Math.abs(cell.lastMediaTime * 1000 - now) <= 2 * frameStepMs()) {
                now = cell.lastMediaTime * 1000;
            }
            timer.elapsed = 0;
            timer.laps = [];
            renderLaps();
            if (video.paused) video.play().catch(function () { /* prehrá sa po interakcii */ });
        }
        timer.origin = now - timer.elapsed;
        timer.runStartedAt = now;
        timer.running = true;
        timer.stopMode = null;
        detector.reset();
        stopSoundStart();
        setState('running');
        updateControls();
        hideSaveBox();
    }

    function stopTimer(mode, at) {
        if (!timer.running) return;
        const now = clockNow();
        const t = Number.isFinite(at) ? Math.min(at, now) : now;
        timer.elapsed = Math.max(0, t - timer.origin);
        timer.running = false;
        timer.stopMode = source === 'file' ? (mode === 'fotobunka' ? 'zaznam' : 'zaznam-rucne') : mode;
        if (source === 'file' && !video.paused) video.pause();   // snímok cieľa ostane na obraze
        renderTime(now);
        setState(mode === 'fotobunka' ? 'crossed' : 'ready');
        updateControls();
        showSaveBox();
    }

    function addLap(at) {
        if (!timer.running) return;
        const now = clockNow();
        const t = Number.isFinite(at) ? Math.min(at, now) : now;
        let total = Math.max(0, t - timer.origin);
        const prev = timer.laps.length ? timer.laps[timer.laps.length - 1] : 0;
        if (total < prev) total = prev;   // medzičasy nikdy necúvajú
        timer.laps.push(total);
        renderLaps();
    }

    function resetTimer() {
        if (timer.running) return;
        timer.elapsed = 0;
        timer.laps = [];
        timer.stopMode = null;
        detector.reset();
        renderTime();
        renderLaps();
        setState('ready');
        updateControls();
        hideSaveBox();
    }

    function renderLaps() {
        lapsList.querySelectorAll('li.lap').forEach(function (li) { li.remove(); });
        lapsEmpty.classList.toggle('hidden', timer.laps.length > 0);
        lapsCount.textContent = String(timer.laps.length);
        timer.laps.forEach(function (total, i) {
            const split = total - (i ? timer.laps[i - 1] : 0);
            const li = document.createElement('li');
            li.className = 'lap';
            li.appendChild(span('lap-idx', String(i + 1)));
            li.appendChild(span('lap-split', '+' + formatTime(split)));
            li.appendChild(span('lap-total', formatTime(total)));
            lapsList.appendChild(li);
        });
        lapsList.scrollTop = lapsList.scrollHeight;
    }

    function span(cls, text) {
        const el = document.createElement('span');
        el.className = cls;
        el.textContent = text;
        return el;
    }

    function updateControls() {
        btnStart.disabled = timer.running;
        btnStop.disabled = !timer.running;
        btnLap.disabled = !timer.running;
        btnReset.disabled = timer.running || (timer.elapsed === 0 && timer.laps.length === 0);
        btnStart.textContent = (!timer.running && timer.elapsed > 0 && source !== 'file') ? 'Pokračovať' : 'Štart';
        timeEl.classList.toggle('running', timer.running);
        timeLabel.textContent = timer.running ? 'Beží'
            : byPhotocell() ? 'Zastavené fotobunkou'
            : timer.stopMode ? 'Zastavené ručne' : 'Čas';
    }

    function setState(name) {
        Object.keys(stateEls).forEach(function (key) {
            const on = key === name;
            stateEls[key].classList.toggle('on', on);
            stateEls[key].classList.toggle('volt', on && name !== 'ready');
        });
    }

    // ── Uloženie výsledku ────────────────────────────────────────────────────
    function showSaveBox() {
        if (!saveBox || timer.elapsed < 10) return;
        saveBox.classList.remove('hidden');
        if (!loggedIn) return;
        $('result_s').value = (timer.elapsed / 1000).toFixed(2);
        $('laps_field').value = timer.laps.map(function (ms) { return (ms / 1000).toFixed(2); }).join(',');
        $('stop_mode').value = timer.stopMode || 'rucne';
        $('precision_ms').value = byPhotocell() ? String(Math.round(measuredFrameMs())) : '';
        $('save-summary').textContent = formatTime(timer.elapsed);
    }

    function hideSaveBox() {
        if (saveBox) saveBox.classList.add('hidden');
    }

    const disciplineSelect = $('discipline');
    if (disciplineSelect) {
        const customBox = $('discipline-custom-box'), customInput = $('discipline_custom');
        const syncDiscipline = function (focus) {
            const other = disciplineSelect.value === otherDiscipline;
            customBox.classList.toggle('hidden', !other);
            customInput.required = other;
            if (other && focus) customInput.focus();
        };
        disciplineSelect.addEventListener('change', function () { syncDiscipline(true); });
        syncDiscipline(false);
        saveBox.addEventListener('submit', function (e) {
            // Po resete je čas 0 – server by to odmietol, radšej to nepustíme.
            if (!Number($('result_s').value)) { e.preventDefault(); hideSaveBox(); }
        });
    }

    // ── Kamera ───────────────────────────────────────────────────────────────
    let stream = null;
    let camOn = false;                      // je pripojený zdroj obrazu (kamera alebo záznam)
    let fileUrl = null;                     // blob URL vybraného súboru – uvoľní sa pri výmene
    let camGen = 0;                         // generácia zdroja – starý snímkový callback sa po prepnutí zahodí
    let facing = 'environment';
    const useRvfc = 'requestVideoFrameCallback' in HTMLVideoElement.prototype;

    function setCamMessage(title, text) {
        camMsgTitle.textContent = title;
        camMsgText.textContent = text;
    }

    function cameraErrorText(err) {
        const name = err && err.name || '';
        switch (name) {
            case 'NotAllowedError':
            case 'PermissionDeniedError':
            case 'SecurityError':
                return 'Prístup ku kamere bol zamietnutý. Povoľ ho v prehliadači (ikona kamery v adresnom riadku) a skús znova. Ručné stopky fungujú aj tak.';
            case 'NotFoundError':
            case 'DevicesNotFoundError':
                return 'Žiadna kamera sa nenašla. Stopky fungujú ručne – Štart a Stop.';
            case 'NotReadableError':
            case 'TrackStartError':
                return 'Kameru práve používa iná aplikácia. Zavri ju a skús znova.';
            case 'OverconstrainedError':
                return 'Kamera nepodporuje požadované nastavenie. Skús ju prepnúť alebo použi ručné stopky.';
            default:
                return 'Kameru sa nepodarilo spustiť' + (name ? ' (' + name + ')' : '') + '. Stopky fungujú ručne.';
        }
    }

    async function startCamera() {
        if (!mediaOk) return;
        stopCamera();
        btnCam.disabled = true;
        setCamMessage('Zapínam kameru…', 'Prehliadač sa pýta na povolenie. Bez kamery fungujú ručné stopky.');
        // Zadná kamera a HD sú len želanie; keď ich zariadenie nemá, vezmeme akúkoľvek.
        const attempts = [
            { video: { facingMode: facing, width: { ideal: 1280 } }, audio: false },
            { video: true, audio: false },
        ];
        let err = null, s = null;
        for (let i = 0; i < attempts.length && !s; i++) {
            try {
                s = await navigator.mediaDevices.getUserMedia(attempts[i]);
            } catch (e) {
                err = e;
                if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) break;
            }
        }
        btnCam.disabled = false;
        if (!s) {
            setCamMessage('Kamera nie je dostupná', cameraErrorText(err));
            camWrap.classList.add('cam-off');
            return;
        }
        attachStream(s);
    }

    function attachStream(s) {
        releaseFile();
        source = 'camera';
        stream = s;
        camGen++;
        camOn = true;
        video.srcObject = s;
        video.play().catch(function () { /* autoplay muted – prehrá sa po loadedmetadata */ });
        camWrap.classList.add('cam-on');
        camWrap.classList.remove('cam-off');
        btnCam.textContent = 'Vypnúť kameru';
        btnCalib.disabled = false;
        cell.baseline = null;
        cell.autoCalibrate = true;
        cell.frames = 0;
        cell.frameMs = 0;
        s.getVideoTracks().forEach(function (track) {
            track.addEventListener('ended', function () {
                if (stream === s) stopCamera('Kamera sa odpojila. Zapni ju znova alebo meraj ručne.');
            });
        });
        if (mediaOk) btnSound.disabled = false;
        if (useRvfc) video.requestVideoFrameCallback(onVideoFrame.bind(null, camGen));
        navigator.mediaDevices.enumerateDevices().then(function (list) {
            const cams = list.filter(function (d) { return d.kind === 'videoinput'; });
            const multi = cams.length > 1;
            btnFlip.classList.toggle('hidden', !multi);
            btnFlip.disabled = !multi;
        }).catch(function () { /* bez zoznamu zariadení sa len neukáže prepínanie */ });
        overlayDirty = true;
    }

    function stopCamera(reason) {
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
        releaseFile();
        camOn = false;
        camGen++;
        video.srcObject = null;
        camWrap.classList.remove('cam-on');
        camWrap.classList.add('cam-off');
        camWrap.style.aspectRatio = '';
        btnCam.textContent = 'Zapnúť kameru';
        btnCalib.disabled = true;
        btnFlip.disabled = true;
        cell.baseline = null;
        cell.fraction = 0;
        cell.mask.fill(0);
        detector.reset();
        setCamMessage('Kamera je vypnutá', reason ||
            'Zapni kameru alebo nahraj video zo záznamu a potiahni cieľovú čiaru na miesto, kde bežec končí. Stopky fungujú aj bez kamery – ručne.');
        overlayDirty = true;
    }

    video.addEventListener('loadedmetadata', function () {
        if (!camOn || !video.videoWidth) return;
        camWrap.style.aspectRatio = video.videoWidth + ' / ' + video.videoHeight;
        ensureProcSize();
        overlayDirty = true;
    });

    // ── Video zo záznamu ─────────────────────────────────────────────────────
    /** Uvoľní záznam (súbor aj URL) – volá sa pri zapnutí kamery a pri výmene videa. */
    function releaseFile() {
        if (source !== 'file' && !fileUrl) return;
        video.pause();
        video.removeAttribute('src');
        video.load();
        if (fileUrl) { URL.revokeObjectURL(fileUrl); fileUrl = null; }
        source = 'camera';
        fileControls.classList.add('hidden');
        if (savedSelect) savedSelect.value = '';
    }

    /** Pripojí video zo súboru (blob URL) alebo z /media/<súbor>; kamera sa vypne. */
    function attachFile(url, name, isBlob) {
        if (timer.running) stopTimer('rucne');
        stopCamera();                       // vypne kameru aj predošlý záznam
        source = 'file';
        fileUrl = isBlob ? url : null;
        camGen++;
        camOn = true;
        video.playbackRate = Number(speedSelect.value) || 1;
        video.src = url;
        video.load();
        camWrap.classList.add('cam-on');
        camWrap.classList.remove('cam-off');
        btnCalib.disabled = false;
        btnFlip.classList.add('hidden');
        btnFlip.disabled = true;
        stopSoundStart();
        btnSound.disabled = true;           // štart na zvuk z mikrofónu nemá pri zázname zmysel
        cell.baseline = null;
        cell.autoCalibrate = true;
        cell.frames = 0;
        cell.frameMs = 0;
        cell.minFrameMs = 0;
        cell.lastMediaTime = -1;
        cell.note = null;
        fileName.textContent = name || 'záznam';
        seekInput.value = '0';
        fileControls.classList.remove('hidden');
        setPlayLabel();
        if (useRvfc) video.requestVideoFrameCallback(onVideoFrame.bind(null, camGen));
        overlayDirty = true;
    }

    function setPlayLabel() { btnPlay.textContent = video.paused ? 'Prehrať' : 'Pauza'; }

    /** Dĺžka jedného snímku záznamu – najkratší interval nameraný počas prehrávania; kým sa nehralo, 1/30 s. */
    function frameStepMs() { return cell.minFrameMs > 0 ? cell.minFrameMs : 1000 / 30; }

    /**
     * Krok o jeden snímok po mriežke snímok: cieľ je štvrtina do vnútra cieľového
     * snímku, aby hľadanie neskončilo na hranici dvoch snímok (a nezáviselo od
     * toho, či je aktuálna poloha presne na značke snímku alebo kdesi vnútri).
     */
    function stepFrame(dir) {
        if (source !== 'file' || !camOn) return;
        video.pause();
        const stepS = frameStepMs() / 1000;
        const k = Math.floor(video.currentTime / stepS + 0.01);
        const dur = Number.isFinite(video.duration) ? video.duration : Infinity;
        video.currentTime = Math.min(dur, Math.max(0, (k + dir) * stepS + stepS * 0.25));
    }

    function togglePlay() {
        if (source !== 'file' || !camOn) return;
        if (video.paused) video.play().catch(function () { /* formát / autoplay */ });
        else video.pause();
    }

    let seekDrag = false;
    seekInput.addEventListener('input', function () {
        if (source !== 'file' || !Number.isFinite(video.duration)) return;
        seekDrag = true;
        video.pause();
        video.currentTime = Number(seekInput.value) / 1000 * video.duration;
    });
    seekInput.addEventListener('change', function () { seekDrag = false; });
    speedSelect.addEventListener('change', function () { video.playbackRate = Number(speedSelect.value) || 1; });
    btnPlay.addEventListener('click', togglePlay);
    btnPrev.addEventListener('click', function () { stepFrame(-1); });
    btnNext.addEventListener('click', function () { stepFrame(1); });
    video.addEventListener('play', setPlayLabel);
    video.addEventListener('pause', setPlayLabel);
    video.addEventListener('loadeddata', function () {
        // Prvý snímok záznamu kalibruje základ hneď, aj keď sa ešte neprehráva.
        if (source === 'file' && camOn) processFrame(performance.now(), mediaNow(), mediaNow());
    });
    video.addEventListener('seeked', function () {
        // Bez requestVideoFrameCallback by sa krokovaný snímok nespracoval.
        if (source === 'file' && camOn && !useRvfc && video.paused) processFrame(performance.now(), mediaNow(), mediaNow());
    });
    video.addEventListener('ended', function () {
        if (source !== 'file') return;
        if (timer.running) {
            stopTimer('rucne');
            cell.note = { text: 'záznam skončil bez pretnutia cieľa', until: performance.now() + 5000 };
        }
    });
    video.addEventListener('error', function () {
        if (source !== 'file') return;
        stopCamera('Toto video sa nedá prehrať. Prehliadač formát nepodporuje – skús MP4 (H.264), alebo iný prehliadač.');
    });
    btnFile.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () {
        const f = fileInput.files && fileInput.files[0];
        if (!f) return;
        attachFile(URL.createObjectURL(f), f.name, true);
        fileInput.value = '';               // ten istý súbor sa dá vybrať znova
    });
    if (savedSelect) {
        savedSelect.addEventListener('change', function () {
            const opt = savedSelect.options[savedSelect.selectedIndex];
            if (opt && opt.value) attachFile(opt.value, opt.textContent.trim(), false);
        });
    }

    // ── Fotobunka ────────────────────────────────────────────────────────────
    const proc = document.createElement('canvas');
    const pctx = proc.getContext('2d', { willReadFrequently: true });
    const detector = createDetector();
    const cell = {
        baseline: null,                     // Float32Array jasu z kalibrácie
        lum: new Float32Array(SAMPLES),
        mask: new Uint8Array(SAMPLES),      // 1 = bod sa práve líši od základu (kreslí sa)
        fraction: 0,
        autoCalibrate: false,               // prvá snímka po zapnutí / presune čiary kalibruje sama
        calibrateRequested: false,
        lastProcess: 0,
        frameMs: 0,                         // nameraný interval snímok kamery (EMA)
        minFrameMs: 0,                      // najkratší interval – dĺžka jedného snímku záznamu (krokovanie)
        frames: 0,
        lastMediaTime: -1,
        note: null,                         // krátka správa do stavu fotobunky {text, until}
        flashUntil: 0,                      // čiara na chvíľu zbelie pri pretnutí
        crossedUntil: 0,                    // pri medzičase svieti CIEĽ PRETNUTÝ len chvíľu
    };
    let pointsCache = null;
    let overlayDirty = true;

    function ensureProcSize() {
        const vw = video.videoWidth, vh = video.videoHeight;
        if (!vw || !vh) return false;
        const ph = Math.max(1, Math.round(PROC_WIDTH * vh / vw));
        if (proc.width !== PROC_WIDTH || proc.height !== ph) {
            proc.width = PROC_WIDTH;
            proc.height = ph;
            pointsCache = null;
            cell.baseline = null;           // iný pomer strán = iné body, starý základ neplatí
            cell.autoCalibrate = true;
        }
        return true;
    }

    function points() {
        const key = [line.x1, line.y1, line.x2, line.y2, proc.width, proc.height].join('|');
        if (!pointsCache || pointsCache.key !== key) {
            pointsCache = samplePoints(line, proc.width, proc.height, SAMPLES);
            pointsCache.key = key;
        }
        return pointsCache;
    }

    function requestCalibration() {
        cell.calibrateRequested = true;
    }

    function measuredFrameMs() {
        return (cell.frames >= 10 && cell.frameMs > 0) ? cell.frameMs : 1000 / 30;
    }

    function noteFrameInterval(dt) {
        if (!(dt > 4 && dt < 500)) return;
        cell.frameMs = cell.frameMs ? cell.frameMs * 0.9 + dt * 0.1 : dt;
        cell.minFrameMs = cell.minFrameMs ? Math.min(cell.minFrameMs, dt) : dt;
        cell.frames++;
    }

    /**
     * Snímka cez requestVideoFrameCallback – presný čas snímky a jej skutočný interval.
     * Pri zázname je časom snímky jej poloha vo videu (mediaTime), nie hodiny prehliadača.
     */
    function onVideoFrame(gen, now, meta) {
        if (gen !== camGen || !camOn) return;
        const hasMedia = !!(meta && Number.isFinite(meta.mediaTime));
        if (hasMedia) {
            // Interval snímok sa meria len pri prehrávaní – pri krokovaní prehliadač
            // zlučuje viac hľadaní do jedného a rozdiel by nebol jeden snímok.
            if (cell.lastMediaTime >= 0 && (source !== 'file' || !video.paused)) {
                noteFrameInterval((meta.mediaTime - cell.lastMediaTime) * 1000);
            }
            cell.lastMediaTime = meta.mediaTime;
        }
        let frameTime = now, clockT = now;
        if (source === 'file') {
            frameTime = hasMedia ? meta.mediaTime * 1000 : mediaNow();
            clockT = frameTime;
        } else if (meta && Number.isFinite(meta.captureTime) && now - meta.captureTime >= 0 && now - meta.captureTime < 1000) {
            // captureTime je na rovnakých hodinách ako performance.now(); ak je rozumný, odpočíta oneskorenie kamery.
            frameTime = meta.captureTime;
        }
        processFrame(now, frameTime, clockT);
        video.requestVideoFrameCallback(onVideoFrame.bind(null, gen));
    }

    /** now = hodiny prehliadača (efekty), frameTime = čas snímky, clockT = hodiny stopiek pre ochrannú dobu. */
    function processFrame(now, frameTime, clockT) {
        if (!camOn || video.readyState < 2 || !ensureProcSize()) return;
        pctx.drawImage(video, 0, 0, proc.width, proc.height);
        let data;
        try {
            data = pctx.getImageData(0, 0, proc.width, proc.height).data;
        } catch (e) {
            return;                         // pri kamere sa to nestáva, ale nechceme padnúť v slučke
        }
        sampleLuminance(data, proc.width, proc.height, points(), BAND, cell.lum);

        if (cell.calibrateRequested || cell.autoCalibrate || !cell.baseline) {
            if (cell.calibrateRequested || cell.autoCalibrate) {
                cell.baseline = Float32Array.from(cell.lum);
                cell.calibrateRequested = false;
                cell.autoCalibrate = false;
                cell.fraction = 0;
                cell.mask.fill(0);
                detector.reset();
                overlayDirty = true;
            }
            return;
        }

        const fraction = compareToBaseline(cell.lum, cell.baseline, thresholdValue(), cell.mask);
        cell.fraction = fraction;
        if (fraction < QUIET_FRACTION) adaptBaseline(cell.baseline, cell.lum, BASELINE_ALPHA);

        const armed = timer.running && (clockT - timer.runStartedAt) >= guardMs();
        const hitAt = detector.step(fraction, armed, frameTime);
        if (hitAt !== null) onCrossing(hitAt, now);
        overlayDirty = true;
    }

    function onCrossing(hitAt, now) {
        cell.flashUntil = now + 500;
        if (actionSelect.value === 'lap') {
            addLap(hitAt);
            cell.crossedUntil = now + 900;
            setState('crossed');
        } else {
            stopTimer('fotobunka', hitAt);
        }
    }

    function cellStatusText(now) {
        if (cell.note && now < cell.note.until) return cell.note.text;
        if (source === 'file' && camOn) {
            if (!cell.baseline) return 'čaká na kalibráciu';
            if (!timer.running) return byPhotocell() ? 'pretnutá' : (video.paused ? 'záznam pozastavený' : 'pripravená');
            if (mediaNow() - timer.runStartedAt < guardMs()) return 'ochranná doba';
            return video.paused ? 'záznam pozastavený' : 'aktívna';
        }
        if (!mediaOk) return 'kamera nedostupná';
        if (!camOn) return 'kamera vypnutá';
        if (!cell.baseline) return 'čaká na kalibráciu';
        if (!timer.running) return byPhotocell() ? 'pretnutá' : 'pripravená';
        if (now - timer.runStartedAt < guardMs()) return 'ochranná doba';
        return 'aktívna';
    }

    // ── Cieľová čiara na plátne ──────────────────────────────────────────────
    let drag = null;

    /** Obdĺžnik, v ktorom sa v <video> naozaj kreslí obraz (object-fit: contain), v CSS px plátna. */
    function frameRect() {
        const w = overlay.clientWidth, h = overlay.clientHeight;
        const vw = video.videoWidth, vh = video.videoHeight;
        if (!camOn || !vw || !vh || !w || !h) return { x: 0, y: 0, w: w, h: h };
        const scale = Math.min(w / vw, h / vh);
        const cw = vw * scale, ch = vh * scale;
        return { x: (w - cw) / 2, y: (h - ch) / 2, w: cw, h: ch };
    }

    function toPx(rx, ry) {
        const f = frameRect();
        return { x: f.x + rx * f.w, y: f.y + ry * f.h };
    }

    function toRel(p, clamp) {
        const f = frameRect();
        let x = f.w ? (p.x - f.x) / f.w : 0;
        let y = f.h ? (p.y - f.y) / f.h : 0;
        if (clamp !== false) { x = Math.min(1, Math.max(0, x)); y = Math.min(1, Math.max(0, y)); }
        return { x: x, y: y };
    }

    function overlayPoint(e) {
        const r = overlay.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    function distToSegment(p, a, b) {
        const dx = b.x - a.x, dy = b.y - a.y;
        const l2 = dx * dx + dy * dy;
        let t = l2 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0;
        t = Math.max(0, Math.min(1, t));
        return Math.hypot(p.x - (a.x + dx * t), p.y - (a.y + dy * t));
    }

    /** Posunie celú čiaru o (dx, dy) tak, aby ostala v obraze – bez deformácie na okraji. */
    function translateLine(src, dx, dy) {
        dx = Math.max(-Math.min(src.x1, src.x2), Math.min(1 - Math.max(src.x1, src.x2), dx));
        dy = Math.max(-Math.min(src.y1, src.y2), Math.min(1 - Math.max(src.y1, src.y2), dy));
        return { x1: src.x1 + dx, y1: src.y1 + dy, x2: src.x2 + dx, y2: src.y2 + dy };
    }

    function lineLengthPx() {
        const a = toPx(line.x1, line.y1), b = toPx(line.x2, line.y2);
        return Math.hypot(b.x - a.x, b.y - a.y);
    }

    overlay.addEventListener('pointerdown', function (e) {
        if (!camOn || drag) return;
        const p = overlayPoint(e);
        const a = toPx(line.x1, line.y1), b = toPx(line.x2, line.y2);
        let mode;
        if (Math.hypot(p.x - a.x, p.y - a.y) <= 22) mode = 'p1';
        else if (Math.hypot(p.x - b.x, p.y - b.y) <= 22) mode = 'p2';
        else if (distToSegment(p, a, b) <= 14) mode = 'move';
        else mode = 'draw';
        drag = { mode: mode, id: e.pointerId, start: p, last: p, before: Object.assign({}, line), moved: false };
        if (mode === 'draw') {
            const r = toRel(p);
            Object.assign(line, { x1: r.x, y1: r.y, x2: r.x, y2: r.y });
        }
        try { overlay.setPointerCapture(e.pointerId); } catch (err) { /* staršie prehliadače */ }
        e.preventDefault();
        overlayDirty = true;
    });

    overlay.addEventListener('pointermove', function (e) {
        if (!drag || e.pointerId !== drag.id) return;
        const p = overlayPoint(e);
        if (Math.hypot(p.x - drag.start.x, p.y - drag.start.y) > 4) drag.moved = true;
        if (drag.mode === 'p1') {
            const r = toRel(p); line.x1 = r.x; line.y1 = r.y;
        } else if (drag.mode === 'p2' || drag.mode === 'draw') {
            const r = toRel(p); line.x2 = r.x; line.y2 = r.y;
        } else {
            const r0 = toRel(drag.last, false), r1 = toRel(p, false);
            Object.assign(line, translateLine(line, r1.x - r0.x, r1.y - r0.y));
        }
        drag.last = p;
        overlayDirty = true;
    });

    function endDrag(e) {
        if (!drag || (e && e.pointerId !== drag.id)) return;
        if (drag.mode === 'draw' && !drag.moved) {
            // Klepnutie mimo čiary: presuň stred čiary na to miesto, tvar ostane.
            const before = drag.before;
            const r = toRel(drag.last);
            Object.assign(line, translateLine(before, r.x - (before.x1 + before.x2) / 2, r.y - (before.y1 + before.y2) / 2));
        }
        if (lineLengthPx() < MIN_LINE_PX) Object.assign(line, drag.before);   // príliš krátka – vráť predošlú
        drag = null;
        onLineChanged();
    }
    overlay.addEventListener('pointerup', endDrag);
    overlay.addEventListener('pointercancel', endDrag);
    overlay.addEventListener('lostpointercapture', function () { if (drag) endDrag(null); });

    function onLineChanged() {
        pointsCache = null;
        saveSettings();
        overlayDirty = true;
        if (camOn) requestCalibration();    // nové body na čiare = nový základ
    }

    function drawOverlay(now) {
        const dpr = window.devicePixelRatio || 1;
        const w = overlay.clientWidth, h = overlay.clientHeight;
        if (!w || !h) return;
        const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
        if (overlay.width !== pw || overlay.height !== ph) { overlay.width = pw; overlay.height = ph; }
        const ctx = overlay.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        if (!camOn) return;

        const a = toPx(line.x1, line.y1), b = toPx(line.x2, line.y2);
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const nx = -dy / len, ny = dx / len;
        const color = cell.flashUntil > now ? '#ffffff' : VOLT;

        ctx.lineWidth = 2;
        ctx.strokeStyle = color;
        ctx.lineCap = 'butt';
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();

        // Koncové značky kolmo na čiaru; ťahaný koniec dostane plný štvorček.
        const cap = 6;
        [a, b].forEach(function (p, i) {
            ctx.beginPath();
            ctx.moveTo(p.x - nx * cap, p.y - ny * cap);
            ctx.lineTo(p.x + nx * cap, p.y + ny * cap);
            ctx.stroke();
            if (drag && ((i === 0 && drag.mode === 'p1') || (i === 1 && (drag.mode === 'p2' || drag.mode === 'draw')))) {
                ctx.fillStyle = color;
                ctx.fillRect(p.x - 4, p.y - 4, 8, 8);
            }
        });

        // Body, ktoré sa práve líšia od základu – vidno, čo fotobunka „vidí“.
        if (cell.baseline) {
            ctx.fillStyle = '#ffffff';
            for (let i = 0; i < SAMPLES; i++) {
                if (!cell.mask[i]) continue;
                const t = (i + 0.5) / SAMPLES;
                ctx.fillRect(a.x + dx * t - 2, a.y + dy * t - 2, 4, 4);
            }
        }

        // Popis pri hornom konci; ak by vyšiel z obrazu, ide na druhú stranu.
        ctx.font = "500 10px 'IBM Plex Mono', monospace";
        if ('letterSpacing' in ctx) ctx.letterSpacing = '0.14em';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = color;
        const top = a.y <= b.y ? a : b;
        const label = 'CIEĽ';
        const tw = ctx.measureText(label).width;
        let lx = top.x + 8;
        if (lx + tw > w - 4) lx = top.x - 8 - tw;
        const ly = Math.min(h - 8, Math.max(8, top.y + 10));
        ctx.fillText(label, lx, ly);
    }

    if (typeof ResizeObserver === 'function') {
        new ResizeObserver(function () { overlayDirty = true; }).observe(camWrap);
    } else {
        window.addEventListener('resize', function () { overlayDirty = true; });
    }

    // ── Štart na zvuk (tlesknutie / výstrel) ─────────────────────────────────
    const sound = { active: false, ctx: null, analyser: null, stream: null, buf: null, since: 0 };

    async function startSoundStart() {
        if (sound.active || !mediaOk) return;
        btnSound.disabled = true;
        let s;
        try {
            s = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false,
            });
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) throw new Error('AudioContext');
            const ctx = new Ctx();
            if (ctx.state === 'suspended') await ctx.resume();
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 1024;
            ctx.createMediaStreamSource(s).connect(analyser);
            sound.ctx = ctx;
            sound.analyser = analyser;
            sound.stream = s;
            sound.buf = new Float32Array(analyser.fftSize);
            sound.since = performance.now();
            sound.active = true;
            btnSound.textContent = 'Čaká na zvuk… (zrušiť)';
            btnSound.classList.add('listening');
        } catch (e) {
            if (s) s.getTracks().forEach(function (t) { t.stop(); });
            btnSound.textContent = 'Mikrofón nedostupný';
            setTimeout(function () { btnSound.textContent = 'Štart na zvuk'; }, 2500);
        }
        btnSound.disabled = false;
    }

    function stopSoundStart() {
        if (!sound.active) return;
        sound.active = false;
        if (sound.stream) sound.stream.getTracks().forEach(function (t) { t.stop(); });
        if (sound.ctx) sound.ctx.close().catch(function () { /* už zavretý */ });
        sound.ctx = sound.analyser = sound.stream = sound.buf = null;
        btnSound.textContent = 'Štart na zvuk';
        btnSound.classList.remove('listening');
    }

    function pollSound(now) {
        if (!sound.active || now - sound.since < SOUND_WARMUP_MS) return;
        const buf = sound.buf;
        if (sound.analyser.getFloatTimeDomainData) {
            sound.analyser.getFloatTimeDomainData(buf);
        } else {
            // Starší Safari: bajty 0–255 so stredom 128.
            const bytes = new Uint8Array(buf.length);
            sound.analyser.getByteTimeDomainData(bytes);
            for (let i = 0; i < bytes.length; i++) buf[i] = (bytes[i] - 128) / 128;
        }
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        if (rms > SOUND_RMS && !timer.running) startTimer(now);   // startTimer aj vypne počúvanie
    }

    // ── Hlavná slučka: čas, spracovanie snímok (bez rVFC), kreslenie, popisky ─
    let lastPrecisionAt = 0, lastStatus = '', lastPct = -1, lastFileTime = '';

    function tick(now) {
        if (timer.running) renderTime(clockNow());

        if (camOn && !useRvfc && now - cell.lastProcess >= PROCESS_INTERVAL_MS - 1) {
            if (source === 'file') {
                // Bez requestVideoFrameCallback: spracuj len nový snímok, čas z videa.
                const m = video.currentTime;
                if (m !== cell.lastMediaTime) {
                    if (cell.lastMediaTime >= 0) noteFrameInterval((m - cell.lastMediaTime) * 1000);
                    cell.lastMediaTime = m;
                    cell.lastProcess = now;
                    processFrame(now, m * 1000, m * 1000);
                }
            } else {
                if (cell.lastProcess) noteFrameInterval(now - cell.lastProcess);
                cell.lastProcess = now;
                processFrame(now, now, now);
            }
        }

        if (source === 'file' && camOn) {
            const dur = Number.isFinite(video.duration) ? video.duration : 0;
            const text = formatTime(mediaNow()) + ' / ' + formatTime(dur * 1000);
            if (text !== lastFileTime) { lastFileTime = text; fileTime.textContent = text; }
            if (!seekDrag && dur > 0) {
                const pos = String(Math.round(video.currentTime / dur * 1000));
                if (seekInput.value !== pos) seekInput.value = pos;
            }
        }

        pollSound(now);

        if (cell.crossedUntil && now > cell.crossedUntil) {
            cell.crossedUntil = 0;
            if (timer.running) setState('running');
        }

        if (overlayDirty || cell.flashUntil > now || drag) { drawOverlay(now); overlayDirty = false; }

        const pct = camOn && cell.baseline ? Math.round(cell.fraction * 100) : 0;
        if (pct !== lastPct) {
            lastPct = pct;
            activityFill.style.width = pct + '%';
            activityFill.classList.toggle('hot', cell.fraction >= TRIGGER_FRACTION);
            activityPct.textContent = pct + ' %';
        }
        const status = cellStatusText(now);
        if (status !== lastStatus) { lastStatus = status; cellStatus.textContent = status; }

        if (now - lastPrecisionAt > 1000) {
            lastPrecisionAt = now;
            const ms = measuredFrameMs();
            const text = camOn && cell.frames >= 10
                ? 'Presnosť fotobunky: ±' + Math.round(ms) + ' ms pri ' + Math.round(1000 / ms) + ' fps ' + (source === 'file' ? 'zázname' : 'kamere') + ' (namerané)'
                : 'Presnosť fotobunky: ±33 ms pri 30 fps kamere (odhad, kým kamera nebeží)';
            if (text !== precisionEl.textContent) precisionEl.textContent = text;
        }

        requestAnimationFrame(tick);
    }

    // ── Ovládanie ────────────────────────────────────────────────────────────
    btnStart.addEventListener('click', function () { startTimer(); });
    btnStop.addEventListener('click', function () { stopTimer('rucne'); });
    btnLap.addEventListener('click', function () { addLap(); });
    btnReset.addEventListener('click', function () { resetTimer(); });
    btnCam.addEventListener('click', function () { (camOn && source === 'camera') ? stopCamera() : startCamera(); });
    btnFlip.addEventListener('click', function () {
        facing = facing === 'environment' ? 'user' : 'environment';
        startCamera();
    });
    btnCalib.addEventListener('click', function () { requestCalibration(); });
    btnSound.addEventListener('click', function () { sound.active ? stopSoundStart() : startSoundStart(); });

    function syncSliders() {
        thresholdVal.textContent = String(thresholdValue());
        guardVal.textContent = (Number(guardInput.value) || 0).toFixed(1).replace('.', ',') + ' s';
    }
    [thresholdInput, guardInput].forEach(function (el) {
        el.addEventListener('input', function () { syncSliders(); saveSettings(); });
    });
    actionSelect.addEventListener('change', saveSettings);

    // Klávesy: medzerník štart/stop, L medzičas, R reset. V textových poliach nič.
    function inTextField(target) {
        if (!target) return false;
        const tag = (target.tagName || '').toLowerCase();
        if (tag === 'textarea' || tag === 'select' || target.isContentEditable) return true;
        return tag === 'input' && !['range', 'checkbox', 'radio', 'button', 'submit'].includes(target.type);
    }
    document.addEventListener('keydown', function (e) {
        if (e.repeat || e.altKey || e.ctrlKey || e.metaKey || inTextField(e.target)) return;
        if (e.code === 'Space') {
            e.preventDefault();               // inak by medzerník aj rolovalo aj „kliklo“ na tlačidlo
            if (timer.running) stopTimer('rucne'); else startTimer();
        } else if (e.key === 'l' || e.key === 'L') {
            addLap();
        } else if (e.key === 'r' || e.key === 'R') {
            resetTimer();
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            if (source === 'file' && camOn) { e.preventDefault(); stepFrame(e.key === 'ArrowLeft' ? -1 : 1); }
        } else if (e.key === 'p' || e.key === 'P') {
            togglePlay();
        }
    });
    document.addEventListener('keyup', function (e) {
        if (e.code === 'Space' && !inTextField(e.target)) e.preventDefault();
    });

    // ── Štart ────────────────────────────────────────────────────────────────
    if (!mediaOk) {
        setCamMessage('Kamera nie je dostupná', (window.isSecureContext
            ? 'Tento prehliadač nepodporuje prístup ku kamere. Stopky fungujú ručne – Štart a Stop.'
            : 'Prehliadač pustí kameru len cez zabezpečené spojenie. Otvor stránku cez https, alebo priamo na tomto počítači cez http://localhost. Ručné stopky fungujú aj tak.')
            + ' Fotobunka funguje aj nad videom zo záznamu.');
        camWrap.classList.add('cam-off');
        btnCam.disabled = true;
        btnSound.disabled = true;
    } else {
        btnSound.disabled = false;
    }
    syncSliders();
    renderTime();
    renderLaps();
    setState('ready');
    updateControls();
    requestAnimationFrame(tick);
})();
