// Sign-in, account activation and sign-in help forms (moved out of the templates: no inline scripts, so the
// Content-Security-Policy can use script-src 'self'). Translated strings come from the page's JSON data block.
(function () {
  const el = document.getElementById("page-strings");
  const T = el ? JSON.parse(el.textContent) : {};
  // password Show / Hide toggle
  document.querySelectorAll("[data-reveal]").forEach(b => b.addEventListener("click", () => {
    const i = document.getElementById(b.dataset.reveal); const show = i.type === "password";
    i.type = show ? "text" : "password"; b.textContent = show ? T.hide : T.show; b.setAttribute("aria-pressed", String(show)); }));
  // sign-in
  const lf = document.getElementById("login-form");
  if (lf) lf.addEventListener("submit", e => { const f = e.target;
    if (!f.username.value.trim() || !f.password.value) { e.preventDefault(); W.toast(T.missing, "err"); (f.username.value.trim() ? f.password : f.username).focus(); return; }
    f.querySelector("button[type=submit]").setAttribute("aria-busy", "true"); });
  // account activation / set password
  const af = document.getElementById("act-form");
  if (af) af.addEventListener("submit", e => { const miss = [...af.querySelectorAll("input[required]")].find(i => !i.value.trim());
    if (miss) { e.preventDefault(); W.toast(T.missing, "err"); miss.focus(); return; }
    if (af.password.value !== af.confirm.value) { e.preventDefault(); W.toast(T.mismatch, "err"); af.confirm.focus(); return; }
    af.querySelector("button[type=submit]").setAttribute("aria-busy", "true"); });
  // forgot username / password
  const hf = document.getElementById("help-form");
  if (hf) {
    hf.querySelectorAll('input[name="id_type"]').forEach(r => r.addEventListener("change", () => { document.getElementById("id_last4").inputMode = r.value === "passport" ? "text" : "numeric"; }));
    hf.addEventListener("submit", e => {
      hf.querySelectorAll("[aria-invalid]").forEach(x => x.removeAttribute("aria-invalid"));
      const miss = [...hf.querySelectorAll("input[required], select[required]")].filter(i => !i.value.trim());
      if (miss.length) { e.preventDefault(); miss.forEach(i => i.setAttribute("aria-invalid", "true")); W.toast(T.missing, "err"); miss[0].focus(); return; }
      const l4 = document.getElementById("id_last4");
      if (l4 && !/^[0-9A-Za-z]{4}$/.test(l4.value.trim())) { e.preventDefault(); l4.setAttribute("aria-invalid", "true"); W.toast(T.bad_last4, "err"); l4.focus(); return; }
      hf.querySelector("button[type=submit]").setAttribute("aria-busy", "true"); });
  }
})();
