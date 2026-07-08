// Live IP balance: subscribe to the WebSocket for authoritative updates, and
// smoothly count down between them using the current decay rate.
(function () {
    const el = document.getElementById("balance");
    if (!el) return;
    const rateEl = document.getElementById("rate");
    const statusEl = document.getElementById("status");
    const incomeEl = document.getElementById("income");
    const incomeLine = document.getElementById("incomeline");
    const jeopardy = document.getElementById("jeopardy");

    let balance = parseFloat(el.textContent) || 0;
    let rate = parseFloat(el.dataset.rate) || 0;      // NET IP per minute (may be negative)
    let running = el.dataset.running === "1";
    let jeopardyPending = jeopardy ? jeopardy.dataset.pending === "1" : false;

    function render() {
        el.textContent = balance.toFixed(1);
        // Reveal the Jeopardy comeback panel the moment a team hits 0 IP.
        // (Once a card is pending, keep it shown until the server re-renders.)
        if (jeopardy) jeopardy.style.display = (balance <= 0 || jeopardyPending) ? "" : "none";
    }

    // Local smoothing: apply the net rate every 200ms so the number ticks live.
    // Net can be negative (region income > decay), in which case the balance grows.
    setInterval(function () {
        if (running) {
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
            rate = msg.rate;                    // net (decay - income)
            running = msg.running;
            render();
            if (rateEl) rateEl.textContent = rate.toFixed(3);
            if (statusEl) statusEl.textContent = running ? "SPĒLE IET" : "PAUZĒTS";
            if (incomeEl && incomeLine) {
                incomeEl.textContent = (msg.income || 0).toFixed(3);
                incomeLine.style.display = (msg.income || 0) > 0 ? "" : "none";
            }
        };

        // Reconnect if the socket drops (server restart, flaky wifi in the field).
        ws.onclose = function () { setTimeout(connect, 2000); };
        ws.onerror = function () { ws.close(); };
    }
    connect();
})();
