// Polls the server every 8 seconds so the farmer's queue position
// updates live without needing to refresh the page.
(function () {
  function refreshQueue() {
    fetch("/api/queue-status")
      .then((res) => res.json())
      .then((data) => {
        if (!data.has_booking) return;
        if (data.status !== "waiting") {
          // Status changed (e.g. now being served) — reload to show the new panel.
          window.location.reload();
          return;
        }
        const servingEl = document.getElementById("serving-token");
        const aheadEl = document.getElementById("ahead-count");
        const etaEl = document.getElementById("eta-minutes");
        if (servingEl) servingEl.textContent = data.serving_token;
        if (aheadEl) aheadEl.textContent = data.ahead;
        if (etaEl) etaEl.textContent = data.eta_minutes + " min";
      })
      .catch(() => {
        /* silent — will retry on next interval */
      });
  }

  setInterval(refreshQueue, 8000);
})();
