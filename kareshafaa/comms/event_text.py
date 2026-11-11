"""Patient-facing wording for appointment and result emails (English, Arabic, Hindi, Urdu).

Clinical-safety notes, applied in every language:
- No 'demo' or 'prototype' wording.
- Critical and actionable results lead with 'your care team will contact you' / 'will go through this
  with you'. They do not use alarming words (critical, urgent, abnormal, cancer, emergency) as a flag.
- Negative / usual-range wording is separate from the actionable programme explanations.
- Prep and fasting instructions are omitted unless a stored, programme-signed instruction exists
  (this file does not invent medical prep).
"""
from __future__ import annotations

from .content import RTL_LANGUAGES, SUPPORTED_LANGUAGES

# Monday = 0, matching datetime.weekday().
WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "ar": ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
    "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    "ur": ("پیر", "منگل", "بدھ", "جمعرات", "جمعہ", "ہفتہ", "اتوار"),
}
MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
           "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
    "hi": ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई",
           "अगस्त", "सितंबर", "अक्तूबर", "नवंबर", "दिसंबर"),
    "ur": ("جنوری", "فروری", "مارچ", "اپریل", "مئی", "جون", "جولائی",
           "اگست", "ستمبر", "اکتوبر", "نومبر", "دسمبر"),
}

DATE_FMT = {
    "en": "{weekday}, {day} {month} {year} at {time} UAE time (Asia/Dubai)",
    "ar": "{weekday}، {day} {month} {year} في {time} توقيت الإمارات (آسيا/دبي)",
    "hi": "{weekday}, {day} {month} {year}, {time} यूएई समय (एशिया/दुबई)",
    "ur": "{weekday}، {day} {month} {year} کو {time} یو اے ای وقت (ایشیا/دبئی)",
}

ROLES = {
    "en": {"gp": "GP", "specialist": "Specialist", "coordinator": "Care coordinator",
           "clinical_lead": "Clinical lead", "team": "the clinic team"},
    "ar": {"gp": "طبيب الأسرة", "specialist": "أخصائي", "coordinator": "منسق الرعاية",
           "clinical_lead": "رئيس الفريق السريري", "team": "فريق العيادة"},
    "hi": {"gp": "फ़ैमिली डॉक्टर", "specialist": "विशेषज्ञ", "coordinator": "देखभाल संयोजक",
           "clinical_lead": "क्लिनिकल लीड", "team": "क्लिनिक की टीम"},
    "ur": {"gp": "خاندانی معالج", "specialist": "ماہر", "coordinator": "نگہداشت کوآرڈینیٹر",
           "clinical_lead": "کلینیکل لیڈ", "team": "کلینک کی ٹیم"},
}

SPECIALTIES = {
    "en": {"gastroenterology": "Gastroenterology", "gynaecology": "Gynaecology",
           "breast_surgery": "Breast surgery", "endocrinology": "Endocrinology",
           "diabetes": "Diabetes"},
    "ar": {"gastroenterology": "أمراض الجهاز الهضمي", "gynaecology": "أمراض النساء",
           "breast_surgery": "جراحة الثدي", "endocrinology": "الغدد الصماء",
           "diabetes": "السكري"},
    "hi": {"gastroenterology": "जठरांत्र विशेषज्ञता", "gynaecology": "स्त्री रोग",
           "breast_surgery": "स्तन शल्य चिकित्सा", "endocrinology": "अंतःस्राव विज्ञान",
           "diabetes": "मधुमेह"},
    "ur": {"gastroenterology": "نظام ہاضمہ", "gynaecology": "نسائی امراض",
           "breast_surgery": "چھاتی کی سرجری", "endocrinology": "غدود",
           "diabetes": "ذیابیطس"},
}

# Usual screening ranges, worded for the patient (not a diagnosis).
RANGE = {
    "en": {
        "hba1c_%": "below 5.7 %",
        "hba1c_mmol": "below 39 mmol/mol",
        "fpg_mgdl": "below 100 mg/dL",
        "fpg_mmoll": "below 5.6 mmol/L",
        "fit": "below 80 µg/g",
    },
    "ar": {
        "hba1c_%": "أقل من 5.7 ٪",
        "hba1c_mmol": "أقل من 39 ملمول/مول",
        "fpg_mgdl": "أقل من 100 ملغ/دل",
        "fpg_mmoll": "أقل من 5.6 ملمول/لتر",
        "fit": "أقل من 80 ميكروغرام/غرام",
    },
    "hi": {
        "hba1c_%": "5.7 % से कम",
        "hba1c_mmol": "39 mmol/mol से कम",
        "fpg_mgdl": "100 mg/dL से कम",
        "fpg_mmoll": "5.6 mmol/L से कम",
        "fit": "80 µg/g से कम",
    },
    "ur": {
        "hba1c_%": "5.7 ٪ سے کم",
        "hba1c_mmol": "39 mmol/mol سے کم",
        "fpg_mgdl": "100 mg/dL سے کم",
        "fpg_mmoll": "5.6 mmol/L سے کم",
        "fit": "80 µg/g سے کم",
    },
}

NEGATIVE_MEANING = {
    "en": {
        "colorectal_fit": "Your bowel screening test was in the usual range. No follow-up is needed from this test.",
        "cervical": "Your cervical screening test was in the usual range. No follow-up is needed from this test.",
        "breast": "Your breast screening test was in the usual range. No follow-up is needed from this test.",
        "diabetes_hba1c": "Your blood sugar screening test was in the usual range. No follow-up is needed from this test.",
    },
    "ar": {
        "colorectal_fit": "كانت نتيجة فحص القولون ضمن النطاق المعتاد. لا حاجة لمتابعة من هذا الفحص.",
        "cervical": "كانت نتيجة فحص عنق الرحم ضمن النطاق المعتاد. لا حاجة لمتابعة من هذا الفحص.",
        "breast": "كانت نتيجة فحص الثدي ضمن النطاق المعتاد. لا حاجة لمتابعة من هذا الفحص.",
        "diabetes_hba1c": "كانت نتيجة فحص السكر ضمن النطاق المعتاد. لا حاجة لمتابعة من هذا الفحص.",
    },
    "hi": {
        "colorectal_fit": "आपकी आंत की जांच सामान्य सीमा में आई है। इस जांच से आगे की कोई ज़रूरत नहीं है।",
        "cervical": "आपकी गर्भाशय ग्रीवा की जांच सामान्य सीमा में आई है। इस जांच से आगे की कोई ज़रूरत नहीं है।",
        "breast": "आपकी स्तन जांच सामान्य सीमा में आई है। इस जांच से आगे की कोई ज़रूरत नहीं है।",
        "diabetes_hba1c": "आपकी ब्लड शुगर जांच सामान्य सीमा में आई है। इस जांच से आगे की कोई ज़रूरत नहीं है।",
    },
    "ur": {
        "colorectal_fit": "آپ کا آنت کا ٹیسٹ معمول کی حد میں آیا ہے۔ اس ٹیسٹ سے مزید فالو اپ کی ضرورت نہیں۔",
        "cervical": "آپ کا سروائیکل ٹیسٹ معمول کی حد میں آیا ہے۔ اس ٹیسٹ سے مزید فالو اپ کی ضرورت نہیں۔",
        "breast": "آپ کا چھاتی کا ٹیسٹ معمول کی حد میں آیا ہے۔ اس ٹیسٹ سے مزید فالو اپ کی ضرورت نہیں۔",
        "diabetes_hba1c": "آپ کا بلڈ شوگر ٹیسٹ معمول کی حد میں آیا ہے۔ اس ٹیسٹ سے مزید فالو اپ کی ضرورت نہیں۔",
    },
}

# Shared chrome: greeting, labels, flags, leads, buttons, footer.
TEXT = {
    "en": {
        "brand": "KareShafaa",
        "hello": "Hello {name},",
        "open_app": "Open the patient app",
        "footer": "This message is from KareShafaa. Please do not reply to this email. "
                  "To ask a question or change an appointment, open the patient app.",
        "case_ref": "Case #{id}",
        "request_ref": "Request #{id}",
        "tz_note": "UAE time (Asia/Dubai)",
        "not_recorded": "Not recorded",
        "labels": {
            "programme": "Programme",
            "service": "Appointment for",
            "when": "Date and time",
            "previous": "Previous time",
            "clinician": "Clinician",
            "place": "Clinic or hospital",
            "address": "Address",
            "phone": "Phone",
            "department": "Department",
            "bring": "What to bring",
            "change": "If you cannot come",
            "reference": "Reference",
            "test": "Test",
            "collected": "Sample collected",
            "reported": "Result reported",
            "value": "Your result",
            "range": "Usual range",
            "flag": "What this means",
            "next": "What happens next",
            "who": "Who will contact you",
            "ordered_by": "Ordered by",
            "lab": "Laboratory or clinic",
        },
        "change_how": "Open the patient app to ask for a new time or to cancel. Your care team will confirm any change.",
        "bring_default": "Please bring a photo ID and your Emirates ID. Arrive a little early if you can.",
        "who_team": "Your care team",
        "who_named": "{name} ({role})",
        "flag": {
            "normal": "Within the usual range",
            "actionable": "Your care team will go through this result with you",
            "critical": "Your care team will contact you about this result",
        },
        "lead": {
            "normal": "Your screening result is ready.",
            "actionable": "Your care team will go through this result with you and help with the next step.",
            "critical": "Your care team will contact you about this result. You do not need to go to an "
                        "emergency department unless you feel unwell.",
        },
        "next_help": "We will help you arrange this.",
        "next_normal": "No follow-up is needed from this test. You will be invited when your next routine screening is due.",
        "subjects": {
            "appointment_confirmation": "KareShafaa: your appointment is booked",
            "appointment_changed": "KareShafaa: your appointment has been moved",
            "appointment_cancelled": "KareShafaa: your appointment was cancelled",
            "appointment_reminder": "KareShafaa: reminder of your appointment",
            "result_notification": "KareShafaa: your screening result",
        },
        "intro": {
            "appointment_confirmation": "Your appointment is booked. The details are below.",
            "appointment_changed": "Your appointment has been moved. The new details are below.",
            "appointment_cancelled": "Your appointment was cancelled. We will help you book a new time.",
            "appointment_reminder": "This is a reminder of your upcoming appointment. The details are below.",
            "result_notification": "A result from your screening programme is ready.",
        },
    },
    "ar": {
        "brand": "KareShafaa",
        "hello": "مرحباً {name}،",
        "open_app": "افتح تطبيق المريض",
        "footer": "هذه الرسالة من KareShafaa. يرجى عدم الرد على هذا البريد. "
                  "لطرح سؤال أو تغيير موعد، افتح تطبيق المريض.",
        "case_ref": "الحالة رقم {id}",
        "request_ref": "الطلب رقم {id}",
        "tz_note": "توقيت الإمارات (آسيا/دبي)",
        "not_recorded": "غير مسجّل",
        "labels": {
            "programme": "البرنامج",
            "service": "الموعد من أجل",
            "when": "التاريخ والوقت",
            "previous": "الوقت السابق",
            "clinician": "الطبيب",
            "place": "العيادة أو المستشفى",
            "address": "العنوان",
            "phone": "الهاتف",
            "department": "القسم",
            "bring": "ما الذي تحضره",
            "change": "إذا لم تتمكن من الحضور",
            "reference": "المرجع",
            "test": "الفحص",
            "collected": "تاريخ أخذ العينة",
            "reported": "تاريخ صدور النتيجة",
            "value": "نتيجتك",
            "range": "النطاق المعتاد",
            "flag": "ماذا تعني النتيجة",
            "next": "ماذا بعد ذلك",
            "who": "من سيتواصل معك",
            "ordered_by": "طلب الفحص",
            "lab": "المختبر أو العيادة",
        },
        "change_how": "افتح تطبيق المريض لطلب وقت جديد أو لإلغاء الموعد. سيؤكد فريق الرعاية أي تغيير.",
        "bring_default": "يرجى إحضار بطاقة هوية بصورة وبطاقة الهوية الإماراتية. حاول الحضور قبل الموعد بقليل.",
        "who_team": "فريق الرعاية",
        "who_named": "{name} ({role})",
        "flag": {
            "normal": "ضمن النطاق المعتاد",
            "actionable": "سيشرح لك فريق الرعاية هذه النتيجة",
            "critical": "سيتواصل معك فريق الرعاية بشأن هذه النتيجة",
        },
        "lead": {
            "normal": "نتيجة فحصك جاهزة.",
            "actionable": "سيشرح لك فريق الرعاية هذه النتيجة ويساعدك في الخطوة التالية.",
            "critical": "سيتواصل معك فريق الرعاية بشأن هذه النتيجة. لا حاجة للذهاب إلى قسم الطوارئ إلا إذا شعرت بتوعك.",
        },
        "next_help": "سنساعدك في ترتيب ذلك.",
        "next_normal": "لا حاجة لمتابعة من هذا الفحص. سندعوك عندما يحين موعد فحصك الروتيني التالي.",
        "subjects": {
            "appointment_confirmation": "KareShafaa: تم حجز موعدك",
            "appointment_changed": "KareShafaa: تم تغيير موعدك",
            "appointment_cancelled": "KareShafaa: تم إلغاء موعدك",
            "appointment_reminder": "KareShafaa: تذكير بموعدك",
            "result_notification": "KareShafaa: نتيجة فحصك",
        },
        "intro": {
            "appointment_confirmation": "تم حجز موعدك. التفاصيل أدناه.",
            "appointment_changed": "تم تغيير موعدك. التفاصيل الجديدة أدناه.",
            "appointment_cancelled": "تم إلغاء موعدك. سنساعدك في حجز وقت جديد.",
            "appointment_reminder": "هذا تذكير بموعدك القادم. التفاصيل أدناه.",
            "result_notification": "أصبحت نتيجة من برنامج الفحص جاهزة.",
        },
    },
    "hi": {
        "brand": "KareShafaa",
        "hello": "नमस्ते {name},",
        "open_app": "पेशेंट ऐप खोलें",
        "footer": "यह संदेश KareShafaa की ओर से है। कृपया इस ईमेल का जवाब न दें। "
                  "कोई सवाल पूछने या अपॉइंटमेंट बदलने के लिए पेशेंट ऐप खोलें।",
        "case_ref": "केस #{id}",
        "request_ref": "अनुरोध #{id}",
        "tz_note": "यूएई समय (एशिया/दुबई)",
        "not_recorded": "दर्ज नहीं",
        "labels": {
            "programme": "कार्यक्रम",
            "service": "अपॉइंटमेंट किस लिए",
            "when": "तारीख और समय",
            "previous": "पिछला समय",
            "clinician": "चिकित्सक",
            "place": "क्लिनिक या अस्पताल",
            "address": "पता",
            "phone": "फ़ोन",
            "department": "विभाग",
            "bring": "क्या साथ लाएँ",
            "change": "अगर आप नहीं आ सकते",
            "reference": "संदर्भ",
            "test": "जांच",
            "collected": "नमूना लिया गया",
            "reported": "नतीजा जारी हुआ",
            "value": "आपका नतीजा",
            "range": "सामान्य सीमा",
            "flag": "इसका मतलब",
            "next": "आगे क्या होगा",
            "who": "आपसे कौन संपर्क करेगा",
            "ordered_by": "जांच का आदेश",
            "lab": "प्रयोगशाला या क्लिनिक",
        },
        "change_how": "नया समय माँगने या रद्द करने के लिए पेशेंट ऐप खोलें। कोई भी बदलाव आपकी देखभाल टीम पक्का करेगी।",
        "bring_default": "कृपया फ़ोटो पहचान पत्र और अपनी एमिरेट्स आईडी साथ लाएँ। हो सके तो थोड़ा पहले पहुँचें।",
        "who_team": "आपकी देखभाल टीम",
        "who_named": "{name} ({role})",
        "flag": {
            "normal": "सामान्य सीमा में",
            "actionable": "आपकी देखभाल टीम यह नतीजा आपके साथ समझाएगी",
            "critical": "आपकी देखभाल टीम इस नतीजे के बारे में आपसे संपर्क करेगी",
        },
        "lead": {
            "normal": "आपकी जांच का नतीजा तैयार है।",
            "actionable": "आपकी देखभाल टीम यह नतीजा आपके साथ समझाएगी और अगले कदम में मदद करेगी।",
            "critical": "आपकी देखभाल टीम इस नतीजे के बारे में आपसे संपर्क करेगी। अगर आप अस्वस्थ महसूस नहीं कर रहे तो आपातकालीन विभाग जाने की ज़रूरत नहीं।",
        },
        "next_help": "हम इसे व्यवस्थित करने में आपकी मदद करेंगे।",
        "next_normal": "इस जांच से आगे की कोई ज़रूरत नहीं। अगली नियमित जांच के समय आपको आमंत्रित किया जाएगा।",
        "subjects": {
            "appointment_confirmation": "KareShafaa: आपकी अपॉइंटमेंट बुक हो गई",
            "appointment_changed": "KareShafaa: आपकी अपॉइंटमेंट का समय बदल गया",
            "appointment_cancelled": "KareShafaa: आपकी अपॉइंटमेंट रद्द हो गई",
            "appointment_reminder": "KareShafaa: अपॉइंटमेंट की याददिलाने वाली सूचना",
            "result_notification": "KareShafaa: आपकी जांच का नतीजा",
        },
        "intro": {
            "appointment_confirmation": "आपकी अपॉइंटमेंट बुक हो गई है। विवरण नीचे है।",
            "appointment_changed": "आपकी अपॉइंटमेंट का समय बदल गया है। नया विवरण नीचे है।",
            "appointment_cancelled": "आपकी अपॉइंटमेंट रद्द हो गई है। हम नया समय बुक करने में मदद करेंगे।",
            "appointment_reminder": "यह आपकी आने वाली अपॉइंटमेंट की याददिलाने वाली सूचना है। विवरण नीचे है।",
            "result_notification": "आपके स्क्रीनिंग कार्यक्रम का एक नतीजा तैयार है।",
        },
    },
    "ur": {
        "brand": "KareShafaa",
        "hello": "السلام علیکم {name}،",
        "open_app": "مریض کی ایپ کھولیں",
        "footer": "یہ پیغام KareShafaa کی طرف سے ہے۔ براہ کرم اس ای میل کا جواب نہ دیں۔ "
                  "سوال پوچھنے یا اپوائنٹمنٹ تبدیل کرنے کے لیے مریض کی ایپ کھولیں۔",
        "case_ref": "کیس نمبر {id}",
        "request_ref": "درخواست نمبر {id}",
        "tz_note": "یو اے ای وقت (ایشیا/دبئی)",
        "not_recorded": "درج نہیں",
        "labels": {
            "programme": "پروگرام",
            "service": "اپوائنٹمنٹ کس لیے",
            "when": "تاریخ اور وقت",
            "previous": "پچھلا وقت",
            "clinician": "معالج",
            "place": "کلینک یا ہسپتال",
            "address": "پتہ",
            "phone": "فون",
            "department": "محکمہ",
            "bring": "کیا ساتھ لائیں",
            "change": "اگر آپ نہیں آ سکتے",
            "reference": "حوالہ",
            "test": "ٹیسٹ",
            "collected": "نمونہ لیا گیا",
            "reported": "نتیجہ جاری ہوا",
            "value": "آپ کا نتیجہ",
            "range": "معمول کی حد",
            "flag": "اس کا مطلب",
            "next": "آگے کیا ہوگا",
            "who": "آپ سے کون رابطہ کرے گا",
            "ordered_by": "ٹیسٹ کا حکم",
            "lab": "لیبارٹری یا کلینک",
        },
        "change_how": "نیا وقت مانگنے یا منسوخ کرنے کے لیے مریض کی ایپ کھولیں۔ کوئی بھی تبدیلی آپ کی نگہداشت ٹیم تصدیق کرے گی۔",
        "bring_default": "براہ کرم تصویر والا شناختی کارڈ اور اپنی امارات آئی ڈی ساتھ لائیں۔ ہو سکے تو تھوڑی جلدی پہنچیں۔",
        "who_team": "آپ کی نگہداشت ٹیم",
        "who_named": "{name} ({role})",
        "flag": {
            "normal": "معمول کی حد میں",
            "actionable": "آپ کی نگہداشت ٹیم یہ نتیجہ آپ کے ساتھ سمجھائے گی",
            "critical": "آپ کی نگہداشت ٹیم اس نتیجے کے بارے میں آپ سے رابطہ کرے گی",
        },
        "lead": {
            "normal": "آپ کے ٹیسٹ کا نتیجہ تیار ہے۔",
            "actionable": "آپ کی نگہداشت ٹیم یہ نتیجہ آپ کے ساتھ سمجھائے گی اور اگلے قدم میں مدد کرے گی۔",
            "critical": "آپ کی نگہداشت ٹیم اس نتیجے کے بارے میں آپ سے رابطہ کرے گی۔ اگر آپ ناساز محسوس نہیں کر رہے تو ایمرجنسی ڈیپارٹمنٹ جانے کی ضرورت نہیں۔",
        },
        "next_help": "ہم اسے ترتیب دینے میں آپ کی مدد کریں گے۔",
        "next_normal": "اس ٹیسٹ سے مزید فالو اپ کی ضرورت نہیں۔ اگلی معمول کی اسکریننگ پر آپ کو مدعو کیا جائے گا۔",
        "subjects": {
            "appointment_confirmation": "KareShafaa: آپ کی اپوائنٹمنٹ بک ہو گئی",
            "appointment_changed": "KareShafaa: آپ کی اپوائنٹمنٹ کا وقت بدل گیا",
            "appointment_cancelled": "KareShafaa: آپ کی اپوائنٹمنٹ منسوخ ہو گئی",
            "appointment_reminder": "KareShafaa: اپوائنٹمنٹ کی یاددہانی",
            "result_notification": "KareShafaa: آپ کے ٹیسٹ کا نتیجہ",
        },
        "intro": {
            "appointment_confirmation": "آپ کی اپوائنٹمنٹ بک ہو گئی ہے۔ تفصیل نیچے ہے۔",
            "appointment_changed": "آپ کی اپوائنٹمنٹ کا وقت بدل گیا ہے۔ نئی تفصیل نیچے ہے۔",
            "appointment_cancelled": "آپ کی اپوائنٹمنٹ منسوخ ہو گئی ہے۔ ہم نیا وقت بک کرنے میں مدد کریں گے۔",
            "appointment_reminder": "یہ آپ کی آنے والی اپوائنٹمنٹ کی یاددہانی ہے۔ تفصیل نیچے ہے۔",
            "result_notification": "آپ کے اسکریننگ پروگرام کا ایک نتیجہ تیار ہے۔",
        },
    },
}

# Words we never put in a patient-facing flag or lead (English stem; checked case-insensitively).
ALARM_WORDS = ("critical", "urgent", "abnormal", "cancer", "malignant", "emergency", "alarm",
               "حرج", "عاجل", "سرطان", "طوارئ", "गंभीर", "कैंसर", "आपातकाल", "خطرناک", "کینسر", "ایمرجنسی")


def texts(lang: str) -> dict:
    return TEXT[lang if lang in TEXT else "en"]


def is_rtl(lang: str) -> bool:
    return lang in RTL_LANGUAGES


__all__ = ["ALARM_WORDS", "DATE_FMT", "MONTHS", "NEGATIVE_MEANING", "RANGE", "ROLES", "RTL_LANGUAGES",
           "SPECIALTIES", "SUPPORTED_LANGUAGES", "TEXT", "WEEKDAYS", "is_rtl", "texts"]
