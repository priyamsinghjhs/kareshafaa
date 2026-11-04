// Patient registration / record forms (shared by /patients/new and /patients/{id}).
// Server rules are the source of truth (kareshafaa/services/patients.py); these checks only give early, matching hints.
const PF = {
  opts: null,
  async options() { if (!PF.opts) PF.opts = await W.api("/api/patients/options"); return PF.opts; },
  sel(name, label, pairs, value, { required = false, blank = "Choose…", hint = "", mark = false } = {}) {
    return `<label>${W.esc(label)}${required || mark ? ' <span class="req" aria-hidden="true">*</span>' : ""}<select name="${name}" ${required ? "required" : ""}>${blank !== null ? `<option value="">${W.esc(blank)}</option>` : ""}${pairs.map(([k, v]) => `<option value="${W.esc(k)}" ${String(value ?? "") === String(k) ? "selected" : ""}>${W.esc(v)}</option>`).join("")}</select>${hint ? `<span class="hint small muted">${hint}</span>` : ""}</label>`;
  },
  inp(name, label, value, { type = "text", required = false, hint = "", attrs = "", mark = false } = {}) {
    return `<label>${W.esc(label)}${required || mark ? ' <span class="req" aria-hidden="true">*</span>' : ""}<input name="${name}" type="${type}" value="${W.esc(value ?? "")}" ${required ? "required" : ""} ${attrs}>${hint ? `<span class="hint small muted">${hint}</span>` : ""}</label>`;
  },
  chk(name, label, on) { return `<label class="check"><input type="checkbox" name="${name}" ${on ? "checked" : ""}> ${W.esc(label)}</label>`; },
  today() { const t = new Date(); return new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); },
  demographics(v = {}) { const O = PF.opts;
    return `<div class="form-pair">${PF.inp("given_name", "Given name(s)", v.given_name, { required: true, attrs: 'maxlength="100" autocomplete="off"' })}${PF.inp("family_name", "Family name", v.family_name, { required: true, attrs: 'maxlength="100" autocomplete="off"' })}</div>
      <div class="form-pair">${PF.inp("birth_date", "Date of birth", v.birth_date, { type: "date", required: true, attrs: `max="${PF.today()}" min="1900-01-01"` })}${PF.sel("gender", "Sex", Object.entries(O.genders), v.gender, { required: true })}</div>
      ${PF.sel("nationality", "Nationality", O.nationalities.map(n => [n, n]), v.nationality, { required: true })}
      ${PF.idDoc(v)}
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
  // "Has insurance?" decides what is shown: Yes -> every policy field; No (self-pay) -> no other insurance field at all.
  // The answer is read from the payer for records saved before the question existed. The server applies the same rule.
  hasIns(v = {}) { return v.has_insurance ?? (v.insurance_payer ? v.insurance_payer !== "self_pay" : null); },
  insurance(v = {}) { const O = PF.opts, hi = PF.hasIns(v);
    const payers = Object.entries(O.payers).filter(([k]) => k !== "self_pay");
    const radio = (val, label, on) => `<label class="check"><input type="radio" name="has_insurance" value="${val}" aria-controls="ins-fields" ${on ? "checked" : ""}> ${label}</label>`;
    return `<fieldset class="ins-q" data-ins-q><legend>Has insurance?</legend>
      <div class="checks ins-yn">${radio("yes", "Yes", hi === true)}${radio("no", "No (self-pay)", hi === false)}</div>
      <p class="hint small muted" data-ins-no ${hi === false ? "" : "hidden"}>Self-pay: no insurance details are kept. Discuss costs before a referral or appointment.</p>
      <p class="hint small muted" data-ins-unset ${hi === null ? "" : "hidden"}>Not recorded yet. Ask the patient, then choose Yes or No.</p></fieldset>
      <div id="ins-fields" data-ins-fields ${hi === true ? "" : "hidden"}>${PF.sel("insurance_payer", "Insurer", payers, v.insurance_payer === "self_pay" ? "" : v.insurance_payer, { mark: true, hint: "Thiqa for UAE nationals." })}
      <div class="form-pair">${PF.inp("insurance_plan", "Plan / network", v.insurance_plan, { attrs: 'maxlength="80"' })}${PF.inp("insurance_member_id", "Member / card number", v.insurance_member_id, { mark: true, hint: "Required when the patient has insurance.", attrs: 'maxlength="40" autocomplete="off" dir="ltr"' })}</div>
      <div class="form-pair">${PF.inp("insurance_valid_to", "Cover ends on", v.insurance_valid_to, { type: "date", attrs: 'min="2000-01-01"' })}${PF.sel("insurance_preauth_required", "Pre-authorisation needed before a referral?", [["yes", "Yes"], ["no", "No"]], v.insurance_preauth_required === true ? "yes" : v.insurance_preauth_required === false ? "no" : "", { blank: "Not recorded" })}</div></div>`; },
  contacts(v = {}) { const O = PF.opts;
    return `<div class="form-pair">${PF.inp("nok_name", "Next of kin name", v.nok_name, { attrs: 'maxlength="200" autocomplete="off"' })}${PF.sel("nok_relationship", "Relationship", Object.entries(O.relationships), v.nok_relationship, { blank: "Not recorded" })}</div>
      ${PF.inp("nok_phone", "Next of kin phone", v.nok_phone, { type: "tel", hint: "UAE mobile 05X XXX XXXX, or international with +", attrs: 'inputmode="tel" maxlength="30" autocomplete="off"' })}
      <div class="checks">${PF.chk("nok_is_guardian", "Legal guardian (decides for the patient)", v.nok_is_guardian)}</div>
      ${PF.sel("proxy_consent", "May we discuss screening and follow-up with this person?", [["permit", "Yes, the patient agrees"], ["deny", "No, the patient does not agree"]], v.proxy_consent, { blank: "Not asked yet", hint: "Ask the patient. Needs the next of kin's name and phone." }).replace('<select name="proxy_consent"', `<select name="proxy_consent" data-was="${W.esc(v.proxy_consent || "")}"`)}
      ${PF.inp("proxy_note", "How did the patient give this answer?", v.proxy_note, { hint: "Needed when you record an answer (at least 10 characters), e.g. in person at the clinic.", attrs: 'maxlength="500"' })}`; },
  // identity document: the keyboard, hint and checks follow the type (Emirates ID keeps its check digit)
  ID_UI: {
    emirates_id: { label: "Emirates ID number", hint: "784-YYYY-NNNNNNN-C. Dashes are added for you; checked against the card's check digit.", attrs: 'inputmode="numeric" pattern="[0-9\\-]*" maxlength="18" placeholder="784-1985-1234567-3"' },
    passport: { label: "Passport number", hint: "5 to 20 letters and digits, as printed on the passport.", attrs: 'inputmode="text" autocapitalize="characters" spellcheck="false" maxlength="24" placeholder="e.g. AB1234567"' },
    gcc_id: { label: "GCC national ID number", hint: "5 to 20 letters and digits, as printed on the card.", attrs: 'inputmode="text" autocapitalize="characters" spellcheck="false" maxlength="24"' },
    other: { label: "Document number", hint: "5 to 20 letters and digits.", attrs: 'inputmode="text" autocapitalize="characters" spellcheck="false" maxlength="24"' },
  },
  idDoc(v = {}) { const O = PF.opts, t = v.id_type || "", U = PF.ID_UI[t] || PF.ID_UI.other;
    const countries = t === "gcc_id" ? O.gcc_countries : O.nationalities.filter(n => n !== "Other");
    const country = v.id_country || (t === "passport" && v.nationality !== "Other" ? v.nationality : t === "gcc_id" && O.gcc_countries.includes(v.nationality) ? v.nationality : "");
    return `<fieldset class="id-doc" data-id-doc><legend class="small">Identity document (optional)</legend>
      <div class="form-pair">${PF.sel("id_type", "Document type", Object.entries(O.id_types), t, { blank: "None given", hint: "Visitors and new arrivals: choose Passport." })}
      <div data-id-number ${t ? "" : "hidden"}>${PF.inp("id_number", U.label, v.id_number, { hint: U.hint, attrs: U.attrs + ' autocomplete="off"' })}</div></div>
      <div data-id-country ${t && t !== "emirates_id" ? "" : "hidden"}>${PF.sel("id_country", "Issuing country", countries.map(n => [n, n]), country, { blank: t === "passport" ? "Same as nationality" : "Choose…", mark: t === "passport" || t === "gcc_id" })}</div></fieldset>`; },
  eidFormat(raw) { const d = String(raw || "").replace(/\D/g, "").slice(0, 15), g = [3, 4, 7, 1], out = []; let i = 0;
    for (const n of g) { if (i >= d.length) break; out.push(d.slice(i, i + n)); i += n; } return out.join("-"); },
  // Emirates ID typing: dashes are inserted as the groups fill, wherever the caret is (typing, deleting, editing in the
  // middle, pasting with spaces or dashes); backspace on a dash removes the digit before it so the user is never stuck.
  eidCaret(f, n) { if (n <= 0) return 0; let seen = 0; for (let i = 0; i < f.length; i++) if (/\d/.test(f[i]) && ++seen === n) return i + 1; return f.length; },
  eidSet(num, digits, n) { const f = PF.eidFormat(digits), c = PF.eidCaret(f, Math.min(n, 15)); num.value = f;
    if (document.activeElement === num) num.setSelectionRange(c, c); },
  eidInput(num, active) { const dn = s => s.replace(/\D/g, "");
    num.addEventListener("beforeinput", e => { if (!active()) return; const s = num.selectionStart, t = num.selectionEnd, v = num.value;
      if (s !== t || s == null) return; const n = dn(v.slice(0, s)).length, d = dn(v);
      if (e.inputType === "deleteContentBackward" && v[s - 1] === "-" && n > 0) { e.preventDefault(); PF.eidSet(num, d.slice(0, n - 1) + d.slice(n), n - 1); num.dispatchEvent(new Event("change", { bubbles: true })); }
      else if (e.inputType === "deleteContentForward" && v[s] === "-") { e.preventDefault(); PF.eidSet(num, d.slice(0, n) + d.slice(n + 1), n); num.dispatchEvent(new Event("change", { bubbles: true })); } });
    num.addEventListener("paste", e => { if (!active()) return; const txt = (e.clipboardData || window.clipboardData)?.getData("text") || ""; e.preventDefault();
      const v = num.value, s = num.selectionStart ?? v.length, t = num.selectionEnd ?? v.length, before = dn(v.slice(0, s)), ins = dn(txt);
      PF.eidSet(num, before + ins + dn(v.slice(t)), before.length + ins.length); num.dispatchEvent(new Event("input", { bubbles: true })); });
    num.addEventListener("input", () => { if (!active()) return; const v = num.value, s = num.selectionStart ?? v.length;
      const f = PF.eidFormat(v); if (f !== v) PF.eidSet(num, v, dn(v.slice(0, s)).length); }); },
  wireIdDoc(form) { const box = form.querySelector("[data-id-doc]"); if (!box) return;
    const typ = box.querySelector('[name="id_type"]'), nat = form.querySelector('[name="nationality"]');
    const wireNum = () => { const num = box.querySelector('[name="id_number"]'); if (num) PF.eidInput(num, () => typ.value === "emirates_id"); };
    typ.onchange = () => { const cur = { id_type: typ.value, id_number: "", id_country: "", nationality: nat ? nat.value : "" };
      const keep = box.querySelector('[name="id_number"]'); if (keep && keep.value.trim() && (typ.value !== "emirates_id") === (keep.dataset.t !== "emirates_id")) cur.id_number = keep.value;
      box.outerHTML = PF.idDoc(cur); PF.wireIdDoc(form); const n = form.querySelector('[name="id_number"]'); if (n) { n.dataset.t = typ.value; n.focus(); } };
    const n0 = box.querySelector('[name="id_number"]'); if (n0) n0.dataset.t = typ.value;
    wireNum(); },
  docOk(raw) { const s = String(raw || "").replace(/[\s-]/g, "").toUpperCase(); return !s || /^[A-Z0-9]{5,20}$/.test(s); },
  wire(form) { const O = PF.opts;
    const em = form.querySelector('[name="emirate"]'), reg = form.querySelector("[data-region]");
    if (em && reg) em.addEventListener("change", () => { reg.hidden = em.value !== "Abu Dhabi" && em.value !== ""; });
    PF.wireIdDoc(form);
    const box = form.querySelector("[data-ins-fields]");
    if (box) form.querySelectorAll('[name="has_insurance"]').forEach(r => r.addEventListener("change", () => {
      const x = form.querySelector('[name="has_insurance"]:checked')?.value;
      box.hidden = x !== "yes"; form.querySelector("[data-ins-no]").hidden = x !== "no"; form.querySelector("[data-ins-unset]").hidden = !!x;
      if (x !== "yes") box.querySelectorAll("[aria-invalid]").forEach(el => { el.removeAttribute("aria-invalid"); PF.fieldError(el, ""); }); }));
    const hc = form.querySelector('[name="managing_org_id"]'), gp = form.querySelector('[name="gp_id"]');
    if (hc && gp) hc.addEventListener("change", () => { const cur = gp.value;
      gp.innerHTML = `<option value="">Not yet assigned</option>` + O.gps.filter(g => g.org_id === +hc.value).map(g => `<option value="${g.id}" ${String(g.id) === cur ? "selected" : ""}>${W.esc(g.name)}</option>`).join(""); });
  },
  // the same rules as the server, so mistakes show before saving (the server still decides)
  // same rules and wording as services/patients.normalise_emirates_id
  eidWhy(raw) { const d = String(raw || "").replace(/[\s-]/g, ""); if (!d) return "";
    if (!/^\d{15}$/.test(d)) return "Emirates ID must have 15 digits, in the form 784-YYYY-NNNNNNN-C.";
    if (!d.startsWith("784")) return "Emirates ID must start with 784.";
    const y = +d.slice(3, 7); if (y < 1900 || y > new Date().getFullYear()) return "Emirates ID year part (digits 4-7) is not a valid year.";
    let t = 0; [...d.slice(0, 14)].reverse().forEach((c, i) => { let x = +c * (i % 2 === 0 ? 2 : 1); t += x > 9 ? x - 9 : x; });
    return (10 - t % 10) % 10 === +d[14] ? "" : "Emirates ID check digit does not match - please check the number on the card."; },
  eidOk(raw) { return !PF.eidWhy(raw); },
  phoneOk(raw) { let s = String(raw || "").trim(); if (!s) return true; const plus = s.startsWith("+") || s.startsWith("00");
    let d = s.replace(/[\s().-]/g, "").replace(/^\+/, ""); if (d.startsWith("00")) d = d.slice(2); if (!/^\d+$/.test(d)) return false;
    if (!plus && d.startsWith("0") && (d.length === 9 || d.length === 10)) d = "971" + d.slice(1); else if (!plus && d.length === 9 && d[0] === "5") d = "971" + d;
    if (d.startsWith("971")) return /^5\d{8}$/.test(d.slice(3)) || /^[2-9]\d{7}$/.test(d.slice(3));
    return plus && d.length >= 8 && d.length <= 15; },
  // the reason shown right under the field (as well as in the summary by the buttons), cleared as soon as it is edited
  fieldError(el, msg) { const id = `err-${el.name}`; let e = el.parentElement.querySelector(`#${id}`);
    if (!msg) { if (e) e.remove(); return; }
    if (!e) { e = document.createElement("span"); e.id = id; e.className = "field-err small"; e.dir = "auto"; el.after(e);
      el.setAttribute("aria-describedby", id); el.addEventListener("input", () => { e.remove(); el.removeAttribute("aria-invalid"); }, { once: true }); }
    e.textContent = msg; },
  check(form) { const g = n => form.querySelector(`[name="${n}"]`), bad = [];
    const mark = (n, ok, msg) => { const el = g(n); if (!el) return; el.setAttribute("aria-invalid", ok ? "false" : "true"); PF.fieldError(el, ok ? "" : msg); if (!ok) bad.push(msg); };
    if (g("national_id")) mark("national_id", PF.eidOk(g("national_id").value), PF.eidWhy(g("national_id").value));
    const it = g("id_type"), inum = g("id_number");
    if (it && inum && inum.closest("[hidden]") === null) {
      if (it.value === "emirates_id") mark("id_number", PF.eidOk(inum.value), PF.eidWhy(inum.value));
      else if (it.value) mark("id_number", PF.docOk(inum.value), `${PF.ID_UI[it.value].label}: 5 to 20 letters and digits.`);
      const ic = g("id_country");
      if (it.value === "passport" && inum.value.trim() && ic && !ic.value && (!g("nationality") || !g("nationality").value || g("nationality").value === "Other")) mark("id_country", false, "Passport: choose the issuing country.");
      if (it.value === "gcc_id" && inum.value.trim() && ic && !ic.value && !(g("nationality") && PF.opts.gcc_countries.includes(g("nationality").value))) mark("id_country", false, "GCC national ID: choose the issuing country.");
    }
    if (g("phone")) mark("phone", PF.phoneOk(g("phone").value), "Mobile phone: use 05X XXX XXXX or an international number starting with +.");
    if (g("email") && g("email").value.trim()) mark("email", /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(g("email").value.trim()), "Email address is not valid.");
    const ch = g("preferred_channel"); if (ch && ["sms", "whatsapp", "phone"].includes(ch.value) && g("phone") && !g("phone").value.trim()) bad.push("A phone number is needed for the preferred channel chosen.");
    if (g("nok_phone")) mark("nok_phone", PF.phoneOk(g("nok_phone").value), "Next of kin phone: use 05X XXX XXXX or an international number starting with +.");
    if (form.querySelector('[name="has_insurance"][value="yes"]:checked')) {  // only checked when the patient has insurance
      const py = g("insurance_payer"), mid = g("insurance_member_id");
      if (py) mark("insurance_payer", !!py.value, "Insurer: choose the patient's insurer.");
      if (mid && !mid.value.trim()) mark("insurance_member_id", false, "Member / card number is required when the patient has insurance.");
      else if (mid) mark("insurance_member_id", /^[A-Za-z0-9][A-Za-z0-9 .\/-]{2,39}$/.test(mid.value.trim()), "Member / card number: 3-40 letters, digits, spaces, dots, slashes or dashes.");
    }
    const pc = g("proxy_consent"); if (pc && pc.value === "permit" && (!g("nok_name").value.trim() || !g("nok_phone").value.trim())) bad.push("Proxy consent needs the next of kin's name and phone number.");
    if (pc && pc.value && (g("proxy_note").value.trim().length < 10) && pc.dataset.was !== pc.value) mark("proxy_note", false, "Say how the patient gave the proxy consent answer (at least 10 characters).");
    if (g("nok_is_guardian") && g("nok_is_guardian").checked && !g("nok_name").value.trim()) bad.push("A guardian needs a name.");
    if (g("emirate") && g("emirate").value === "Abu Dhabi" && g("region") && !g("region").value) { mark("region", false, "Region: choose Abu Dhabi, Al Ain or Al Dhafra."); }
    return bad; },
  collect(form, keys) { const fd = new FormData(form), out = {};
    keys.forEach(k => { const el = form.querySelector(`[name="${k}"]`); if (!el) return;
      if (el.type === "checkbox") out[k] = el.checked;
      else if (["managing_org_id", "gp_id"].includes(k)) out[k] = fd.get(k) ? +fd.get(k) : null;
      else if (k === "has_insurance") { const x = fd.get(k); out[k] = x === "yes" ? true : x === "no" ? false : null; }
      else if (k === "insurance_preauth_required") out[k] = fd.get(k) === "yes" ? true : fd.get(k) === "no" ? false : null;
      else { const v = String(fd.get(k) ?? "").trim(); out[k] = v === "" ? null : v; } });
    if (keys.includes("emirate") && out.emirate && out.emirate !== "Abu Dhabi") out.region = null;
    // No / not answered: only the answer is sent (the hidden policy fields are never saved; the server strips them too)
    if (keys.includes("has_insurance") && out.has_insurance !== true) keys.forEach(k => { if (k !== "has_insurance") delete out[k]; });
    return out; },
  KEYS: { demographics: ["given_name", "family_name", "birth_date", "gender", "id_type", "id_number", "id_country", "nationality", "language"],
    contact: ["phone", "email", "address_line", "city", "emirate", "region", "preferred_channel", "whatsapp", "has_smartphone"],
    care: ["managing_org_id", "gp_id"],
    insurance: ["has_insurance", "insurance_payer", "insurance_plan", "insurance_member_id", "insurance_valid_to", "insurance_preauth_required"],
    contacts: ["nok_name", "nok_relationship", "nok_phone", "nok_is_guardian", "proxy_consent", "proxy_note"] },
  dupList(cands) { return `<ul class="dup-list">${cands.map(c => `<li><a href="/patients/${c.id}" target="_blank" rel="noopener"><b>${W.esc(c.name)}</b></a> · ${W.esc(c.mrn)} · born ${W.esc(c.birth_date || c.birth_year || "–")}${c.document ? " · " + W.esc(c.document.label) + " " + W.esc(c.document.number) : (c.emirates_id ? " · EID " + W.esc(c.emirates_id) : "")} <span class="chip sla-amber">${W.esc(c.why)}</span></li>`).join("")}</ul>`; },
  label(map, k) { return (map && map[k]) || k || "–"; },
};
