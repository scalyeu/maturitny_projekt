document.addEventListener("DOMContentLoaded", () => {
    // -----------------------------------------
    // 1. Form Submission & Mocking Prediction
    // -----------------------------------------
    const form = document.getElementById("training-form");
    if (form) {
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            
            const payload = {
                training_type: document.getElementById("training_type").value,
                times: document.getElementById("times").value,
                rest_minutes: document.getElementById("rest_minutes").value,
                whoop_hrv: document.getElementById("whoop_hrv").value,
                whoop_recovery: document.getElementById("whoop_recovery").value
            };

            const btn = form.querySelector('button[type="submit"]');
            btn.innerHTML = "Spracovávam...";
            
            // In a real application, we would POST to an endpoint to save it to DB. 
            // For now, let's delay to show loading, and trigger prediction.
            setTimeout(() => {
                runPrediction(payload);
                btn.innerHTML = "Dáta Analyzované";
                setTimeout(() => { btn.innerHTML = "Uložiť a Analyzovať dáta"; form.reset(); }, 2000);
            }, 800);
        });
    }

    // -----------------------------------------
    // 2. Chart.js Setup for drop-off visualization
    // -----------------------------------------
    const ctx = document.getElementById("trendChart");
    if (ctx) {
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['1. Máj', '8. Máj', '15. Máj', '22. Máj', '29. Máj'],
                datasets: [
                    {
                        label: 'Spomalenie na úsekoch (sekundy medzi 1. a posledným úsekom)',
                        data: [1.2, 1.8, 0.9, 2.3, 0.5],
                        borderColor: '#ef4444',
                        backgroundColor: 'rgba(239, 68, 68, 0.1)',
                        tension: 0.4,
                        fill: true,
                        yAxisID: 'y'
                    },
                    {
                        label: 'WHOOP HRV (ms)',
                        data: [58, 45, 66, 42, 72],
                        borderColor: '#22d3ee',
                        backgroundColor: 'rgba(34, 211, 238, 0.1)',
                        tension: 0.4,
                        fill: true,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#9ca3af' },
                        title: { display: true, text: 'Sekundy', color: '#9ca3af' }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        grid: { drawOnChartArea: false },
                        ticks: { color: '#9ca3af' },
                        title: { display: true, text: 'HRV', color: '#9ca3af' }
                    },
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#9ca3af' }
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: '#f3f4f6' }
                    }
                }
            }
        });
    }
});

async function runPrediction(payload = {}) {
    const timeDisplay = document.getElementById("predicted-time");
    timeDisplay.style.opacity = 0.5;
    
    try {
        const response = await fetch('/api/predict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (data.status === "success") {
            // Display prediction with suffix
            timeDisplay.innerHTML = data.predicted_time.toFixed(2) + "s";
            timeDisplay.style.opacity = 1;
        }
    } catch (e) {
        console.error(e);
        timeDisplay.innerHTML = "Chyba";
        timeDisplay.style.opacity = 1;
    }
}
