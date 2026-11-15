/* Staff UI localization (en / ar / hi / ur). Applied on load and on switcher change. */
(function () {
  const STRINGS = {
    en: {
      overview: "Overview", "work-queue": "Work queue", "todays-queue": "Today's queue",
      patients: "Patients", dashboard: "Dashboard", outcomes: "Outcomes",
      approvals: "Approvals", "audit-trail": "Audit trail", about: "About", more: "More",
      "sign-out": "Sign out", "change-password": "Change password",
      "next-step": "Next step", actions: "Actions", priority: "Priority",
      "open-cases": "Open cases", "closure-rate": "Closure rate", overdue: "Overdue",
      "synthetic": "Synthetic before/after", "pilot": "Pilot path",
      search: "Search", "record-attendance": "Record attendance", cancel: "Cancel",
      confirm: "Confirm", language: "Language",
      "needs-me": "Needs me", "care-summaries": "Care summaries",
      "ask-a-specialist": "Ask a specialist", "consult-requests": "Consult requests",
      "due-for-recall": "Due for recall", "results-inbox": "Results inbox",
      "my-cases": "My cases", "all-my-cases": "All my cases",
      "models-rules": "Models & rules", users: "Users", system: "System",
      "recall-list": "Recall list", "how-decisions-are-made": "How decisions are made",
      advanced: "Advanced", "my-results": "My results", "content-sign-off": "Content sign-off"
    },
    ar: {
      overview: "نظرة عامة", "work-queue": "قائمة العمل", "todays-queue": "قائمة اليوم",
      patients: "المرضى", dashboard: "لوحة المعلومات", outcomes: "النتائج",
      approvals: "الموافقات", "audit-trail": "سجل التدقيق", about: "حول", more: "المزيد",
      "sign-out": "تسجيل الخروج", "change-password": "تغيير كلمة المرور",
      "next-step": "الخطوة التالية", actions: "الإجراءات", priority: "الأولوية",
      "open-cases": "الحالات المفتوحة", "closure-rate": "معدل الإغلاق", overdue: "متأخر",
      "synthetic": "قبل/بعد (محاكاة)", "pilot": "مسار التجربة",
      search: "بحث", "record-attendance": "تسجيل الحضور", cancel: "إلغاء",
      confirm: "تأكيد", language: "اللغة",
      "needs-me": "يحتاجني", "care-summaries": "ملخصات الرعاية",
      "ask-a-specialist": "اسأل أخصائياً", "consult-requests": "طلبات الاستشارة",
      "due-for-recall": "موعد الاستدعاء", "results-inbox": "صندوق النتائج",
      "my-cases": "حالاتي", "all-my-cases": "كل حالاتي",
      "models-rules": "النماذج والقواعد", users: "المستخدمون", system: "النظام",
      "recall-list": "قائمة الاستدعاء", "how-decisions-are-made": "كيف تُتخذ القرارات",
      advanced: "متقدم", "my-results": "نتائجي", "content-sign-off": "اعتماد المحتوى"
    },
    hi: {
      overview: "अवलोकन", "work-queue": "कार्य कतार", "todays-queue": "आज की कतार",
      patients: "मरीज़", dashboard: "डैशबोर्ड", outcomes: "परिणाम",
      approvals: "स्वीकृतियाँ", "audit-trail": "ऑडिट ट्रेल", about: "के बारे में", more: "और",
      "sign-out": "साइन आउट", "change-password": "पासवर्ड बदलें",
      "next-step": "अगला कदम", actions: "कार्रवाई", priority: "प्राथमिकता",
      "open-cases": "खुले मामले", "closure-rate": "समापन दर", overdue: "अतिदेय",
      "synthetic": "पहले/बाद (सिंथेटिक)", "pilot": "पायलट पथ",
      search: "खोजें", "record-attendance": "उपस्थिति दर्ज करें", cancel: "रद्द करें",
      confirm: "पुष्टि करें", language: "भाषा",
      "needs-me": "मुझे चाहिए", "care-summaries": "देखभाल सारांश",
      "ask-a-specialist": "विशेषज्ञ से पूछें", "consult-requests": "परामर्श अनुरोध",
      "due-for-recall": "याद दिलाने के लिए", "results-inbox": "परिणाम इनबॉक्स",
      "my-cases": "मेरे मामले", "all-my-cases": "सभी मेरे मामले",
      "models-rules": "मॉडल और नियम", users: "उपयोगकर्ता", system: "सिस्टम",
      "recall-list": "याद सूची", "how-decisions-are-made": "निर्णय कैसे होते हैं",
      advanced: "उन्नत", "my-results": "मेरे परिणाम", "content-sign-off": "सामग्री साइन-ऑफ"
    },
    ur: {
      overview: "جائزہ", "work-queue": "کام کی قطار", "todays-queue": "آج کی قطار",
      patients: "مریض", dashboard: "ڈیش بورڈ", outcomes: "نتائج",
      approvals: "منظوریاں", "audit-trail": "آڈٹ ٹریل", about: "کے بارے میں", more: "مزید",
      "sign-out": "سائن آؤٹ", "change-password": "پاس ورڈ تبدیل کریں",
      "next-step": "اگلا قدم", actions: "اقدامات", priority: "ترجیح",
      "open-cases": "کھلے کیسز", "closure-rate": "بند ہونے کی شرح", overdue: "تاخیر شدہ",
      "synthetic": "پہلے/بعد (مصنوعی)", "pilot": "پائلٹ راستہ",
      search: "تلاش", "record-attendance": "حاضری درج کریں", cancel: "منسوخ",
      confirm: "تصدیق", language: "زبان",
      "needs-me": "مجھے چاہیے", "care-summaries": "نگہداشت کے خلاصے",
      "ask-a-specialist": "ماہر سے پوچھیں", "consult-requests": "مشورے کی درخواستیں",
      "due-for-recall": "یاد دہانی کے لیے", "results-inbox": "نتائج ان باکس",
      "my-cases": "میرے کیسز", "all-my-cases": "تمام میرے کیسز",
      "models-rules": "ماڈلز اور قواعد", users: "صارفین", system: "سسٹم",
      "recall-list": "یاد دہانی فہرست", "how-decisions-are-made": "فیصلے کیسے ہوتے ہیں",
      advanced: "اعلیٰ", "my-results": "میرے نتائج", "content-sign-off": "مواد سائن آف"
    }
  };

  const LANG_LABEL = { en: "EN", ar: "ع", hi: "हि", ur: "اردو" };
  const RTL = new Set(["ar", "ur"]);

  function current() {
    return localStorage.getItem("ks-staff-lang") || "en";
  }

  function t(key) {
    const lang = current();
    return (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
  }

  function apply(lang) {
    lang = STRINGS[lang] ? lang : "en";
    localStorage.setItem("ks-staff-lang", lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = RTL.has(lang) ? "rtl" : "ltr";
    // Translate elements marked with data-i18n
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.getAttribute("data-i18n");
      if (key && t(key) !== key) el.textContent = t(key);
    });
    // Translate title attributes
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      const key = el.getAttribute("data-i18n-title");
      if (key) el.title = t(key);
    });
    // Update switcher buttons
    document.querySelectorAll("[data-staff-lang]").forEach(btn => {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-staff-lang") === lang ? "true" : "false");
      btn.classList.toggle("secondary", btn.getAttribute("data-staff-lang") !== lang);
    });
  }

  function mountSwitcher() {
    const topbar = document.querySelector(".topbar .spacer");
    if (!topbar || document.getElementById("staff-lang")) return;
    const bar = document.createElement("div");
    bar.id = "staff-lang";
    bar.className = "lang-switch";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", t("language"));
    bar.style.cssText = "display:flex;gap:.25rem;margin-inline-end:.5rem;";
    Object.keys(LANG_LABEL).forEach(code => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary sm";
      b.setAttribute("data-staff-lang", code);
      b.setAttribute("lang", code);
      b.textContent = LANG_LABEL[code];
      b.addEventListener("click", () => apply(code));
      bar.appendChild(b);
    });
    topbar.parentNode.insertBefore(bar, topbar.nextSibling);
  }

  function init() {
    mountSwitcher();
    apply(current());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.KS_I18N = { t, apply, current };
})();
