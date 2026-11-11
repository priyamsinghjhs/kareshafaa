// patient.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
let lang = new URLSearchParams(location.search).get("lang");
let draftQ = "", draftDn = "", draftTb = null;
const LANG_LABEL = { en: "English", ar: "العربية", hi: "हिन्दी", ur: "اردو" };
const EXTRA = {
  en: { more: "More", roles: { coordinator: "Care coordinator", gp: "Family doctor", specialist: "Specialist", clinical_lead: "Clinical lead" }, team: "Care team", ask: "Your question for the clinician", send: "Send", sent: "Sent - a clinician will reply.", declineNote: "Would you like to tell us why? (optional)", confirmDecline: "Are you sure? You can change your mind at any time.", done: "Thank you", steps: "Steps", tooShort: "Please write at least 5 characters.", langSaved: "Language saved for your next visit.", noCaseTitle: "Your account is ready", noCaseBody: "You have no screening result that needs follow-up at the moment. When a result arrives, you will see it here and your care team will contact you." },
  ar: { more: "المزيد", roles: { coordinator: "منسق الرعاية", gp: "طبيب الأسرة", specialist: "طبيب أخصائي", clinical_lead: "المسؤول السريري" }, team: "فريق الرعاية", ask: "سؤالك للطبيب", send: "إرسال", sent: "تم الإرسال - سيرد عليك طبيب.", declineNote: "هل تود إخبارنا بالسبب؟ (اختياري)", confirmDecline: "هل أنت متأكد؟ يمكنك تغيير رأيك في أي وقت.", done: "شكراً لك", steps: "الخطوات", tooShort: "يرجى كتابة 5 أحرف على الأقل.", langSaved: "تم حفظ اللغة لزيارتك القادمة.", noCaseTitle: "حسابك جاهز", noCaseBody: "لا توجد لديك حالياً نتيجة فحص تحتاج إلى متابعة. عندما تصل نتيجة ستظهر هنا وسيتواصل معك فريق الرعاية." },
  hi: { more: "और पढ़ें", roles: { coordinator: "केयर कोऑर्डिनेटर", gp: "फ़ैमिली डॉक्टर", specialist: "विशेषज्ञ डॉक्टर", clinical_lead: "क्लिनिकल लीड" }, team: "देखभाल टीम", ask: "डॉक्टर के लिए आपका सवाल", send: "भेजें", sent: "भेज दिया गया - एक डॉक्टर जवाब देंगे।", declineNote: "क्या आप हमें कारण बताना चाहेंगे? (वैकल्पिक)", confirmDecline: "क्या आप पक्का हैं? आप कभी भी मन बदल सकते हैं।", done: "धन्यवाद", steps: "कदम", tooShort: "कृपया कम से कम 5 अक्षर लिखें।", langSaved: "भाषा आपकी अगली विज़िट के लिए सहेज ली गई।", noCaseTitle: "आपका खाता तैयार है", noCaseBody: "अभी आपका कोई जांच परिणाम ऐसा नहीं है जिस पर आगे कार्रवाई चाहिए। परिणाम आने पर वह यहां दिखेगा और आपकी देखभाल टीम आपसे संपर्क करेगी।" },
  ur: { more: "مزید", roles: { coordinator: "کیئر کوآرڈینیٹر", gp: "فیملی ڈاکٹر", specialist: "ماہر ڈاکٹر", clinical_lead: "کلینیکل لیڈ" }, team: "نگہداشت ٹیم", ask: "ڈاکٹر کے لیے آپ کا سوال", send: "بھیجیں", sent: "بھیج دیا گیا - ایک ڈاکٹر جواب دیں گے۔", declineNote: "کیا آپ ہمیں وجہ بتانا چاہیں گے؟ (اختیاری)", confirmDecline: "کیا آپ کو یقین ہے؟ آپ کسی بھی وقت ارادہ بدل سکتے ہیں۔", done: "شکریہ", steps: "مراحل", tooShort: "براہ کرم کم از کم 5 حروف لکھیں۔", langSaved: "زبان آپ کے اگلے دورے کے لیے محفوظ ہو گئی۔", noCaseTitle: "آپ کا اکاؤنٹ تیار ہے", noCaseBody: "اس وقت آپ کا کوئی ایسا اسکریننگ نتیجہ نہیں جس پر مزید کارروائی کی ضرورت ہو۔ نتیجہ آنے پر وہ یہاں نظر آئے گا اور آپ کی نگہداشت ٹیم آپ سے رابطہ کرے گی۔" },
};
function wireLang(el) {
  el.querySelectorAll(".lang-switch button").forEach(b => b.onclick = async () => { const nl = b.dataset.l; if (nl === lang) return;
    try { await W.api("/api/me/display-language", { method: "POST", body: { language: nl } }); lang = nl; history.replaceState(null, "", "?lang=" + lang); await load(); W.toast(EXTRA[lang].langSaved, "ok"); }
    catch (e) { W.toast(e.message, "err"); } });
}
async function load() {
  const qEl = document.getElementById("q"), dnEl = document.getElementById("dn");
  if (qEl) draftQ = qEl.value; if (dnEl) draftDn = dnEl.value;
  const tbf = document.getElementById("tbf"); draftTb = tbf ? Object.fromEntries(new FormData(tbf)) : null;
  const d = await W.api("/api/me/case" + (lang ? "?lang=" + lang : ""));
  lang = d.language; const x = EXTRA[lang], u = d.ui, tb = d.teach_back;
  document.documentElement.lang = lang;
  const el = document.getElementById("p-root");
  el.className = "patient-wrap" + (d.rtl ? " rtl" : "");
  el.dir = d.rtl ? "rtl" : "ltr"; el.lang = lang;  // real dir/lang attributes for screen readers and shaping
  const langBar = `<div class="lang-switch" role="group" aria-label="Language">${Object.entries(LANG_LABEL).map(([k, v]) => `<button class="${k === lang ? "" : "secondary outline"}" data-l="${k}" lang="${k}" aria-pressed="${k === lang}">${v}</button>`).join("")}</div>`;
  const T = d.self || {};
  const jump = `<nav class="p-jump" aria-label="${W.esc(T.jump || "")}">${[["#self-appts", T.appts_title], ["#self-results", T.results_title], ["#pmsgs-card", T.msgs_title], d.case_id !== null ? ["#p-ask", tb ? tb.labels.talk : ""] : null, ["#self-details", T.details_title], ["#p-consent", T.consent_title]].filter(Boolean).map(([h, l]) => `<a href="${h}">${W.esc(l)}</a>`).join("")}</nav>`;
  const head = langBar
    + `<section class="card p-hero" aria-labelledby="p-hello"><h2 id="p-hello">${W.esc((T.hello || "{name}").replace("{name}", d.patient.given_name))}</h2><div id="ploop"></div></section>` + jump;
  if (d.case_id === null) { el.innerHTML = head + '<div id="pappts"></div><div id="pmsgs"></div><div id="pprofile"></div>';
    wireLang(el); window.renderProfile(d); return; }
  // the result itself is explained only once the care team has told the patient; after closure nothing stale is shown
  // visual pathway: how urgent -> what it means -> what to do next (emphasised) -> who is looking after you
  const URG = { critical: "tier-critical", urgent: "tier-urgent", high: "tier-high", routine: "tier-routine" };
  const sentences = t => { const m = String(t || "").match(/^(.+?[.!?؟۔।])(\s+[\s\S]+)?$/); return m ? [m[1], (m[2] || "").trim()] : [String(t || ""), ""]; };
  const [rs1, rsMore] = sentences(d.reassurance);
  const role = d.care_team && (x.roles[d.care_team.role] || x.team);
  const about = d.told && !d.closed ? `<section class="card p-path" id="p-about" aria-labelledby="p-about-h">
     <h3 id="p-about-h" class="p-path-title">${W.esc(u.your_result)}: ${W.esc(d.programme)}</h3>
     <div class="p-urgency"><span class="chip ${URG[d.urgency_level] || "tier-routine"}">${W.ic("clock")}<span class="sr-only">${W.esc(u.urgency)}: </span>${W.esc(d.urgency)}</span><span class="p-when"><b>${W.esc(u.when)}:</b> ${W.esc(d.timeframe)}</span></div>
     <div class="p-step"><h4 class="p-sec-h">${W.ic("file")}${W.esc(u.what_it_means)}</h4><p class="p-edu">${W.esc(d.meaning)}</p></div>
     <div class="p-step p-next"><h4 class="p-sec-h">${W.ic("arrow")}${W.esc(u.next_step)}</h4><p class="p-next-what"><b>${W.esc(d.next_step)}</b></p></div>
     <div class="p-step p-team"><h4 class="p-sec-h">${W.ic("users")}${W.esc(u.your_team)}</h4><p><b>${W.esc(d.care_team.owner)}</b>${role ? ` <span class="muted small">· ${W.esc(role)}</span>` : ""}</p></div>
     <p class="p-reassure">${W.ic("heart")}<span>${W.esc(rs1)}</span></p>${rsMore ? `<details class="help-more p-more"><summary>${W.esc(x.more)}</summary><p class="p-edu small">${W.esc(rsMore)}</p></details>` : ""}</section>` : "";
  const showTb = d.can_teach_back || (d.answers_lock && d.answers_lock.submitted) || d.last_teach_back;
  el.innerHTML = head + `
   ${d.interpreter_notice ? `<div class="card notice" role="note" lang="${W.esc(d.patient.preferred_language)}"><p class="p-sec-h">${W.ic("chat")}${W.esc(d.interpreter_notice)}</p></div>` : ""}
   ${about}
   ${showTb ? '<div class="card" id="tb"></div>' : ""}
   <div id="pappts"></div><div id="pmsgs"></div>
   <div class="card" id="p-ask"><h4 class="p-sec-h">${W.ic("chat")}${W.esc(tb.labels.talk)}</h4>${d.consults.map(c => `<div class="msg"><b>?</b> ${W.esc(c.question)}${c.response ? `<br><b>↳</b> ${W.esc(c.response)}` : ` <span class="chip sla-amber">…</span>`}</div>`).join("")}
     <label for="q">${x.ask}<textarea id="q" rows="3" minlength="5" maxlength="4000"></textarea></label><div class="tb-actions"><button id="ask">${x.send}</button></div></div>
   <div id="pprofile"></div>
   ${d.can_decline ? `<details class="p-decline"><summary class="small">${W.esc(tb.labels.decline)}</summary><label>${x.declineNote}<textarea id="dn" rows="2" maxlength="2000"></textarea></label><button class="secondary outline w-auto" id="decline">${W.esc(tb.labels.decline)}</button></details>` : ""}`;
  document.getElementById("q").value = draftQ; const dn0 = document.getElementById("dn"); if (dn0) dn0.value = draftDn;
  // the chosen language is saved for the next visit (messages and calls keep the recorded language)
  wireLang(el); window.renderProfile(d);
  const tbEl = document.getElementById("tb") || document.createElement("div"), L = d.answers_lock || {}, sub = L.submitted;
  const when = iso => { if (!iso) return "–"; const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    try { return t.toLocaleString({ en: "en-GB", ar: "ar-AE", hi: "hi-IN", ur: "ur-PK" }[lang] || undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Dubai" }); } catch (e) { return W.dt(iso); } };  // UAE clock, as local_display()
  const opts = (q, mark, ro) => q.options.map(o => { const on = mark && mark[q.id] === o.key;
    return `<label class="${ro && on ? "chosen" : ""}"><input type="radio" name="${q.id}" value="${o.key}"${on ? " checked" : ""}${ro ? " disabled" : " required"}> ${W.esc(o.text)}</label>`; }).join("");
  const confRow = (v, ro) => `<div class="q"><b>${W.esc(tb.confidence)}</b><div>${[1, 2, 3, 4, 5].map(n => `<label class="inline${ro && v === n ? " chosen" : ""}"><input type="radio" name="conf" value="${n}"${v === n ? " checked" : ""}${ro ? " disabled" : " required"}> ${n}</label>`).join("")}</div></div>`;
  if (d.can_teach_back) {
    const prev = L.reopened && sub ? sub.answers : null;  // changing: start from the saved answers
    tbEl.innerHTML = `<h4 class="p-sec-h tb-step">${W.ic("check")}${W.esc(tb.title)}</h4>${L.reopened ? `<div class="notice-inline info" role="status">${W.esc(u.reopened)}</div>` : ""}<form id="tbf">${tb.questions.map(q => `<div class="q"><b>${W.esc(q.text)}</b>${opts(q, prev, false)}</div>`).join("")}
      ${confRow(prev ? sub.confidence : null, false)}
      <div class="tb-actions"><button type="submit">${W.esc(tb.labels.submit)} – ${W.esc(tb.labels.understand)}</button>${L.reopened ? `<button type="button" class="secondary outline" id="tb-keep">${W.esc(u.keep)}</button>` : ""}</div></form>`;
    const f0 = document.getElementById("tbf");
    if (draftTb) Object.entries(draftTb).forEach(([k, v]) => { const i = f0.querySelector(`input[name="${k}"][value="${v}"]`); if (i) i.checked = true; });
    f0.onsubmit = async ev => { ev.preventDefault(); const btn = f0.querySelector("button[type=submit]"); btn.disabled = true; const f = Object.fromEntries(new FormData(ev.target));
      try { const r = await W.api("/api/me/teach-back", { method: "POST", body: { answers: { q1: f.q1, q2: f.q2, q3: f.q3 }, confidence: +f.conf, language: lang } });
        draftTb = null; f0.reset(); W.toast(r.passed ? tb.labels.passed : tb.labels.failed, r.passed ? "ok" : ""); load(); }
      catch (e) { const lk = e.code === "answers_locked"; W.formError(f0, lk ? u.locked : e.message); W.toast(lk ? u.locked : e.message, "err"); btn.disabled = false; if (lk) load(); } };
    const keep = document.getElementById("tb-keep");
    if (keep) keep.onclick = async () => { try { await W.api("/api/me/teach-back/reopen/cancel", { method: "POST", body: {} }); } catch (e) { W.toast(e.message, "err"); } load(); };
  } else if (sub) {
    const st = d.last_teach_back || sub;
    tbEl.innerHTML = `<h4 class="p-sec-h tb-step">${W.ic("ok")}${W.esc(u.saved_title)}</h4>
      <p>${st.passed ? "✅ " + W.esc(tb.labels.passed) : "ℹ️ " + W.esc(tb.labels.failed)}</p>
      <p class="small muted" id="tb-saved-on">${W.esc(u.saved_on.replace("{when}", when(sub.submitted_at)))}</p>
      ${L.changed ? `<p class="small">${W.esc(u.changed_note)}</p>` : ""}
      <fieldset class="q-saved" disabled aria-describedby="tb-saved-on"><legend class="sr-only">${W.esc(u.saved_title)}</legend>
      ${tb.questions.map(q => `<div class="q"><b>${W.esc(q.text)}</b>${opts(q, sub.answers, true)}</div>`).join("")}
      ${sub.confidence ? confRow(sub.confidence, true) : ""}</fieldset>
      <div class="tb-actions">${L.can_change ? `<button type="button" class="secondary outline" id="tb-change">${W.esc(u.change)}</button>` : `<p class="small muted">${W.esc(u.no_change)}</p>`}</div>`;
    const ch = document.getElementById("tb-change");
    if (ch) ch.onclick = async () => { if (!(await W.confirm(u.change_q, W.esc(u.change_body), { ok: u.change_ok, cancel: u.cancel, dir: d.rtl ? "rtl" : "ltr" }))) return;
      try { await W.api("/api/me/teach-back/reopen", { method: "POST", body: { confirm: true } }); await load();
        const first = document.querySelector("#tbf input"); if (first) first.focus(); } catch (e) { W.toast(e.message, "err"); } };
  } else {
    tbEl.innerHTML = `<h4 class="p-sec-h tb-step">${W.ic("check")}${W.esc(tb.title)}</h4>` + (d.last_teach_back ? `<p>${d.last_teach_back.passed ? "✅ " + W.esc(tb.labels.passed) : "ℹ️ " + W.esc(tb.labels.failed)}</p>` : `<p class="muted">–</p>`);
  }
  document.getElementById("ask").onclick = async ev => { const b = ev.currentTarget, ta = document.getElementById("q"), q = ta.value.trim(), box = ta.closest(".card");
    if (q.length < 5) { W.formError(box, x.tooShort); ta.focus(); return; }
    W.busy(b, true); W.formError(box, "");
    try { await W.api("/api/me/consult", { method: "POST", body: { question: q } }); ta.value = ""; draftQ = ""; W.toast(x.sent, "ok"); await load(); }
    catch (e) { W.formError(box, e.message); W.toast(e.message, "err"); W.busy(b, false); } };
  const dec = document.getElementById("decline");
  if (dec) dec.onclick = async () => { if (!(await W.confirm(x.confirmDecline, "", { ok: tb.labels.decline, danger: true, dir: d.rtl ? "rtl" : "ltr" }))) return;
    W.busy(dec, true);
    try { await W.api("/api/me/decline", { method: "POST", body: { note: document.getElementById("dn").value.trim() || "Patient declined via app" } }); draftDn = ""; W.toast(x.done, "ok"); await load(); }
    catch (e) { W.formError(dec.closest("details"), e.message); W.toast(e.message, "err"); W.busy(dec, false); } };
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  if (W.me.role !== "patient") { document.getElementById("p-root").innerHTML = W.empty("Patient view", "Sign in with a patient account.", "", "heart"); return; }
  try { await load(); } catch (e) { document.getElementById("p-root").innerHTML = `<div class="empty">${W.esc(e.message)}</div>`; } });
