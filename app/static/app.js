// Live IP balance: subscribe to the WebSocket for authoritative updates, and
// smoothly count down between them using the current decay rate.
(function () {
    const el = document.getElementById("balance");
    if (!el) return;
    const rateEl = document.getElementById("rate");
    const statusEl = document.getElementById("status");

    let balance = parseFloat(el.textContent) || 0;
    let rate = parseFloat(el.dataset.rate) || 0;      // IP per minute
    let running = el.dataset.running === "1";

    function render() {
        el.textContent = balance.toFixed(1);
    }

    // Local smoothing: decrement every 200ms so the number visibly ticks down.
    setInterval(function () {
        if (running && balance > 0) {
            balance = Math.max(0, balance - rate * (0.2 / 60));
            render();
        }
    }, 200);

    function connect() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${location.host}/ws`);

        ws.onmessage = function (ev) {
            const msg = JSON.parse(ev.data);
            if (msg.type !== "state") return;
            balance = msg.balance;              // resync to server truth
            rate = msg.rate;
            running = msg.running;
            render();
            if (rateEl) rateEl.textContent = rate.toFixed(3);
            if (statusEl) statusEl.textContent = running ? "SPĒLE IET" : "PAUZĒTS";
        };

        // Reconnect if the socket drops (server restart, flaky wifi in the field).
        ws.onclose = function () { setTimeout(connect, 2000); };
        ws.onerror = function () { ws.close(); };
    }
    connect();
})();
