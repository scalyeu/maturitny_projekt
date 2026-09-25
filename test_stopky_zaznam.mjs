/*
 * test_stopky_zaznam.mjs
 * ======================
 * Stopky s fotobunkou zo záznamu v skutočnom prehliadači (headless Google Chrome
 * cez DevTools protokol, bez závislostí okrem Node 22+ a Chromu).
 *
 * Vyrobí sa syntetické video (svetlý pruh ide zľava doprava a pretne cieľovú
 * čiaru x = 0,6 presne v 3,406 s), spustí sa Flask na voľnom porte, video sa
 * nahrá do stopiek cez <input type="file"> a overí sa:
 *   - záznam sa načíta pozastavený, prvý snímok kalibruje fotobunku,
 *   - krokovanie po snímkach ide po 1/30 s,
 *   - Štart na snímku 1,0 s + fotobunka zastaví čas na 2,40 s (±1 snímok),
 *   - pauza prehrávania zmrazí čas, spomalené prehrávanie dá rovnaký čas,
 *   - koniec záznamu bez pretnutia zastaví čas ručne s hlásením,
 *   - zapnutie kamery záznam uvoľní.
 *
 * Spustenie:   node test_stopky_zaznam.mjs        (voliteľne CHROME=/cesta/k/chrome)
 */
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(fileURLToPath(import.meta.url));
const PY = existsSync(join(ROOT, 'venv/bin/python')) ? join(ROOT, 'venv/bin/python') : 'python3';
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const APP_PORT = 5099, CDP_PORT = 9339;
const APP = `http://127.0.0.1:${APP_PORT}/stopky`;
const WORK = mkdtempSync(join(tmpdir(), 'stopky-test-'));
const VIDEO = join(WORK, 'run.mp4');

let ok = true;
function check(cond, msg) { console.log((cond ? '  [OK]   ' : '  [CHYBA] ') + msg); ok = ok && !!cond; }

if (!existsSync(CHROME)) {
    console.log(`Chrome sa nenašiel (${CHROME}) – test preskočený. Nastav CHROME=/cesta/k/chrome.`);
    process.exit(0);
}

// 1. syntetický záznam: pruh stojí do 1,0 s pri x = 0,1, potom ide rovnomerne a stred dosiahne x = 0,6 v 3,5 s
execFileSync(PY, ['-c', `
import cv2, numpy as np
fps, w, h, n = 30, 640, 360, 180
x0, x_line, t0, t_cross = 0.10, 0.60, 1.0, 3.5
speed = (x_line - x0) / (t_cross - t0)
wr = cv2.VideoWriter(${JSON.stringify(VIDEO)}, cv2.VideoWriter_fourcc(*"avc1"), fps, (w, h))
assert wr.isOpened(), "VideoWriter avc1"
for i in range(n):
    t = i / fps
    fr = np.full((h, w, 3), 24, np.uint8)
    px = int((x0 + speed * max(0.0, t - t0)) * w)
    cv2.rectangle(fr, (px - 12, 60), (px + 12, h - 60), (230, 230, 230), -1)
    wr.write(fr)
wr.release()
`]);
const EXPECTED_S = 3.406 - 1.0;          // predná hrana pruhu (12 px) dosiahne čiaru v 3,406 s

// 2. Flask
const flask = spawn(PY, ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(ROOT)}); from app import app; app.run(host="127.0.0.1", port=${APP_PORT}, debug=False, use_reloader=False)`],
    { cwd: ROOT, stdio: 'ignore' });
let chrome = null;
function stopProcesses() {
    try { flask.kill(); } catch (e) { /* už skončil */ }
    if (chrome) { try { chrome.kill(); } catch (e) { /* už skončil */ } }
}
process.on('exit', stopProcesses);

try {
    let up = false;
    for (let i = 0; i < 80 && !up; i++) {
        try { up = (await fetch(APP)).status === 200; } catch (e) { await sleep(250); }
    }
    if (!up) throw new Error('Flask na porte ' + APP_PORT + ' nenabehol');

    // 3. Chrome + DevTools
    chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${join(WORK, 'profile')}`,
        '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required', '--mute-audio',
        '--window-size=1280,900', 'about:blank'], { stdio: 'ignore' });
    let wsUrl = null;
    for (let i = 0; i < 60 && !wsUrl; i++) {
        try { wsUrl = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(APP)}`, { method: 'PUT' })).json()).webSocketDebuggerUrl; }
        catch (e) { await sleep(250); }
    }
    if (!wsUrl) throw new Error('Chrome DevTools sa nespustil');
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let id = 0; const pending = new Map();
    ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
    const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
    const js = async (expr) => {
        const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
        if (r.result && r.result.exceptionDetails) throw new Error('JS: ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
        return r.result.result.value;
    };
    const until = async (expr, ms, label) => {
        const t0 = Date.now();
        while (Date.now() - t0 < ms) { if (await js(expr)) return true; await sleep(100); }
        throw new Error('timeout: ' + label);
    };
    const seekTo = (t) => js(`new Promise(res => { const v = document.getElementById('cam'); v.addEventListener('seeked', () => res(true), { once: true }); v.currentTime = ${t}; })`);
    const loadFile = async () => {
        const doc = await send('DOM.getDocument', { depth: 1 });
        const q = await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '#file-input' });
        await send('DOM.setFileInputFiles', { nodeId: q.result.nodeId, files: [VIDEO] });
        await until("document.getElementById('cam').readyState >= 2 && !document.getElementById('file-controls').classList.contains('hidden')", 10000, 'video načítané');
        await sleep(400);
    };
    const parseTime = (txt) => { const m = /(\d+):(\d+)\.(\d+)/.exec(txt); return m ? Number(m[1]) * 60 + Number(m[2]) + Number(m[3]) / 100 : NaN; };

    await send('Page.enable'); await send('Runtime.enable'); await send('DOM.enable');
    await until("document.readyState === 'complete' && !!document.getElementById('btn-file')", 10000, 'stránka');
    await js("localStorage.clear(); true");

    console.log('\n=== Stopky zo záznamu (headless Chrome) ===\n');
    await loadFile();
    const a = await js(`(() => { const v = document.getElementById('cam'); return { paused: v.paused, w: v.videoWidth, dur: +v.duration.toFixed(2),
        status: document.getElementById('cell-status').textContent, sound: document.getElementById('btn-sound').disabled, calib: document.getElementById('btn-calib').disabled }; })()`);
    check(a.paused && a.w === 640 && a.dur === 6, `záznam sa načítal pozastavený (${a.w} px, ${a.dur} s)`);
    check(a.status === 'záznam pozastavený', `prvý snímok skalibroval fotobunku (stav „${a.status}“)`);
    check(a.sound && !a.calib, 'štart na zvuk vypnutý, kalibrácia zapnutá');

    await js("for (let i = 0; i < 3; i++) document.getElementById('btn-next').click(); true");
    await sleep(600);
    const t3 = await js("document.getElementById('cam').currentTime");
    await js("document.getElementById('btn-prev').click(); true");
    await sleep(400);
    const t2 = await js("document.getElementById('cam').currentTime");
    // Snímok k pokrýva [k/30, (k+1)/30): po 3 krokoch vpred má byť zobrazený snímok 3, po kroku späť snímok 2.
    check(t3 >= 0.1 && t3 < 0.1333 && t2 >= 0.0667 && t2 < 0.1, `krokovanie po snímkach: 3× vpred → snímok 3 (${t3.toFixed(3)} s), 1× späť → snímok 2 (${t2.toFixed(3)} s)`);

    await seekTo(1.0);
    await js("document.getElementById('btn-start').click(); true");
    await sleep(300);
    const run = await js("({ paused: document.getElementById('cam').paused, label: document.getElementById('time-label').textContent, status: document.getElementById('cell-status').textContent })");
    check(!run.paused && run.label === 'Beží' && run.status === 'ochranná doba', 'Štart rozbehol video a ochrannú dobu');
    await until("document.getElementById('time-label').textContent === 'Zastavené fotobunkou'", 15000, 'fotobunka zo záznamu');
    const st = await js(`(() => { const v = document.getElementById('cam'); return { time: document.getElementById('time').textContent, paused: v.paused, t: +v.currentTime.toFixed(3),
        crossed: document.getElementById('st-crossed').classList.contains('on'), precision: document.getElementById('precision').textContent }; })()`);
    const measured = parseTime(st.time);
    check(Math.abs(measured - EXPECTED_S) <= 0.034, `fotobunka zastavila na ${st.time} (očakávané ${EXPECTED_S.toFixed(2)} s ± 1 snímok)`);
    check(st.paused && st.t >= 3.39 && st.t <= 3.5 && st.crossed, `záznam stojí na snímku cieľa (${st.t} s), stav CIEĽ PRETNUTÝ`);
    check(/30 fps zázname/.test(st.precision), `presnosť zo záznamu: „${st.precision}“`);

    await seekTo(1.0);
    await js("document.getElementById('btn-reset').click(); document.getElementById('btn-start').click(); true");
    await sleep(700);
    await js("document.getElementById('btn-play').click(); true");
    await sleep(200);
    const p1 = await js("document.getElementById('time').textContent");
    await sleep(600);
    const p2 = await js("document.getElementById('time').textContent");
    check(p1 === p2 && parseTime(p1) > 0.3, `pauza prehrávania zmrazí čas (${p1})`);
    await js("document.getElementById('btn-play').click(); true");
    await until("document.getElementById('time-label').textContent === 'Zastavené fotobunkou'", 15000, 'druhé pretnutie');
    const second = await js("document.getElementById('time').textContent");
    check(Math.abs(parseTime(second) - measured) <= 0.034, `po pauze rovnaký čas (${second})`);

    await js("const s = document.getElementById('speed'); s.value = '0.25'; s.dispatchEvent(new Event('change')); true");
    await seekTo(1.0);
    await js("document.getElementById('btn-reset').click(); document.getElementById('btn-start').click(); true");
    const wall0 = Date.now();
    await until("document.getElementById('time-label').textContent === 'Zastavené fotobunkou'", 40000, 'pretnutie pri 0,25×');
    const slow = await js("document.getElementById('time').textContent");
    const wallS = (Date.now() - wall0) / 1000;
    check(Math.abs(parseTime(slow) - measured) <= 0.034 && wallS > 2 * EXPECTED_S, `spomalené 0,25× dá rovnaký čas (${slow}, reálne trvalo ${wallS.toFixed(1)} s)`);

    // koniec záznamu bez pretnutia: čiara mimo dráhy pruhu, štart od 4,5 s
    await js("localStorage.setItem('atletcoach.stopky', JSON.stringify({ line: { x1: 0.9, y1: 0.02, x2: 0.95, y2: 0.05 } })); true");
    await send('Page.reload');
    await until("document.readyState === 'complete' && !!document.getElementById('btn-file')", 10000, 'reload');
    await loadFile();
    await seekTo(4.5);
    await js("document.getElementById('btn-start').click(); true");
    await until("document.getElementById('time-label').textContent === 'Zastavené ručne'", 15000, 'koniec záznamu');
    const ended = await js("({ time: document.getElementById('time').textContent, status: document.getElementById('cell-status').textContent })");
    check(Math.abs(parseTime(ended.time) - 1.5) <= 0.034 && /skončil/.test(ended.status), `koniec záznamu zastaví čas (${ended.time}, „${ended.status}“)`);

    await js("document.getElementById('btn-cam').click(); true");
    await sleep(1500);
    const cam = await js("({ src: document.getElementById('cam').getAttribute('src'), hidden: document.getElementById('file-controls').classList.contains('hidden') })");
    check(cam.src === null && cam.hidden, 'zapnutie kamery záznam uvoľní');

    ws.close();
} catch (e) {
    check(false, 'test spadol: ' + e.message);
}
console.log('\n' + (ok ? 'VŠETKY KONTROLY PREŠLI' : 'NIEKTORÉ KONTROLY ZLYHALI') + '\n');
stopProcesses();
await sleep(500);                         // Chrome ešte dopisuje profil – inak by rm zlyhalo
rmSync(WORK, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
process.exit(ok ? 0 : 1);
