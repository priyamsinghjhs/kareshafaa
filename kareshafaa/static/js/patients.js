// Patient registration / record forms (shared by /patients/new and /patients/{id}).
// Server rules are the source of truth (kareshafaa/services/patients.py); these checks only give early, matching hints.
const PF = {
  opts: null,
  async options() { if (!PF.opts) PF.opts = await W.api("/api/patients/options"); return PF.opts; },
  sel(name, label, pairs, value, { required = false, blank = "Choose…", hint = "", mark = false } = {}) {
    return `<label>${W.esc(label)}${required || mark ? ' <span class="req" aria-hidden="true">*</span>' : ""}<select name="${name}" ${required ? "required" : ""}>${blank !== null ? `<option value="">${W.esc(blank)}</option>` : ""}${pairs.map(([k, v]) => `<option value="${W.esc(k)}" ${String(value ?? "") === String(k) ? "selected" : ""}>${W.esc(v)}</option>`).join("")}</select>${hint ? `<span class="hint small muted">${hint}</span>` : ""}</label>`;
  },
  inp(name, label, value, { type = "text", required = false, hint = "", attrs = "" } = {}) {
    return `<label>${W.esc(label)}${required ? ' <span class="req" aria-hidden="true">*</span>' : ""}<input name="${name}" type="${type}" value="${W.esc(value ?? "")}" ${required ? "required" : ""} ${attrs}>${hint ? `<span class="hint small muted">${hint}</span>` : ""}</label>`;
  },
  chk(name, label, on) { return `<label class="check"><input type="checkbox" name="${name}" ${on ? "checked" : ""}> ${W.esc(label)}</label>`; },
  today() { const t = new Date(); return new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); },
  demographics(v = {}) { const O = PF.opts;
    return `<div class="form-pair">${PF.inp("given_name", "Given name(s)", v.given_name, { required: true, attrs: 'maxlength="100" autocomplete="off"' })}${PF.inp("family_name", "Family name", v.family_name, { required: true, attrs: 'maxlength="100" autocomplete="off"' })}</div>
      <div class="form-pair">${PF.inp("birth_date", "Date of birth", v.birth_date, { type: "date", required: true, attrs: `max="${PF.today()}" min="1900-01-01"` })}${PF.sel("gender", "Sex", Object.entries(O.genders), v.gender, { required: true })}</div>
      <div class="form-pair">${PF.inp("national_id", "Emirates ID", v.national_id, { hint: "784-YYYY-NNNNNNN-C. Checked against the card's check digit.", attrs: 'inputmode="numeric" maxlength="25" autocomplete="off" placeholder="784-1985-1234567-1"' })}${PF.sel("nationality", "Nationality", O.nationalities.map(n => [n, n]), v.nationality, { required: true })}</div>
      ${PF.sel("language", "Preferred language", Object.entries(O.languages), v.language, { required: true, hint: "Messages and the patient app use this language (Tagalog and Malayalam: English with an interpreter call)." })}`; },
  contact(v = {}) { const O = PF.opts;
    return `<div class="form-pair">${PF.inp("phone", "Mobile phone", v.phone, { type: "tel", hint: "UAE mobile 05X XXX XXXX, or international with +", attrs: 'inputmode="tel" maxlength="30" autocomplete="off"' })}${PF.inp("email", "Email", v.email, { type: "email", attrs: 'maxlength="200" autocomplete="off"' })}</div>
      <div class="checks">${PF.chk("whatsapp", "Uses WhatsApp on this number", v.whatsapp)}${PF.chk("has_smartphone", "Has a smartphone (can use the app)", v.has_smartphone)}</div>
      ${PF.sel("preferred_channel", "Preferred way to contact", Object.entries(O.channels), v.preferred_channel, { blank: "No preference" })}
      ${PF.inp("address_line", "Address (building, street, area)", v.address_line, { attrs: 'maxlength="300"' })}
      <div class="form-pair">${PF.inp("city", "City / area", v.city, { attrs: 'maxlength="100"' })}${PF.sel("emirate", "Emirate", O.emirates.map(e => [e, e]), v.emirate, { required: true })}</div>
      <div data-region ${v.emirate && v.emirate !== "Abu Dhabi" ? "hidden" : ""}>${PF.sel("region", "Region (Abu Dhabi emirate)", O.abu_dhabi_regions.map(r => [r, r]), v.region, { mark: true, hint: "Required inside Abu Dhabi emirate. Used to plan services and to check fairness across regions." })}</div>`; },
  care(v = {}) { const O = PF.opts;
    return `<div class="form-pair">${PF.sel("managing_org_id", "Registered health centre", O.health_centres.map(o => [o.id, o.name]), v.managing_org_id, { required: true })}${PF.sel("gp_id", "Family doctor (GP)", O.gps.filter(g => !v.managing_org_id || g.org_id === +v.managing_org_id).map(g => [g.id, g.name]), v.gp_id, { blank: "Not yet assigned" })}</div>`; },
  wire(form) { const O = PF.opts;
    const em = form.querySelector('[name="emirate"]'), reg = form.querySelector("[data-region]");
    if (em && reg) em.addEventListener("change", () => { reg.hidden = em.value !== "Abu Dhabi" && em.value !== ""; });
    const hc = form.querySelector('[name="managing_org_id"]'), gp = form.querySelector('[name="gp_id"]');
    if (hc && gp) hc.addEventListener("change", () => { const cur = gp.value;
      gp.innerHTML = `<option value="">Not yet assigned</option>` + O.gps.filter(g => g.org_id === +hc.value).map(g => `<option value="${g.id}" ${String(g.id) === cur ? "selected" : ""}>${W.esc(g.name)}</option>`).join(""); });
  },
  // the same rules as the server, so mistakes show before saving (the server still decides)
  eidOk(raw) { const d = String(raw || "").replace(/[\s-]/g, ""); if (!d) return true;
    if (!/^784\d{12}$/.test(d)) return false; let t = 0;
    [...d.slice(0, 14)].reverse().forEach((c, i) => { let x = +c * (i % 2 === 0 ? 2 : 1); t += x > 9 ? x - 9 : x; });
    return (10 - t % 10) % 10 === +d[14]; },
  phoneOk(raw) { let s = String(raw || "").trim(); if (!s) return true; const plus = s.startsWith("+") || s.startsWith("00");
    let d = s.replace(/[\s().-]/g, "").replace(/^\+/, ""); if (d.startsWith("00")) d = d.slice(2); if (!/^\d+$/.test(d)) return false;
    if (!plus && d.startsWith("0") && (d.length === 9 || d.length === 10)) d = "971" + d.slice(1); else if (!plus && d.length === 9 && d[0] === "5") d = "971" + d;
    if (d.startsWith("971")) return /^5\d{8}$/.test(d.slice(3)) || /^[2-9]\d{7}$/.test(d.slice(3));
    return plus && d.length >= 8 && d.length <= 15; },
  check(form) { const g = n => form.querySelector(`[name="${n}"]`), bad = [];
    const mark = (n, ok, msg) => { const el = g(n); if (!el) return; el.setAttribute("aria-invalid", ok ? "false" : "true"); if (!ok) bad.push(msg); };
    if (g("national_id")) mark("national_id", PF.eidOk(g("national_id").value), "Emirates ID: 15 digits starting 784 with a valid check digit (784-YYYY-NNNNNNN-C).");
    if (g("phone")) mark("phone", PF.phoneOk(g("phone").value), "Mobile phone: use 05X XXX XXXX or an international number starting with +.");
    if (g("email") && g("email").value.trim()) mark("email", /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(g("email").value.trim()), "Email address is not valid.");
    const ch = g("preferred_channel"); if (ch && ["sms", "whatsapp", "phone"].includes(ch.value) && g("phone") && !g("phone").value.trim()) bad.push("A phone number is needed for the preferred channel chosen.");
    if (g("emirate") && g("emirate").value === "Abu Dhabi" && g("region") && !g("region").value) { mark("region", false, "Region: choose Abu Dhabi, Al Ain or Al Dhafra."); }
    return bad; },
  collect(form, keys) { const fd = new FormData(form), out = {};
    keys.forEach(k => { const el = form.querySelector(`[name="${k}"]`); if (!el) return;
      if (el.type === "checkbox") out[k] = el.checked;
      else if (["managing_org_id", "gp_id"].includes(k)) out[k] = fd.get(k) ? +fd.get(k) : null;
      else { const v = String(fd.get(k) ?? "").trim(); out[k] = v === "" ? null : v; } });
    if (keys.includes("emirate") && out.emirate && out.emirate !== "Abu Dhabi") out.region = null;
    return out; },
  KEYS: { demographics: ["given_name", "family_name", "birth_date", "gender", "national_id", "nationality", "language"],
    contact: ["phone", "email", "address_line", "city", "emirate", "region", "preferred_channel", "whatsapp", "has_smartphone"],
    care: ["managing_org_id", "gp_id"] },
  dupList(cands) { return `<ul class="dup-list">${cands.map(c => `<li><a href="/patients/${c.id}" target="_blank" rel="noopener"><b>${W.esc(c.name)}</b></a> · ${W.esc(c.mrn)} · born ${W.esc(c.birth_date || c.birth_year || "–")}${c.emirates_id ? " · EID " + W.esc(c.emirates_id) : ""} <span class="chip sla-amber">${W.esc(c.why)}</span></li>`).join("")}</ul>`; },
  label(map, k) { return (map && map[k]) || k || "–"; },
};
