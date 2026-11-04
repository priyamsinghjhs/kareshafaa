// starting.html (cold-start wait page): moved out of the template, no inline scripts.
  (function () {
    const started = Date.now();
    if (new URLSearchParams(location.search).has("preview")) return;  // layout checks: stay on this page
    const go = () => {
      if (location.pathname === "/starting") { location.replace("/"); return; }  // opened directly
      const u = new URL(location.href); u.searchParams.set("ks_wait", "1"); location.replace(u.toString()); };
    const poll = () => fetch("/healthz", { cache: "no-store" }).then(r => { if (r.ok) go(); else setTimeout(poll, 2000); })
      .catch(() => setTimeout(poll, 2000));
    setInterval(() => { document.getElementById("wake-state").textContent = "Waiting for the service (" + Math.round((Date.now() - started) / 1000) + " s)"; }, 1000);
    poll();
  })();
  
