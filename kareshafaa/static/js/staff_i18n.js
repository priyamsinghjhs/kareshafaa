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

  // Common visible strings replaced on language change (queue, overview, cards).
  const TEXT = {
    en: {
      "Needs you now": "Needs you now",
      "All open cases": "All open cases",
      "Overdue": "Overdue",
      "Need review": "Need review",
      "No owner": "No owner",
      "Escalated": "Escalated",
      "Call patient": "Call patient",
      "Close the loop": "Close the loop",
      "Check understanding": "Check understanding",
      "Record attendance": "Record attendance",
      "Hello,": "Hello,",
      "Speaks": "Speaks",
      "Overdue by": "Overdue by",
      "days": "days",
      "Screening programme": "Screening programme",
      "Find a patient": "Find a patient",
      "Order": "Order",
      "Priority (needs you first)": "Priority (needs you first)",
      "What do the urgency levels mean?": "What do the urgency levels mean?",
      "URGENCY": "URGENCY",
      "PATIENT": "PATIENT",
      "SCREENING": "SCREENING",
      "NEXT STEP": "NEXT STEP",
      "DEADLINE": "DEADLINE",
      "OWNER": "OWNER",
      "RISK": "RISK"
    },
    ar: {
      "Needs you now": "يحتاجك الآن",
      "All open cases": "كل الحالات المفتوحة",
      "Overdue": "متأخر",
      "Need review": "يحتاج مراجعة",
      "No owner": "بدون مالك",
      "Escalated": "مُصعَّد",
      "Call patient": "اتصل بالمريض",
      "Close the loop": "أغلق الحلقة",
      "Check understanding": "تحقق من الفهم",
      "Record attendance": "تسجيل الحضور",
      "Hello,": "مرحباً،",
      "Speaks": "يتحدث",
      "Overdue by": "متأخر بـ",
      "days": "أيام",
      "Screening programme": "برنامج الفحص",
      "Find a patient": "ابحث عن مريض",
      "Order": "ترتيب",
      "Priority (needs you first)": "الأولوية (يحتاجك أولاً)",
      "What do the urgency levels mean?": "ماذا تعني مستويات الإلحاح؟",
      "URGENCY": "الإلحاح",
      "PATIENT": "المريض",
      "SCREENING": "الفحص",
      "NEXT STEP": "الخطوة التالية",
      "DEADLINE": "الموعد",
      "OWNER": "المالك",
      "RISK": "الخطر"
    },
    hi: {
      "Needs you now": "अभी आपको चाहिए",
      "All open cases": "सभी खुले मामले",
      "Overdue": "अतिदेय",
      "Need review": "समीक्षा चाहिए",
      "No owner": "कोई मालिक नहीं",
      "Escalated": "बढ़ाया गया",
      "Call patient": "मरीज़ को कॉल करें",
      "Close the loop": "लूप बंद करें",
      "Check understanding": "समझ जाँचें",
      "Record attendance": "उपस्थिति दर्ज करें",
      "Hello,": "नमस्ते,",
      "Speaks": "बोलते हैं",
      "Overdue by": "अतिदेय",
      "days": "दिन",
      "Screening programme": "जांच कार्यक्रम",
      "Find a patient": "मरीज़ खोजें",
      "Order": "क्रम",
      "Priority (needs you first)": "प्राथमिकता (पहले आपको)",
      "What do the urgency levels mean?": "तत्कालता स्तर का क्या अर्थ है?",
      "URGENCY": "तत्कालता",
      "PATIENT": "मरीज़",
      "SCREENING": "जांच",
      "NEXT STEP": "अगला कदम",
      "DEADLINE": "समय सीमा",
      "OWNER": "मालिक",
      "RISK": "जोखिम"
    },
    ur: {
      "Needs you now": "اب آپ کو چاہیے",
      "All open cases": "تمام کھلے کیسز",
      "Overdue": "تاخیر شدہ",
      "Need review": "جائزہ چاہیے",
      "No owner": "کوئی مالک نہیں",
      "Escalated": "بڑھا ہوا",
      "Call patient": "مریض کو کال کریں",
      "Close the loop": "لوپ بند کریں",
      "Check understanding": "سمجھ چیک کریں",
      "Record attendance": "حاضری درج کریں",
      "Hello,": "السلام علیکم،",
      "Speaks": "بولتے ہیں",
      "Overdue by": "تاخیر",
      "days": "دن",
      "Screening programme": "اسکریننگ پروگرام",
      "Find a patient": "مریض تلاش کریں",
      "Order": "ترتیب",
      "Priority (needs you first)": "ترجیح (پہلے آپ کو)",
      "What do the urgency levels mean?": "فوری سطح کا کیا مطلب ہے؟",
      "URGENCY": "فوری",
      "PATIENT": "مریض",
      "SCREENING": "اسکریننگ",
      "NEXT STEP": "اگلا قدم",
      "DEADLINE": "آخری تاریخ",
      "OWNER": "مالک",
      "RISK": "خطرہ"
    }
  };

  function replaceText(lang) {
    // Build a map from any known string (en or translated) back to the English key,
    // then to the target language. This lets switching back to English work.
    const toEn = {};
    Object.keys(TEXT).forEach(l => {
      Object.entries(TEXT[l]).forEach(([en, tr]) => { toEn[tr] = en; toEn[en] = en; });
    });
    const target = TEXT[lang] || {};
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(node => {
      let v = node.nodeValue;
      if (!v || !v.trim()) return;
      // try whole-string match first, then substring
      const trimmed = v.trim();
      if (toEn[trimmed] && target[toEn[trimmed]]) {
        node.nodeValue = v.replace(trimmed, target[toEn[trimmed]]);
        return;
      }
      Object.keys(target).forEach(en => {
        if (v.includes(en)) v = v.split(en).join(target[en]);
      });
      if (v !== node.nodeValue) node.nodeValue = v;
    });
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
    // Replace common visible strings in the page
    replaceText(lang);
    // Re-apply once more after async content (queue cards) has rendered
    setTimeout(() => replaceText(lang), 400);
    setTimeout(() => replaceText(lang), 1200);
    // Update switcher
    const sel = document.getElementById("staff-lang-sel");
    if (sel && sel.value !== lang) sel.value = lang;
  }

  function mountSwitcher() {
    const topbar = document.querySelector(".topbar .spacer");
    if (!topbar || document.getElementById("staff-lang")) return;
    const bar = document.createElement("div");
    bar.id = "staff-lang";
    bar.className = "lang-switch";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", t("language"));
    // single compact select: never wraps, works on mobile and desktop
    const sel = document.createElement("select");
    sel.id = "staff-lang-sel";
    sel.setAttribute("aria-label", t("language"));
    sel.style.cssText = "height:32px;padding:0 .4rem;font-size:.8rem;border-radius:6px;border:1px solid var(--c-border,#ccc);background:var(--c-surface,#fff);";
    Object.entries(LANG_LABEL).forEach(([code, label]) => {
      const o = document.createElement("option");
      o.value = code;
      o.textContent = label;
      o.lang = code;
      sel.appendChild(o);
    });
    sel.value = current();
    sel.addEventListener("change", () => apply(sel.value));
    bar.appendChild(sel);
    bar.style.cssText = "display:flex;align-items:center;margin-inline-end:.4rem;";
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
