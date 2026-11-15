/* Staff UI localization (en / ar / hi / ur). Applied on load and on switcher change. */
(function () {
  const STRINGS = {
    en: {
      overview: "Overview", "work-queue": "Work queue", "todays-queue": "Today's queue",
      patients: "Patients", dashboard: "Dashboard", outcomes: "Outcomes",
      approvals: "Approvals", "audit-trail": "Audit trail", about: "About", more: "More",
      "sign-out": "Sign out", "change-password": "Change password",
      "next-step": "Next step", actions: "Actions", priority: "Priority",
      "needs-you-now": "Needs you now",
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
      advanced: "Advanced", "my-results": "My results", "content-sign-off": "Content sign-off",
      "Soon": "Soon",
      "Appointment booked": "Appointment booked",
      "Diabetes (HbA1c)": "Diabetes (HbA1c)",
      "Breast (mammogram)": "Breast (mammogram)",
      "Cervical (HPV)": "Cervical (HPV)",
      "Bowel (FIT)": "Bowel (FIT)",
      "Due for recall": "Due for recall",
      "Register a patient": "Register a patient",
      "Type 2+ characters to search.": "Type 2+ characters to search.",
      "Install app": "Install app",
      "next:": "next:",
      "now:": "now:",
      "Escalated L3": "Escalated L3",
      "Team pool": "Team pool",
      "Showing": "Showing",
      "Previous": "Previous",
      "Next": "Next",
      "of": "of",
      "speaks": "speaks",
      "Name, MRN, Emirates ID, passport or phone": "Name, MRN, Emirates ID, passport or phone",
      "KareShafaa is not a certified medical device.": "KareShafaa is not a certified medical device.",
      "Clinical decisions stay with the treating clinician.": "Clinical decisions stay with the treating clinician.",
      "Show the guide": "Show the guide",
      "Find a patient": "Find a patient",
      "Results inbox": "Results inbox",
      "Today's queue": "Today's queue",
      "due-for-recall": "Due for recall",
      "register-a-patient": "Register a patient",
      "find-a-patient": "Find a patient",
      "search-placeholder": "Name, MRN, Emirates ID, passport or phone",
      "type-to-search": "Type 2+ characters to search.",
    },
    ar: {
      overview: "نظرة عامة", "work-queue": "قائمة العمل", "todays-queue": "قائمة اليوم",
      patients: "المرضى", dashboard: "لوحة المعلومات", outcomes: "النتائج",
      approvals: "الموافقات", "audit-trail": "سجل التدقيق", about: "حول", more: "المزيد",
      "sign-out": "تسجيل الخروج", "change-password": "تغيير كلمة المرور",
      "next-step": "الخطوة التالية", actions: "الإجراءات", priority: "الأولوية",
      "needs-you-now": "يحتاجك الآن",
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
      advanced: "متقدم", "my-results": "نتائجي", "content-sign-off": "اعتماد المحتوى",
      "Soon": "قريباً",
      "Appointment booked": "تم حجز الموعد",
      "Diabetes (HbA1c)": "السكري (HbA1c)",
      "Breast (mammogram)": "الثدي (تصوير)",
      "Cervical (HPV)": "عنق الرحم (HPV)",
      "Bowel (FIT)": "الأمعاء (FIT)",
      "Due for recall": "موعد الاستدعاء",
      "Register a patient": "تسجيل مريض",
      "Type 2+ characters to search.": "اكتب حرفين أو أكثر للبحث.",
      "Install app": "تثبيت التطبيق",
      "next:": "التالي:",
      "now:": "الآن:",
      "Escalated L3": "مُصعَّد L3",
      "Team pool": "مجموعة الفريق",
      "Showing": "عرض",
      "Previous": "السابق",
      "Next": "التالي",
      "of": "من",
      "speaks": "يتحدث",
      "Name, MRN, Emirates ID, passport or phone": "الاسم أو رقم الملف أو الهوية أو الجواز أو الهاتف",
      "KareShafaa is not a certified medical device.": "KareShafaa ليس جهازاً طبياً معتمداً.",
      "Clinical decisions stay with the treating clinician.": "القرارات السريرية تبقى للطبيب المعالج.",
      "Show the guide": "عرض الدليل",
      "Find a patient": "ابحث عن مريض",
      "Results inbox": "صندوق النتائج",
      "Today's queue": "قائمة اليوم",
      "due-for-recall": "موعد الاستدعاء",
      "register-a-patient": "تسجيل مريض",
      "find-a-patient": "ابحث عن مريض",
      "search-placeholder": "الاسم أو رقم الملف أو الهوية أو الجواز أو الهاتف",
      "type-to-search": "اكتب حرفين أو أكثر للبحث.",
    },
    hi: {
      overview: "अवलोकन", "work-queue": "कार्य कतार", "todays-queue": "आज की कतार",
      patients: "मरीज़", dashboard: "डैशबोर्ड", outcomes: "परिणाम",
      approvals: "स्वीकृतियाँ", "audit-trail": "ऑडिट ट्रेल", about: "के बारे में", more: "और",
      "sign-out": "साइन आउट", "change-password": "पासवर्ड बदलें",
      "next-step": "अगला कदम", actions: "कार्रवाई", priority: "प्राथमिकता",
      "needs-you-now": "अभी आपको चाहिए",
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
      advanced: "उन्नत", "my-results": "मेरे परिणाम", "content-sign-off": "सामग्री साइन-ऑफ",
      "Soon": "जल्द",
      "Appointment booked": "अपॉइंटमेंट बुक",
      "Diabetes (HbA1c)": "मधुमेह (HbA1c)",
      "Breast (mammogram)": "स्तन (मैमोग्राम)",
      "Cervical (HPV)": "गर्भाशय (HPV)",
      "Bowel (FIT)": "आंत (FIT)",
      "Due for recall": "रिकॉल देय",
      "Register a patient": "मरीज़ पंजीकरण",
      "Type 2+ characters to search.": "खोजने के लिए 2+ अक्षर लिखें।",
      "Install app": "ऐप इंस्टॉल करें",
      "next:": "अगला:",
      "now:": "अभी:",
      "Escalated L3": "बढ़ाया L3",
      "Team pool": "टीम पूल",
      "Showing": "दिखा रहे",
      "Previous": "पिछला",
      "Next": "अगला",
      "of": "में से",
      "speaks": "बोलते हैं",
      "Name, MRN, Emirates ID, passport or phone": "नाम, MRN, Emirates ID, पासपोर्ट या फोन",
      "KareShafaa is not a certified medical device.": "KareShafaa प्रमाणित चिकित्सा उपकरण नहीं है।",
      "Clinical decisions stay with the treating clinician.": "नैदानिक निर्णय इलाज करने वाले चिकित्सक के पास रहते हैं।",
      "Show the guide": "गाइड दिखाएं",
      "Find a patient": "मरीज़ खोजें",
      "Results inbox": "परिणाम इनबॉक्स",
      "Today's queue": "आज की कतार",
      "due-for-recall": "रिकॉल देय",
      "register-a-patient": "मरीज़ पंजीकरण",
      "find-a-patient": "मरीज़ खोजें",
      "search-placeholder": "नाम, MRN, Emirates ID, पासपोर्ट या फोन",
      "type-to-search": "खोजने के लिए 2+ अक्षर लिखें।",
    },
    ur: {
      overview: "جائزہ", "work-queue": "کام کی قطار", "todays-queue": "آج کی قطار",
      patients: "مریض", dashboard: "ڈیش بورڈ", outcomes: "نتائج",
      approvals: "منظوریاں", "audit-trail": "آڈٹ ٹریل", about: "کے بارے میں", more: "مزید",
      "sign-out": "سائن آؤٹ", "change-password": "پاس ورڈ تبدیل کریں",
      "next-step": "اگلا قدم", actions: "اقدامات", priority: "ترجیح",
      "needs-you-now": "اب آپ کو چاہیے",
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
      "Urgent": "Urgent",
      "Call patient": "Call patient",
      "Unreachable": "Unreachable",
      "Patient notified": "Patient notified",
      "Treatment / surveillance started": "Treatment / surveillance started",
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
      "RISK": "RISK",
      "Patients": "Patients",
      "Search": "Search",
      "Register": "Register",
      "Sign in": "Sign in",
      "Sign out": "Sign out",
      "Change password": "Change password",
      "Overview": "Overview",
      "Work queue": "Work queue",
      "Approvals": "Approvals",
      "Outcomes": "Outcomes",
      "Audit trail": "Audit trail",
      "About": "About",
      "More": "More",
      "Soon": "Soon",
      "Appointment booked": "Appointment booked",
      "Diabetes (HbA1c)": "Diabetes (HbA1c)",
      "Breast (mammogram)": "Breast (mammogram)",
      "Cervical (HPV)": "Cervical (HPV)",
      "Bowel (FIT)": "Bowel (FIT)",
      "Due for recall": "Due for recall",
      "Register a patient": "Register a patient",
      "Type 2+ characters to search.": "Type 2+ characters to search.",
      "Install app": "Install app",
      "next:": "next:",
      "now:": "now:",
      "Escalated L3": "Escalated L3",
      "Team pool": "Team pool",
      "Showing": "Showing",
      "Previous": "Previous",
      "Next": "Next",
      "of": "of",
      "speaks": "speaks",
      "Name, MRN, Emirates ID, passport or phone": "نام، MRN، Emirates ID، پاسپورٹ یا فون",
      "KareShafaa is not a certified medical device.": "KareShafaa تصدیق شدہ طبی آلہ نہیں ہے۔",
      "Clinical decisions stay with the treating clinician.": "طبی فیصلے علاج کرنے والے معالج کے پاس رہتے ہیں۔",
      "Show the guide": "گائیڈ دکھائیں",
      "Find a patient": "مریض تلاش کریں",
      "Results inbox": "نتائج ان باکس",
      "Today's queue": "آج کی قطار",
      "due-for-recall": "ری کال واجب",
      "register-a-patient": "مریض رجسٹر کریں",
      "find-a-patient": "مریض تلاش کریں",
      "search-placeholder": "نام، MRN، Emirates ID، پاسپورٹ یا فون",
      "type-to-search": "تلاش کے لیے 2+ حروف لکھیں۔",
    },
    ar: {
      "Needs you now": "يحتاجك الآن",
      "All open cases": "كل الحالات المفتوحة",
      "Overdue": "متأخر",
      "Need review": "يحتاج مراجعة",
      "No owner": "بدون مالك",
      "Escalated": "مُصعَّد",
      "Urgent": "عاجل",
      "Call patient": "اتصل بالمريض",
      "Unreachable": "غير قابل للوصول",
      "Patient notified": "تم إبلاغ المريض",
      "Treatment / surveillance started": "بدأ العلاج / المراقبة",
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
      "RISK": "الخطر",
      "Patients": "المرضى",
      "Search": "بحث",
      "Register": "تسجيل",
      "Sign in": "تسجيل الدخول",
      "Sign out": "تسجيل الخروج",
      "Change password": "تغيير كلمة المرور",
      "Overview": "نظرة عامة",
      "Work queue": "قائمة العمل",
      "Approvals": "الموافقات",
      "Outcomes": "النتائج",
      "Audit trail": "سجل التدقيق",
      "About": "حول",
      "More": "المزيد",
      "Soon": "قريباً",
      "Appointment booked": "تم حجز الموعد",
      "Diabetes (HbA1c)": "السكري (HbA1c)",
      "Breast (mammogram)": "الثدي (تصوير)",
      "Cervical (HPV)": "عنق الرحم (HPV)",
      "Bowel (FIT)": "الأمعاء (FIT)",
      "Due for recall": "موعد الاستدعاء",
      "Register a patient": "تسجيل مريض",
      "Type 2+ characters to search.": "اكتب حرفين أو أكثر للبحث.",
      "Install app": "تثبيت التطبيق",
      "next:": "التالي:",
      "now:": "الآن:",
      "Escalated L3": "مُصعَّد L3",
      "Team pool": "مجموعة الفريق",
      "Showing": "عرض",
      "Previous": "السابق",
      "Next": "التالي",
      "speaks": "يتحدث",
      "Show the guide": "عرض الدليل"
    },
    hi: {
      "Needs you now": "अभी आपको चाहिए",
      "All open cases": "सभी खुले मामले",
      "Overdue": "अतिदेय",
      "Need review": "समीक्षा चाहिए",
      "No owner": "कोई मालिक नहीं",
      "Escalated": "बढ़ाया गया",
      "Urgent": "तत्काल",
      "Call patient": "मरीज़ को कॉल करें",
      "Unreachable": "संपर्क नहीं",
      "Patient notified": "मरीज़ को सूचित",
      "Treatment / surveillance started": "उपचार / निगरानी शुरू",
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
      "RISK": "जोखिम",
      "Patients": "मरीज़",
      "Search": "खोजें",
      "Register": "पंजीकरण",
      "Sign in": "साइन इन",
      "Sign out": "साइन आउट",
      "Change password": "पासवर्ड बदलें",
      "Overview": "अवलोकन",
      "Work queue": "कार्य कतार",
      "Approvals": "स्वीकृतियाँ",
      "Outcomes": "परिणाम",
      "Audit trail": "ऑडिट ट्रेल",
      "About": "के बारे में",
      "More": "और",
      "Soon": "जल्द",
      "Appointment booked": "अपॉइंटमेंट बुक",
      "Diabetes (HbA1c)": "मधुमेह (HbA1c)",
      "Breast (mammogram)": "स्तन (मैमोग्राम)",
      "Cervical (HPV)": "गर्भाशय (HPV)",
      "Bowel (FIT)": "आंत (FIT)",
      "Due for recall": "रिकॉल देय",
      "Register a patient": "मरीज़ पंजीकरण",
      "Type 2+ characters to search.": "खोजने के लिए 2+ अक्षर लिखें।",
      "Install app": "ऐप इंस्टॉल करें",
      "next:": "अगला:",
      "now:": "अभी:",
      "Escalated L3": "बढ़ाया L3",
      "Team pool": "टीम पूल",
      "Showing": "दिखा रहे",
      "Previous": "पिछला",
      "Next": "अगला",
      "speaks": "बोलते हैं",
      "Show the guide": "गाइड दिखाएं"
    },
    ur: {
      "Needs you now": "اب آپ کو چاہیے",
      "All open cases": "تمام کھلے کیسز",
      "Overdue": "تاخیر شدہ",
      "Need review": "جائزہ چاہیے",
      "No owner": "کوئی مالک نہیں",
      "Escalated": "بڑھا ہوا",
      "Urgent": "فوری",
      "Call patient": "مریض کو کال کریں",
      "Unreachable": "رابطہ نہیں",
      "Patient notified": "مریض کو مطلع",
      "Treatment / surveillance started": "علاج / نگرانی شروع",
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
      "RISK": "خطرہ",
      "Patients": "مریض",
      "Search": "تلاش",
      "Register": "رجسٹر",
      "Sign in": "سائن ان",
      "Sign out": "سائن آؤٹ",
      "Change password": "پاس ورڈ تبدیل کریں",
      "Overview": "جائزہ",
      "Work queue": "کام کی قطار",
      "Approvals": "منظوریاں",
      "Outcomes": "نتائج",
      "Audit trail": "آڈٹ ٹریل",
      "About": "کے بارے میں",
      "More": "مزید",
      "Soon": "جلد",
      "Appointment booked": "اپائنٹمنٹ بک",
      "Diabetes (HbA1c)": "ذیابیطس (HbA1c)",
      "Breast (mammogram)": "چھاتی (میموگرام)",
      "Cervical (HPV)": "سروائیکل (HPV)",
      "Bowel (FIT)": "آنت (FIT)",
      "Due for recall": "ری کال واجب",
      "Register a patient": "مریض رجسٹر کریں",
      "Type 2+ characters to search.": "تلاش کے لیے 2+ حروف لکھیں۔",
      "Install app": "ایپ انسٹال کریں",
      "next:": "اگلا:",
      "now:": "اب:",
      "Escalated L3": "بڑھا L3",
      "Team pool": "ٹیم پول",
      "Showing": "دکھا رہے",
      "Previous": "پچھلا",
      "Next": "اگلا",
      "speaks": "بولتے ہیں",
      "Show the guide": "گائیڈ دکھائیں"
    }
  };

  const ORIG = new WeakMap();
  function replaceText(lang) {
    // Translate from the original English only. Re-applying on already
    // translated text was mixing Hindi, Urdu and Arabic on the same screen.
    const target = TEXT[lang] || TEXT.en;
    const keys = Object.keys(TEXT.en).sort((a, b) => b.length - a.length);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(node => {
      const parent = node.parentElement;
      if (!parent || parent.closest("script, style, textarea, input, select, code, [data-i18n]")) return;
      let original = ORIG.get(node);
      if (original == null) {
        original = node.nodeValue || "";
        ORIG.set(node, original);
      }
      if (!original.trim()) return;
      let v = original;
      const trimmed = v.trim();
      if (target[trimmed]) {
        node.nodeValue = v.replace(trimmed, target[trimmed]);
        return;
      }
      keys.forEach(en => {
        if (en.length < 4) return; // short words cause mixed languages
        if (v.includes(en) && target[en]) v = v.split(en).join(target[en]);
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
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.placeholder = t(key);
    });
    // Replace common visible strings, then again after async queue cards render.
    replaceText(lang);
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
    // Re-apply when pages insert new content. Do not watch text changes:
    // our own replacements were triggering more translations and mixing languages.
    let pending = null;
    let applying = false;
    const origApply = apply;
    apply = function (lang) {
      applying = true;
      try { origApply(lang); } finally { applying = false; }
    };
    const obs = new MutationObserver(() => {
      if (applying) return;
      const lang = current();
      if (lang === "en") return;
      clearTimeout(pending);
      pending = setTimeout(() => { applying = true; try { replaceText(lang); } finally { applying = false; } }, 120);
    });
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.KS_I18N = { t, apply, current };
})();
