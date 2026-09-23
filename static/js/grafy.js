/* Grafy výkonnosti (/grafy). Dáta sú vložené v <script id="grafy-data"> už zo servera,
   takže sa nič nenačítava dodatočne a tabuľka pod grafom ukazuje tie isté čísla.
   Pravidlá: jedna os Y na graf, farby sérií len #879c26 / #3987e5 / #d55181,
   legenda len pri dvoch sériách. */
document.addEventListener('DOMContentLoaded', () => {
    const holder = document.getElementById('grafy-data');
    if (!holder || typeof Chart === 'undefined') return;
    const DATA = JSON.parse(holder.textContent);

    const LIME = '#879c26', BLUE = '#3987e5', MAGENTA = '#d55181';
    const SURFACE = '#161617';
    const MONO = { family: 'IBM Plex Mono', size: 10 };

    // 11.23 → "11.23", 125.4 → "2:05.40" – rovnaké ako fmt_time() v grafy.py.
    function fmtTime(s) {
        if (s === null || s === undefined || isNaN(s)) return '—';
        if (s < 60) return s.toFixed(2);
        const m = Math.floor(s / 60);
        const sec = s - m * 60;
        return m + ':' + (sec < 10 ? '0' : '') + sec.toFixed(2);
    }

    // Spoločný základ: mriežka a osi ustúpia, dáta idú dopredu.
    function baseOptions({ yTick, tooltipLabel, tooltipAfter, legend = false, reverse = false, beginAtZero = false }) {
        return {
            responsive: true,
            maintainAspectRatio: true,
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: {
                    reverse: reverse,
                    beginAtZero: beginAtZero,
                    grid:   { color: 'rgba(236,233,227,0.06)', drawTicks: false },
                    border: { display: false },
                    ticks:  { color: '#8b8b84', padding: 10, font: MONO,
                              callback: yTick || ((v) => v) }
                },
                x: {
                    grid:   { display: false },
                    border: { color: 'rgba(236,233,227,0.14)' },
                    ticks:  { color: '#8b8b84', maxRotation: 0, autoSkipPadding: 22, font: MONO }
                }
            },
            plugins: {
                legend: {
                    display: legend,
                    position: 'top',
                    align: 'end',
                    labels: { color: '#8b8b84', boxWidth: 8, boxHeight: 8, padding: 14,
                              font: { family: 'IBM Plex Mono', size: 10 } }
                },
                tooltip: {
                    backgroundColor: '#0b0b0c',
                    borderColor: 'rgba(236,233,227,0.20)',
                    borderWidth: 1,
                    cornerRadius: 2,
                    padding: 10,
                    displayColors: legend,
                    boxWidth: 8, boxHeight: 8,
                    titleColor: '#8b8b84',
                    titleFont: { family: 'IBM Plex Mono', size: 10, weight: '500' },
                    bodyColor: '#ece9e3',
                    bodyFont: { family: 'IBM Plex Mono', size: 13, weight: '600' },
                    footerColor: '#8b8b84',
                    footerFont: { family: 'IBM Plex Mono', size: 10, weight: '400' },
                    callbacks: {
                        label: tooltipLabel || ((c) => c.parsed.y === null ? 'bez záznamu' : String(c.parsed.y)),
                        footer: tooltipAfter || (() => '')
                    }
                }
            }
        };
    }

    function lineDataset(values, color, extra = {}) {
        return Object.assign({
            data: values,
            borderColor: color,
            backgroundColor: 'transparent',
            borderWidth: 2,
            tension: 0.2,
            spanGaps: true,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: color,
            pointHoverBorderColor: SURFACE,
            pointHoverBorderWidth: 2
        }, extra);
    }

    // Stĺpce: 4 px zaoblený vrch, päta rovná na základni, 2 px medzera cez rámik vo farbe plochy.
    function barDataset(label, values, color) {
        return {
            label: label,
            data: values,
            backgroundColor: color,
            borderColor: SURFACE,
            borderWidth: { left: 1, right: 1, top: 0, bottom: 0 },
            borderRadius: 4,
            borderSkipped: 'bottom',
            hoverBackgroundColor: color,
            maxBarThickness: 44
        };
    }

    function el(id) { return document.getElementById(id); }

    /* ── 1. Vývoj výsledkov: obrátená os, PB s väčšou značkou ── */
    const res = DATA.vysledky;
    if (el('chart-results') && res.values.length) {
        const pbIdx = res.meta.pb_index;
        // Body sa nekreslia (pointRadius 0), jedine PB dostane veľkú značku.
        const radii = res.values.map((_, i) => (i === pbIdx ? 6 : 0));
        const hoverRadii = res.values.map((_, i) => (i === pbIdx ? 8 : 5));
        new Chart(el('chart-results'), {
            type: 'line',
            data: { labels: res.labels, datasets: [lineDataset(res.values, LIME, {
                pointRadius: radii,
                pointHoverRadius: hoverRadii,
                pointBackgroundColor: res.values.map((_, i) => (i === pbIdx ? LIME : SURFACE)),
                pointBorderColor: LIME,
                pointBorderWidth: 2
            })]},
            options: baseOptions({
                reverse: true,
                yTick: (v) => fmtTime(v),
                tooltipLabel: (c) => fmtTime(c.parsed.y) + (c.dataIndex === pbIdx ? '  PB' : ''),
                tooltipAfter: (items) => {
                    const r = res.meta.rows[items[0].dataIndex];
                    if (!r) return '';
                    const parts = [];
                    if (r.competition) parts.push(r.competition);
                    if (r.place) parts.push(r.place + '. miesto');
                    if (r.wind !== null && r.wind !== undefined) parts.push('vietor ' + (r.wind > 0 ? '+' : '') + r.wind.toFixed(1));
                    return parts.join(' · ');
                }
            })
        });
    }

    /* ── 2. Posun PB: bežiace minimum, tiež obrátená os ── */
    const pb = DATA.rekordy;
    if (el('chart-pb') && pb.values.length) {
        new Chart(el('chart-pb'), {
            type: 'line',
            data: { labels: pb.labels, datasets: [lineDataset(pb.values, BLUE, { stepped: 'before', tension: 0 })] },
            options: baseOptions({
                reverse: true,
                yTick: (v) => fmtTime(v),
                tooltipLabel: (c) => 'PB ' + fmtTime(c.parsed.y),
                tooltipAfter: (items) => {
                    const r = pb.meta.rows[items[0].dataIndex];
                    return r ? 'výsledok ' + r.result + (r.improved ? ' · nový rekord' : '') : '';
                }
            })
        });
    }

    /* ── 3. Tréningový objem: km za týždeň ── */
    const vol = DATA.objem;
    if (el('chart-volume') && vol.meta.total_km > 0) {
        new Chart(el('chart-volume'), {
            type: 'bar',
            data: { labels: vol.labels, datasets: [barDataset('km', vol.values, LIME)] },
            options: baseOptions({
                beginAtZero: true,
                yTick: (v) => v + ' km',
                tooltipLabel: (c) => c.parsed.y.toFixed(1) + ' km',
                tooltipAfter: (items) => {
                    const r = vol.meta.rows[items[0].dataIndex];
                    return r ? r.week + ' · ' + r.from + ' – ' + r.to + ' · ' + r.sessions + ' tréningov' : '';
                }
            })
        });
    }

    /* ── 4. Plnenie plánu: dve série → legenda áno ── */
    const plan = DATA.plan;
    if (el('chart-plan') && plan.meta.total_planned > 0) {
        new Chart(el('chart-plan'), {
            type: 'bar',
            data: { labels: plan.labels, datasets: [
                barDataset(plan.meta.series[0], plan.values, BLUE),
                barDataset(plan.meta.series[1], plan.meta.completed, LIME)
            ]},
            options: Object.assign(baseOptions({
                legend: true,
                beginAtZero: true,
                yTick: (v) => (Number.isInteger(v) ? v : ''),
                tooltipLabel: (c) => c.dataset.label + ': ' + c.parsed.y,
                tooltipAfter: (items) => {
                    const r = plan.meta.rows[items[0].dataIndex];
                    return r && r.pct !== null ? 'plnenie ' + r.pct + ' %' : '';
                }
            }), { datasets: { bar: { categoryPercentage: 0.7, barPercentage: 1.0 } } })
        });
    }

    /* ── 5. Technika: kontakt a kadencia v dvoch samostatných grafoch ── */
    const tech = DATA.technika;
    if (el('chart-contact') && tech.labels.length) {
        new Chart(el('chart-contact'), {
            type: 'line',
            data: { labels: tech.labels, datasets: [lineDataset(tech.values, MAGENTA)] },
            options: baseOptions({
                yTick: (v) => v + ' ms',
                tooltipLabel: (c) => c.parsed.y === null ? 'bez hodnoty' : Math.round(c.parsed.y) + ' ms',
                tooltipAfter: (items) => { const r = tech.meta.rows[items[0].dataIndex]; return r && r.label ? r.label : ''; }
            })
        });
    }
    if (el('chart-cadence') && tech.labels.length) {
        new Chart(el('chart-cadence'), {
            type: 'line',
            data: { labels: tech.labels, datasets: [lineDataset(tech.meta.cadence, MAGENTA)] },
            options: baseOptions({
                yTick: (v) => v,
                tooltipLabel: (c) => c.parsed.y === null ? 'bez hodnoty' : Math.round(c.parsed.y) + ' krokov/min',
                tooltipAfter: (items) => { const r = tech.meta.rows[items[0].dataIndex]; return r && r.label ? r.label : ''; }
            })
        });
    }
});
