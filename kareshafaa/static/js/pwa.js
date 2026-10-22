// PWA: register the root-scope service worker and offer an "Install app" button (Chrome/Edge/Android).
(function () {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((e) => console.warn("service worker not registered", e));
    });
  }
  let deferred = null;
  const btn = () => document.getElementById("install-app");
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e;
    const b = btn();
    if (b) b.hidden = false;
  });
  window.addEventListener("appinstalled", () => { deferred = null; const b = btn(); if (b) b.hidden = true; });
  document.addEventListener("click", async (e) => {
    if (!e.target || e.target.id !== "install-app" || !deferred) return;
    deferred.prompt();
    try { await deferred.userChoice; } finally { deferred = null; btn().hidden = true; }
  });
})();
