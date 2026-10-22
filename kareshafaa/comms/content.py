"""Plain-language message content in English, Arabic, Hindi and Urdu.

PROTOTYPE CONTENT: written in plain language for the demo. Before any pilot every string must be
reviewed by the programme's clinical lead and by professional medical translators, and tested
with patients (readability / cultural appropriateness).
"""
from __future__ import annotations

SUPPORTED_LANGUAGES = {"en": "English", "ar": "العربية", "hi": "हिन्दी", "ur": "اردو"}
RTL_LANGUAGES = {"ar", "ur"}

PROGRAMME_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "colorectal_fit": {
            "name": "bowel screening (FIT)",
            "meaning": "Your bowel screening test found a small amount of blood in your stool sample. This is common and is usually NOT cancer, but it needs a follow-up check.",
            "next_step": "a camera test of the bowel (colonoscopy) at a hospital clinic"},
        "cervical": {
            "name": "cervical screening (HPV)",
            "meaning": "Your cervical screening test found HPV, a very common virus. Most HPV infections clear on their own, but some can cause cell changes, so we need to check further.",
            "next_step": "a follow-up test and, if needed, a closer look at the cervix (colposcopy)"},
        "breast": {
            "name": "breast screening (mammogram)",
            "meaning": "Your mammogram needs some more pictures or tests to give a clear result. Most women who are called back do NOT have cancer.",
            "next_step": "extra breast imaging and, if needed, a breast clinic visit"},
        "diabetes_hba1c": {
            "name": "diabetes screening (HbA1c)",
            "meaning": "Your blood sugar test (HbA1c) was higher than normal. This can be a sign of diabetes, which can be treated well.",
            "next_step": "a repeat blood test and a visit with your family doctor"},
    },
    "ar": {
        "colorectal_fit": {
            "name": "فحص القولون (اختبار البراز المناعي)",
            "meaning": "وجد فحص القولون كمية صغيرة من الدم في عينة البراز. هذا أمر شائع وغالباً لا يعني وجود سرطان، لكنه يحتاج إلى فحص متابعة.",
            "next_step": "فحص القولون بالمنظار في عيادة المستشفى"},
        "cervical": {
            "name": "فحص عنق الرحم (فيروس الورم الحليمي)",
            "meaning": "وجد الفحص فيروس الورم الحليمي البشري، وهو فيروس شائع جداً. معظم الحالات تزول من تلقاء نفسها، لكن بعضها قد يسبب تغيرات في الخلايا، لذلك نحتاج إلى فحص إضافي.",
            "next_step": "فحص متابعة، وإذا لزم الأمر فحص عنق الرحم بالمنظار المكبّر"},
        "breast": {
            "name": "فحص الثدي (الماموغرام)",
            "meaning": "تحتاج صورة الثدي الشعاعية إلى صور أو فحوصات إضافية للحصول على نتيجة واضحة. معظم النساء اللواتي يتم استدعاؤهن لا يكون لديهن سرطان.",
            "next_step": "تصوير إضافي للثدي، وإذا لزم الأمر زيارة عيادة الثدي"},
        "diabetes_hba1c": {
            "name": "فحص السكري (السكر التراكمي)",
            "meaning": "كانت نتيجة فحص السكر التراكمي أعلى من الطبيعي. قد يكون هذا علامة على مرض السكري، وهو مرض يمكن علاجه بشكل جيد.",
            "next_step": "إعادة فحص الدم وزيارة طبيب الأسرة"},
    },
    "hi": {
        "colorectal_fit": {
            "name": "आंत की जांच (FIT)",
            "meaning": "आपकी आंत की जांच में मल के नमूने में थोड़ा खून मिला है। यह आम बात है और आमतौर पर यह कैंसर नहीं होता, लेकिन इसकी आगे जांच ज़रूरी है।",
            "next_step": "अस्पताल में आंत की कैमरा जांच (कोलोनोस्कोपी)"},
        "cervical": {
            "name": "गर्भाशय ग्रीवा की जांच (HPV)",
            "meaning": "आपकी जांच में HPV मिला है, जो एक बहुत आम वायरस है। ज़्यादातर HPV संक्रमण अपने आप ठीक हो जाते हैं, लेकिन कुछ कोशिकाओं में बदलाव कर सकते हैं, इसलिए आगे जांच ज़रूरी है।",
            "next_step": "एक और जांच और ज़रूरत हो तो गर्भाशय ग्रीवा की नज़दीकी जांच (कोल्पोस्कोपी)"},
        "breast": {
            "name": "स्तन की जांच (मैमोग्राम)",
            "meaning": "साफ़ नतीजे के लिए आपके मैमोग्राम में कुछ और तस्वीरों या जांच की ज़रूरत है। दोबारा बुलाई गई ज़्यादातर महिलाओं को कैंसर नहीं होता।",
            "next_step": "स्तन की अतिरिक्त इमेजिंग और ज़रूरत हो तो स्तन क्लिनिक में मुलाक़ात"},
        "diabetes_hba1c": {
            "name": "मधुमेह की जांच (HbA1c)",
            "meaning": "आपकी ब्लड शुगर (HbA1c) जांच सामान्य से ज़्यादा आई है। यह मधुमेह का संकेत हो सकता है, जिसका अच्छा इलाज संभव है।",
            "next_step": "दोबारा खून की जांच और अपने फ़ैमिली डॉक्टर से मुलाक़ात"},
    },
    "ur": {
        "colorectal_fit": {
            "name": "آنت کی اسکریننگ (FIT)",
            "meaning": "آپ کے آنت کے ٹیسٹ میں پاخانے کے نمونے میں تھوڑا سا خون ملا ہے۔ یہ عام بات ہے اور عام طور پر یہ کینسر نہیں ہوتا، لیکن اس کا مزید معائنہ ضروری ہے۔",
            "next_step": "ہسپتال میں آنت کا کیمرہ ٹیسٹ (کولونوسکوپی)"},
        "cervical": {
            "name": "سروائیکل اسکریننگ (HPV)",
            "meaning": "آپ کے ٹیسٹ میں HPV پایا گیا ہے، جو ایک بہت عام وائرس ہے۔ زیادہ تر HPV انفیکشن خود ہی ختم ہو جاتے ہیں، لیکن کچھ خلیوں میں تبدیلی کر سکتے ہیں، اس لیے مزید معائنہ ضروری ہے۔",
            "next_step": "ایک اور ٹیسٹ اور ضرورت ہو تو سروکس کا قریبی معائنہ (کولپوسکوپی)"},
        "breast": {
            "name": "چھاتی کی اسکریننگ (میموگرام)",
            "meaning": "واضح نتیجے کے لیے آپ کے میموگرام کو کچھ مزید تصاویر یا ٹیسٹ کی ضرورت ہے۔ دوبارہ بلائی جانے والی زیادہ تر خواتین کو کینسر نہیں ہوتا۔",
            "next_step": "چھاتی کی اضافی امیجنگ اور ضرورت ہو تو بریسٹ کلینک کا دورہ"},
        "diabetes_hba1c": {
            "name": "ذیابیطس اسکریننگ (HbA1c)",
            "meaning": "آپ کے خون میں شوگر (HbA1c) کا ٹیسٹ معمول سے زیادہ آیا ہے۔ یہ ذیابیطس کی علامت ہو سکتی ہے، جس کا اچھا علاج ممکن ہے۔",
            "next_step": "دوبارہ خون کا ٹیسٹ اور اپنے فیملی ڈاکٹر سے ملاقات"},
    },
}

TIMEFRAME: dict[str, dict[str, str]] = {
    "en": {"critical": "today", "urgent": "within the next few days", "high": "within the next 2 weeks", "routine": "within the next month"},
    "ar": {"critical": "اليوم", "urgent": "خلال الأيام القليلة القادمة", "high": "خلال الأسبوعين القادمين", "routine": "خلال الشهر القادم"},
    "hi": {"critical": "आज ही", "urgent": "अगले कुछ दिनों में", "high": "अगले 2 हफ़्तों में", "routine": "अगले एक महीने में"},
    "ur": {"critical": "آج ہی", "urgent": "اگلے چند دنوں میں", "high": "اگلے 2 ہفتوں میں", "routine": "اگلے ایک مہینے میں"},
}

# Message templates. Two variants per message: "standard" and "simplified" (shorter sentences).
# Which variant is active per (category, language) is governed config, changed only via an
# approved proposal.
TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "result_notification": {
            "standard": "Hello {name}. This is your screening programme about your {programme} result. {meaning} Your next step: {next_step}, {timeframe}. We will help you arrange it. Please open your secure link to read more and confirm you understand: {link}",
            "simplified": "Hello {name}. Your {programme} result needs a check. {meaning} Next: {next_step}. When: {timeframe}. We will help you. Open: {link}"},
        "reminder": {
            "standard": "Hello {name}, this is a reminder from your screening team. Your next step: {next_step}. When: {timeframe}. Open {link} or ask to talk to us.",
            "simplified": "Hello {name}. This is a reminder. Your next step is {next_step}. When: {timeframe}. Open {link}."},
        "reassurance": {
            "standard": "You are not alone. A named care coordinator is looking after your follow-up and will guide you at every step. Most people who need a follow-up test do not have a serious illness. You can ask to talk to a clinician at any time.",
            "simplified": "You are not alone. We will guide you at every step. Most follow-up tests do not find a serious illness. You can talk to a clinician any time."},
        "no_show": {
            "standard": "Hello {name}, we missed you at your appointment. That is okay - we can find a time that suits you. Open {link} to rebook or tell us what got in the way.",
            "simplified": "Hello {name}. We missed you. Let us find a new time. Open {link}."},
        "appointment_confirmation": {
            "standard": "Hello {name}, your appointment is booked for {date}. It is for: {next_step}. Open {link} for directions and what to bring.",
            "simplified": "Hello {name}. Appointment: {date}. Open {link}."},
        "decline_ack": {
            "standard": "Thank you for telling us, {name}. It is your choice. A clinician will contact you to make sure you have all the information, and you can change your mind at any time.",
            "simplified": "Thank you, {name}. It is your choice. A clinician will call you. You can change your mind any time."},
    },
    "ar": {
        "result_notification": {
            "standard": "مرحباً {name}. هذه رسالة من برنامج الفحص بخصوص نتيجة {programme}. {meaning} خطوتك التالية: {next_step}، {timeframe}. سنساعدك في ترتيب ذلك. يرجى فتح الرابط الآمن لقراءة المزيد وتأكيد فهمك: {link}",
            "simplified": "مرحباً {name}. نتيجة {programme} تحتاج إلى فحص. {meaning} الخطوة التالية: {next_step}. متى: {timeframe}. سنساعدك. افتح: {link}"},
        "reminder": {
            "standard": "مرحباً {name}، تذكير من فريق الفحص. الخطوة التالية: {next_step}. متى: {timeframe}. افتح {link} أو اطلب التحدث معنا.",
            "simplified": "مرحباً {name}. تذكير: {next_step}. متى: {timeframe}. افتح {link}."},
        "reassurance": {
            "standard": "لست وحدك. منسق رعاية محدد يتابع حالتك وسيرشدك في كل خطوة. معظم الأشخاص الذين يحتاجون إلى فحص متابعة لا يكون لديهم مرض خطير. يمكنك طلب التحدث مع طبيب في أي وقت.",
            "simplified": "لست وحدك. سنرشدك في كل خطوة. معظم فحوصات المتابعة لا تجد مرضاً خطيراً. يمكنك التحدث مع طبيب في أي وقت."},
        "no_show": {
            "standard": "مرحباً {name}، افتقدناك في موعدك. لا بأس - يمكننا إيجاد وقت يناسبك. افتح {link} لإعادة الحجز أو أخبرنا بما منعك.",
            "simplified": "مرحباً {name}. افتقدناك. لنجد موعداً جديداً. افتح {link}."},
        "appointment_confirmation": {
            "standard": "مرحباً {name}، تم حجز موعدك في {date}. الموعد من أجل: {next_step}. افتح {link} للاطلاع على الاتجاهات وما يجب إحضاره.",
            "simplified": "مرحباً {name}. موعدك: {date}. افتح {link}."},
        "decline_ack": {
            "standard": "شكراً لإخبارنا يا {name}. القرار لك. سيتواصل معك طبيب للتأكد من حصولك على جميع المعلومات، ويمكنك تغيير رأيك في أي وقت.",
            "simplified": "شكراً {name}. القرار لك. سيتصل بك طبيب. يمكنك تغيير رأيك في أي وقت."},
    },
    "hi": {
        "result_notification": {
            "standard": "नमस्ते {name}। यह आपके {programme} के नतीजे के बारे में आपके स्क्रीनिंग कार्यक्रम का संदेश है। {meaning} आपका अगला कदम: {next_step}, {timeframe}। हम इसकी व्यवस्था में आपकी मदद करेंगे। ज़्यादा जानने और यह पुष्टि करने के लिए कि आप समझ गए हैं, कृपया अपना सुरक्षित लिंक खोलें: {link}",
            "simplified": "नमस्ते {name}। आपके {programme} के नतीजे की आगे जांच ज़रूरी है। {meaning} अगला कदम: {next_step}। कब: {timeframe}। हम मदद करेंगे। खोलें: {link}"},
        "reminder": {
            "standard": "नमस्ते {name}, आपकी स्क्रीनिंग टीम की ओर से याद दिलाना। अगला कदम: {next_step}। कब: {timeframe}। {link} खोलें या हमसे बात करने के लिए कहें।",
            "simplified": "नमस्ते {name}। याद दिलाना: {next_step}। कब: {timeframe}। {link} खोलें।"},
        "reassurance": {
            "standard": "आप अकेले नहीं हैं। एक नामित केयर कोऑर्डिनेटर आपकी देखभाल पर नज़र रख रहे हैं और हर कदम पर आपका मार्गदर्शन करेंगे। आगे की जांच वाले ज़्यादातर लोगों को कोई गंभीर बीमारी नहीं होती। आप कभी भी डॉक्टर से बात करने के लिए कह सकते हैं।",
            "simplified": "आप अकेले नहीं हैं। हम हर कदम पर आपके साथ हैं। ज़्यादातर जांचों में गंभीर बीमारी नहीं मिलती। आप कभी भी डॉक्टर से बात कर सकते हैं।"},
        "no_show": {
            "standard": "नमस्ते {name}, आप अपनी अपॉइंटमेंट पर नहीं आ पाए। कोई बात नहीं - हम आपके लिए सही समय ढूंढ सकते हैं। दोबारा बुक करने या रुकावट बताने के लिए {link} खोलें।",
            "simplified": "नमस्ते {name}। आप नहीं आ पाए। नया समय चुनें। {link} खोलें।"},
        "appointment_confirmation": {
            "standard": "नमस्ते {name}, आपकी अपॉइंटमेंट {date} को बुक है। यह इसके लिए है: {next_step}। रास्ता और क्या साथ लाना है, जानने के लिए {link} खोलें।",
            "simplified": "नमस्ते {name}। अपॉइंटमेंट: {date}। {link} खोलें।"},
        "decline_ack": {
            "standard": "हमें बताने के लिए धन्यवाद, {name}। यह आपका फ़ैसला है। एक डॉक्टर आपसे संपर्क करेंगे ताकि आपके पास पूरी जानकारी हो, और आप कभी भी अपना मन बदल सकते हैं।",
            "simplified": "धन्यवाद, {name}। यह आपका फ़ैसला है। एक डॉक्टर आपको फ़ोन करेंगे। आप कभी भी मन बदल सकते हैं।"},
    },
    "ur": {
        "result_notification": {
            "standard": "السلام علیکم {name}۔ یہ آپ کے {programme} کے نتیجے کے بارے میں آپ کے اسکریننگ پروگرام کا پیغام ہے۔ {meaning} آپ کا اگلا قدم: {next_step}، {timeframe}۔ ہم اس کے انتظام میں آپ کی مدد کریں گے۔ مزید پڑھنے اور سمجھنے کی تصدیق کے لیے اپنا محفوظ لنک کھولیں: {link}",
            "simplified": "السلام علیکم {name}۔ آپ کے {programme} کے نتیجے کا مزید معائنہ ضروری ہے۔ {meaning} اگلا قدم: {next_step}۔ کب: {timeframe}۔ ہم مدد کریں گے۔ کھولیں: {link}"},
        "reminder": {
            "standard": "السلام علیکم {name}، آپ کی اسکریننگ ٹیم کی طرف سے یاد دہانی۔ اگلا قدم: {next_step}۔ کب: {timeframe}۔ {link} کھولیں یا ہم سے بات کرنے کو کہیں۔",
            "simplified": "السلام علیکم {name}۔ یاد دہانی: {next_step}۔ کب: {timeframe}۔ {link} کھولیں۔"},
        "reassurance": {
            "standard": "آپ اکیلے نہیں ہیں۔ ایک نامزد کیئر کوآرڈینیٹر آپ کی دیکھ بھال پر نظر رکھے ہوئے ہیں اور ہر قدم پر آپ کی رہنمائی کریں گے۔ مزید ٹیسٹ والے زیادہ تر لوگوں کو کوئی سنگین بیماری نہیں ہوتی۔ آپ کسی بھی وقت ڈاکٹر سے بات کرنے کو کہہ سکتے ہیں۔",
            "simplified": "آپ اکیلے نہیں ہیں۔ ہم ہر قدم پر آپ کے ساتھ ہیں۔ زیادہ تر ٹیسٹوں میں سنگین بیماری نہیں ملتی۔ آپ کسی بھی وقت ڈاکٹر سے بات کر سکتے ہیں۔"},
        "no_show": {
            "standard": "السلام علیکم {name}، آپ اپنی ملاقات پر نہیں آ سکے۔ کوئی بات نہیں - ہم آپ کے لیے مناسب وقت ڈھونڈ سکتے ہیں۔ دوبارہ بک کرنے یا رکاوٹ بتانے کے لیے {link} کھولیں۔",
            "simplified": "السلام علیکم {name}۔ آپ نہیں آ سکے۔ نیا وقت چنیں۔ {link} کھولیں۔"},
        "appointment_confirmation": {
            "standard": "السلام علیکم {name}، آپ کی ملاقات {date} کو بک ہے۔ یہ اس کے لیے ہے: {next_step}۔ راستے اور ساتھ لانے والی چیزوں کے لیے {link} کھولیں۔",
            "simplified": "السلام علیکم {name}۔ ملاقات: {date}۔ {link} کھولیں۔"},
        "decline_ack": {
            "standard": "ہمیں بتانے کا شکریہ، {name}۔ یہ آپ کا فیصلہ ہے۔ ایک ڈاکٹر آپ سے رابطہ کریں گے تاکہ آپ کے پاس پوری معلومات ہوں، اور آپ کسی بھی وقت اپنا ارادہ بدل سکتے ہیں۔",
            "simplified": "شکریہ، {name}۔ یہ آپ کا فیصلہ ہے۔ ایک ڈاکٹر آپ کو فون کریں گے۔ آپ کسی بھی وقت ارادہ بدل سکتے ہیں۔"},
    },
}

# Teach-back understanding check. Keys of the correct answers are fixed per question.
TEACH_BACK_CORRECT = {"q1": "a", "q2": "b", "q3": "a"}
TEACH_BACK: dict[str, dict] = {
    "en": {
        "title": "Check your understanding",
        "intro": "Three quick questions help us make sure our message was clear. There are no wrong people - only unclear messages.",
        "q1": {"text": "What does your result mean?", "options": {
            "a": "I need a follow-up check. It does not mean I definitely have a serious illness.",
            "b": "Everything is normal. I do not need to do anything.",
            "c": "I definitely have a serious illness."}},
        "q2": {"text": "What is your next step?", "options": {
            "a": "Nothing - wait for next year's screening.", "b": "{next_step}",
            "c": "Go to the emergency department today."}},
        "q3": {"text": "How soon should it happen?", "options": {
            "a": "{timeframe}", "b": "Only if I feel unwell.", "c": "Next year."}},
        "confidence": "How confident do you feel about what happens next? (1 = not at all, 5 = very)",
        "submit": "Submit", "understand": "I understand", "talk": "I'd like to talk to a clinician",
        "decline": "I don't want this follow-up", "passed": "Thank you - you have understood your next step. We will guide you from here.",
        "failed": "Thank you. Some parts were not clear - a care navigator will call you to explain in your language.",
    },
    "ar": {
        "title": "تحقق من فهمك",
        "intro": "ثلاثة أسئلة سريعة تساعدنا على التأكد من أن رسالتنا كانت واضحة. لا توجد إجابات خاطئة من الأشخاص - فقط رسائل غير واضحة.",
        "q1": {"text": "ماذا تعني نتيجتك؟", "options": {
            "a": "أحتاج إلى فحص متابعة. هذا لا يعني بالتأكيد أنني مصاب بمرض خطير.",
            "b": "كل شيء طبيعي. لا أحتاج إلى فعل أي شيء.",
            "c": "أنا مصاب بالتأكيد بمرض خطير."}},
        "q2": {"text": "ما هي خطوتك التالية؟", "options": {
            "a": "لا شيء - أنتظر فحص العام القادم.", "b": "{next_step}", "c": "الذهاب إلى قسم الطوارئ اليوم."}},
        "q3": {"text": "متى يجب أن يتم ذلك؟", "options": {
            "a": "{timeframe}", "b": "فقط إذا شعرت بتوعك.", "c": "العام القادم."}},
        "confidence": "ما مدى ثقتك بما سيحدث بعد ذلك؟ (1 = لا ثقة، 5 = ثقة كبيرة)",
        "submit": "إرسال", "understand": "فهمت", "talk": "أرغب في التحدث مع طبيب",
        "decline": "لا أريد هذه المتابعة", "passed": "شكراً لك - لقد فهمت خطوتك التالية. سنرشدك من هنا.",
        "failed": "شكراً لك. بعض الأجزاء لم تكن واضحة - سيتصل بك منسق رعاية لشرحها بلغتك.",
    },
    "hi": {
        "title": "अपनी समझ जांचें",
        "intro": "तीन छोटे सवाल हमें यह पक्का करने में मदद करते हैं कि हमारा संदेश साफ़ था। गलती लोगों की नहीं - सिर्फ़ अस्पष्ट संदेशों की होती है।",
        "q1": {"text": "आपके नतीजे का क्या मतलब है?", "options": {
            "a": "मुझे आगे की जांच करानी है। इसका मतलब यह नहीं कि मुझे पक्का कोई गंभीर बीमारी है।",
            "b": "सब कुछ सामान्य है। मुझे कुछ करने की ज़रूरत नहीं है।",
            "c": "मुझे पक्का कोई गंभीर बीमारी है।"}},
        "q2": {"text": "आपका अगला कदम क्या है?", "options": {
            "a": "कुछ नहीं - अगले साल की जांच का इंतज़ार।", "b": "{next_step}", "c": "आज ही इमरजेंसी विभाग जाना।"}},
        "q3": {"text": "यह कितनी जल्दी होना चाहिए?", "options": {
            "a": "{timeframe}", "b": "सिर्फ़ तब जब तबीयत ख़राब लगे।", "c": "अगले साल।"}},
        "confidence": "आगे क्या होगा, इसे लेकर आप कितना भरोसा महसूस करते हैं? (1 = बिल्कुल नहीं, 5 = बहुत)",
        "submit": "जमा करें", "understand": "मैं समझ गया/गई", "talk": "मैं डॉक्टर से बात करना चाहता/चाहती हूँ",
        "decline": "मुझे यह फ़ॉलो-अप नहीं चाहिए", "passed": "धन्यवाद - आप अपना अगला कदम समझ गए हैं। हम आगे आपका मार्गदर्शन करेंगे।",
        "failed": "धन्यवाद। कुछ बातें साफ़ नहीं थीं - एक केयर नेविगेटर आपको आपकी भाषा में समझाने के लिए फ़ोन करेंगे।",
    },
    "ur": {
        "title": "اپنی سمجھ کی جانچ کریں",
        "intro": "تین مختصر سوالات ہمیں یہ یقینی بنانے میں مدد دیتے ہیں کہ ہمارا پیغام واضح تھا۔ غلطی لوگوں کی نہیں - صرف غیر واضح پیغامات کی ہوتی ہے۔",
        "q1": {"text": "آپ کے نتیجے کا کیا مطلب ہے؟", "options": {
            "a": "مجھے مزید معائنے کی ضرورت ہے۔ اس کا مطلب یہ نہیں کہ مجھے یقینی طور پر کوئی سنگین بیماری ہے۔",
            "b": "سب کچھ نارمل ہے۔ مجھے کچھ کرنے کی ضرورت نہیں۔",
            "c": "مجھے یقینی طور پر کوئی سنگین بیماری ہے۔"}},
        "q2": {"text": "آپ کا اگلا قدم کیا ہے؟", "options": {
            "a": "کچھ نہیں - اگلے سال کی اسکریننگ کا انتظار۔", "b": "{next_step}", "c": "آج ہی ایمرجنسی جانا۔"}},
        "q3": {"text": "یہ کتنی جلدی ہونا چاہیے؟", "options": {
            "a": "{timeframe}", "b": "صرف تب جب طبیعت خراب لگے۔", "c": "اگلے سال۔"}},
        "confidence": "آگے کیا ہوگا، اس بارے میں آپ کتنا اعتماد محسوس کرتے ہیں؟ (1 = بالکل نہیں، 5 = بہت زیادہ)",
        "submit": "جمع کریں", "understand": "میں سمجھ گیا/گئی", "talk": "میں ڈاکٹر سے بات کرنا چاہتا/چاہتی ہوں",
        "decline": "مجھے یہ فالو اپ نہیں چاہیے", "passed": "شکریہ - آپ اپنا اگلا قدم سمجھ گئے ہیں۔ ہم آگے آپ کی رہنمائی کریں گے۔",
        "failed": "شکریہ۔ کچھ باتیں واضح نہیں تھیں - ایک کیئر نیویگیٹر آپ کو آپ کی زبان میں سمجھانے کے لیے فون کریں گے۔",
    },
}

UI_TEXT: dict[str, dict[str, str]] = {
    "en": {"your_result": "Your result", "what_it_means": "What it means", "next_step": "Your next step",
           "when": "When", "your_team": "Who is looking after you", "messages": "Messages we sent you",
           "progress": "Your journey so far", "urgency": "How urgent"},
    "ar": {"your_result": "نتيجتك", "what_it_means": "ماذا تعني", "next_step": "خطوتك التالية", "when": "متى",
           "your_team": "من يتابع حالتك", "messages": "الرسائل التي أرسلناها لك", "progress": "رحلتك حتى الآن",
           "urgency": "مدى الاستعجال"},
    "hi": {"your_result": "आपका नतीजा", "what_it_means": "इसका मतलब", "next_step": "आपका अगला कदम", "when": "कब",
           "your_team": "आपकी देखभाल कौन कर रहा है", "messages": "हमारे भेजे गए संदेश", "progress": "अब तक की आपकी यात्रा",
           "urgency": "कितना ज़रूरी"},
    "ur": {"your_result": "آپ کا نتیجہ", "what_it_means": "اس کا مطلب", "next_step": "آپ کا اگلا قدم", "when": "کب",
           "your_team": "آپ کی دیکھ بھال کون کر رہا ہے", "messages": "ہمارے بھیجے گئے پیغامات",
           "progress": "اب تک آپ کا سفر", "urgency": "کتنا ضروری"},
}

# patient page: saved-answers lock ("Change my answers"); {when} = local date/time the answers were saved
ANSWER_LOCK_TEXT: dict[str, dict[str, str]] = {
    "en": {"saved_title": "Your saved answers", "saved_on": "Your answers were saved on {when}.",
           "change": "Change my answers", "change_q": "Change your answers?",
           "change_body": "Your saved answers stay in your record. You can then send new answers once.",
           "change_ok": "Yes, change my answers", "cancel": "Cancel", "keep": "Keep my saved answers",
           "reopened": "You can now send new answers once. Your earlier answers stay in your record.",
           "changed_note": "You changed your answers. Your earlier answers are kept in your record.",
           "locked": "Your answers are already saved. Choose \u201cChange my answers\u201d if you want to change them.",
           "no_change": "These answers can no longer be changed here. Please talk to your care team."},
    "ar": {"saved_title": "إجاباتك المحفوظة", "saved_on": "تم حفظ إجاباتك في {when}.",
           "change": "تغيير إجاباتي", "change_q": "هل تريد تغيير إجاباتك؟",
           "change_body": "ستبقى إجاباتك المحفوظة في سجلك. بعد ذلك يمكنك إرسال إجابات جديدة مرة واحدة.",
           "change_ok": "نعم، أريد تغيير إجاباتي", "cancel": "إلغاء", "keep": "الإبقاء على إجاباتي المحفوظة",
           "reopened": "يمكنك الآن إرسال إجابات جديدة مرة واحدة. ستبقى إجاباتك السابقة في سجلك.",
           "changed_note": "لقد غيّرت إجاباتك. إجاباتك السابقة محفوظة في سجلك.",
           "locked": "إجاباتك محفوظة بالفعل. اختر «تغيير إجاباتي» إذا أردت تغييرها.",
           "no_change": "لم يعد بالإمكان تغيير هذه الإجابات هنا. يرجى التحدث مع فريق الرعاية."},
    "hi": {"saved_title": "आपके सहेजे गए जवाब", "saved_on": "आपके जवाब {when} को सहेजे गए।",
           "change": "मेरे जवाब बदलें", "change_q": "क्या आप अपने जवाब बदलना चाहते हैं?",
           "change_body": "आपके सहेजे गए जवाब आपके रिकॉर्ड में रहेंगे। इसके बाद आप एक बार नए जवाब भेज सकते हैं।",
           "change_ok": "हाँ, मेरे जवाब बदलें", "cancel": "रद्द करें", "keep": "मेरे सहेजे गए जवाब रखें",
           "reopened": "अब आप एक बार नए जवाब भेज सकते हैं। आपके पिछले जवाब आपके रिकॉर्ड में रहेंगे।",
           "changed_note": "आपने अपने जवाब बदले हैं। आपके पिछले जवाब आपके रिकॉर्ड में सुरक्षित हैं।",
           "locked": "आपके जवाब पहले ही सहेजे जा चुके हैं। अगर आप उन्हें बदलना चाहते हैं तो \u201cमेरे जवाब बदलें\u201d चुनें।",
           "no_change": "ये जवाब अब यहाँ नहीं बदले जा सकते। कृपया अपनी देखभाल टीम से बात करें।"},
    "ur": {"saved_title": "آپ کے محفوظ کردہ جوابات", "saved_on": "آپ کے جوابات {when} کو محفوظ کیے گئے۔",
           "change": "میرے جوابات تبدیل کریں", "change_q": "کیا آپ اپنے جوابات تبدیل کرنا چاہتے ہیں؟",
           "change_body": "آپ کے محفوظ کردہ جوابات آپ کے ریکارڈ میں رہیں گے۔ اس کے بعد آپ ایک بار نئے جوابات بھیج سکتے ہیں۔",
           "change_ok": "جی ہاں، میرے جوابات تبدیل کریں", "cancel": "منسوخ کریں", "keep": "میرے محفوظ کردہ جوابات رکھیں",
           "reopened": "اب آپ ایک بار نئے جوابات بھیج سکتے ہیں۔ آپ کے پچھلے جوابات آپ کے ریکارڈ میں رہیں گے۔",
           "changed_note": "آپ نے اپنے جوابات تبدیل کیے ہیں۔ آپ کے پچھلے جوابات آپ کے ریکارڈ میں محفوظ ہیں۔",
           "locked": "آپ کے جوابات پہلے ہی محفوظ ہو چکے ہیں۔ اگر آپ انہیں تبدیل کرنا چاہتے ہیں تو «میرے جوابات تبدیل کریں» منتخب کریں۔",
           "no_change": "یہ جوابات اب یہاں تبدیل نہیں کیے جا سکتے۔ براہ کرم اپنی نگہداشت ٹیم سے بات کریں۔"},
}
for _k, _v in ANSWER_LOCK_TEXT.items():
    UI_TEXT[_k].update(_v)

# Patient-facing journey step names.
STATE_LABELS_I18N: dict[str, dict[str, str]] = {
    "en": {"issued": "Result ready", "triaged": "Reviewed by the team", "patient_notified": "You were told",
           "acknowledged": "You understood", "confirmatory_test": "Check-up test", "consultation": "Talk with a doctor",
           "referral_sent": "Referral sent", "referral_accepted": "Clinic accepted", "appointment_booked": "Appointment booked",
           "appointment_attended": "Appointment done", "treatment_or_surveillance_started": "Care plan started",
           "closed": "Completed", "unreachable": "We could not reach you", "no_show": "Missed appointment",
           "declined_pending_review": "You chose not to continue", "referral_rejected": "Finding the right clinic"},
    "ar": {"issued": "النتيجة جاهزة", "triaged": "راجعها الفريق", "patient_notified": "تم إبلاغك", "acknowledged": "لقد فهمت",
           "confirmatory_test": "فحص تأكيدي", "consultation": "التحدث مع طبيب", "referral_sent": "تم إرسال الإحالة",
           "referral_accepted": "قبلت العيادة", "appointment_booked": "تم حجز الموعد", "appointment_attended": "تم الموعد",
           "treatment_or_surveillance_started": "بدأت خطة الرعاية", "closed": "اكتمل", "unreachable": "لم نتمكن من الوصول إليك",
           "no_show": "موعد فائت", "declined_pending_review": "اخترت عدم المتابعة", "referral_rejected": "نبحث عن العيادة المناسبة"},
    "hi": {"issued": "नतीजा तैयार", "triaged": "टीम ने देखा", "patient_notified": "आपको बताया गया", "acknowledged": "आप समझ गए",
           "confirmatory_test": "पुष्टि जांच", "consultation": "डॉक्टर से बात", "referral_sent": "रेफ़रल भेजा गया",
           "referral_accepted": "क्लिनिक ने स्वीकार किया", "appointment_booked": "अपॉइंटमेंट बुक", "appointment_attended": "अपॉइंटमेंट पूरा",
           "treatment_or_surveillance_started": "देखभाल योजना शुरू", "closed": "पूरा हुआ", "unreachable": "हम आप तक नहीं पहुँच सके",
           "no_show": "छूटी अपॉइंटमेंट", "declined_pending_review": "आपने आगे न बढ़ना चुना", "referral_rejected": "सही क्लिनिक ढूंढ रहे हैं"},
    "ur": {"issued": "نتیجہ تیار", "triaged": "ٹیم نے دیکھا", "patient_notified": "آپ کو بتایا گیا", "acknowledged": "آپ سمجھ گئے",
           "confirmatory_test": "تصدیقی ٹیسٹ", "consultation": "ڈاکٹر سے بات", "referral_sent": "ریفرل بھیجا گیا",
           "referral_accepted": "کلینک نے قبول کیا", "appointment_booked": "ملاقات بک", "appointment_attended": "ملاقات مکمل",
           "treatment_or_surveillance_started": "دیکھ بھال کا منصوبہ شروع", "closed": "مکمل", "unreachable": "ہم آپ تک نہیں پہنچ سکے",
           "no_show": "چھوٹی ملاقات", "declined_pending_review": "آپ نے آگے نہ بڑھنے کا انتخاب کیا", "referral_rejected": "صحیح کلینک تلاش کر رہے ہیں"},
}


# Result-specific wording for "direct" pathways (no confirmatory step): the programme-level text above
# describes the commonest pathway; these override it so the patient is told the step that will actually
# happen. PROTOTYPE wording - needs clinical and professional translation review like everything above.
DIRECT_PATHWAY_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "cervical": {"meaning": "Your cervical screening test found a type of HPV (16 or 18) that is more likely to cause cell changes. This does NOT mean you have cancer, but it needs a closer look soon.",
                     "next_step": "a closer look at the cervix (colposcopy) at a hospital clinic"},
        "breast": {"meaning": "Your mammogram showed an area that needs a closer check. Many of these areas turn out NOT to be cancer, but a small tissue sample is the only way to be sure.",
                   "next_step": "a breast clinic visit for a small tissue sample (biopsy)"},
        "diabetes_hba1c": {"meaning": "Your blood sugar test (HbA1c) was very high. This needs to be seen by a doctor quickly. It can be treated well.",
                           "next_step": "a visit with a doctor today or tomorrow"},
    },
    "ar": {
        "cervical": {"meaning": "وجد فحص عنق الرحم نوعاً من فيروس الورم الحليمي (16 أو 18) أكثر احتمالاً لإحداث تغيرات في الخلايا. هذا لا يعني أنك مصابة بالسرطان، لكنه يحتاج إلى فحص دقيق قريباً.",
                     "next_step": "فحص عنق الرحم بالمنظار المكبّر في عيادة المستشفى"},
        "breast": {"meaning": "أظهرت صورة الثدي منطقة تحتاج إلى فحص أدق. كثير من هذه المناطق لا تكون سرطاناً، لكن أخذ عينة صغيرة من النسيج هو الطريقة الوحيدة للتأكد.",
                   "next_step": "زيارة عيادة الثدي لأخذ عينة صغيرة من النسيج (خزعة)"},
        "diabetes_hba1c": {"meaning": "كانت نتيجة فحص السكر التراكمي مرتفعة جداً. يجب أن يراك الطبيب بسرعة. يمكن علاج ذلك بشكل جيد.",
                           "next_step": "زيارة الطبيب اليوم أو غداً"},
    },
    "hi": {
        "cervical": {"meaning": "आपकी जांच में HPV का एक प्रकार (16 या 18) मिला है जिससे कोशिकाओं में बदलाव की संभावना ज़्यादा होती है। इसका मतलब कैंसर नहीं है, लेकिन जल्द नज़दीकी जांच ज़रूरी है।",
                     "next_step": "अस्पताल क्लिनिक में गर्भाशय ग्रीवा की नज़दीकी जांच (कोल्पोस्कोपी)"},
        "breast": {"meaning": "आपके मैमोग्राम में एक हिस्सा दिखा है जिसकी नज़दीकी जांच ज़रूरी है। ऐसे कई हिस्से कैंसर नहीं निकलते, लेकिन पक्का जानने का एक ही तरीका ऊतक का छोटा नमूना है।",
                   "next_step": "ऊतक के छोटे नमूने (बायोप्सी) के लिए स्तन क्लिनिक में मुलाक़ात"},
        "diabetes_hba1c": {"meaning": "आपकी ब्लड शुगर (HbA1c) जांच बहुत ज़्यादा आई है। डॉक्टर को जल्दी दिखाना ज़रूरी है। इसका अच्छा इलाज संभव है।",
                           "next_step": "आज या कल डॉक्टर से मुलाक़ात"},
    },
    "ur": {
        "cervical": {"meaning": "آپ کے ٹیسٹ میں HPV کی ایک قسم (16 یا 18) ملی ہے جس سے خلیوں میں تبدیلی کا امکان زیادہ ہوتا ہے۔ اس کا مطلب کینسر نہیں، لیکن جلد قریبی معائنہ ضروری ہے۔",
                     "next_step": "ہسپتال کلینک میں سروکس کا قریبی معائنہ (کولپوسکوپی)"},
        "breast": {"meaning": "آپ کے میموگرام میں ایک حصہ نظر آیا ہے جس کا قریبی معائنہ ضروری ہے۔ ایسے کئی حصے کینسر نہیں ہوتے، لیکن یقین کا واحد طریقہ ٹشو کا چھوٹا نمونہ ہے۔",
                   "next_step": "ٹشو کے چھوٹے نمونے (بایوپسی) کے لیے بریسٹ کلینک کا دورہ"},
        "diabetes_hba1c": {"meaning": "آپ کے خون میں شوگر (HbA1c) کا ٹیسٹ بہت زیادہ آیا ہے۔ ڈاکٹر کو جلد دکھانا ضروری ہے۔ اس کا اچھا علاج ممکن ہے۔",
                           "next_step": "آج یا کل ڈاکٹر سے ملاقات"},
    },
}

# Languages spoken in the synthetic population without reviewed content yet: patients get English plus an
# interpreter-supported navigator call. A one-line notice in their own language says so.
INTERPRETER_NOTICE: dict[str, str] = {
    "tl": "Tagalog: Hindi pa available ang mensaheng ito sa Tagalog. Tatawagan ka ng aming team na may interpreter.",
    "ml": "Malayalam: ഈ സന്ദേശം ഇപ്പോൾ മലയാളത്തിൽ ലഭ്യമല്ല. ഒരു ദ്വിഭാഷിയുടെ സഹായത്തോടെ ഞങ്ങളുടെ ടീം നിങ്ങളെ വിളിക്കും.",
}


# Result-specific wording keyed by clinical pathway (Iteration 6; translations Iteration 7).
# GOVERNED CONTENT: a pathway text is used for a patient only when its exact current wording (sha256) has a
# clinical sign-off and - for Arabic, Hindi and Urdu - also a professional translator's sign-off, by two different
# people (see kareshafaa/comms/governance.py). Until then: English text + navigator call with an interpreter for other
# languages, or a neutral "your team will call you" message if even the English wording is not signed off.
PATHWAY_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "repeat_fit": {"meaning": "Your bowel screening kit could not be tested (for example the sample was spoilt or "
                                  "unclear). This is NOT a result about your health. We need a new sample.",
                       "next_step": "a new home test kit, which we will send you"},
        "repeat_sample": {"meaning": "Your cervical screening sample could not be read by the lab. This is NOT a result "
                                     "about your health. We need a new sample.",
                          "next_step": "a repeat cervical screening sample at your clinic"},
        "prediabetes_gp": {"meaning": "Your blood sugar test was a little higher than normal (called prediabetes). This is "
                                      "NOT diabetes, but it means a higher chance of getting it. Small lifestyle changes "
                                      "can lower that chance.",
                           "next_step": "a visit with your family doctor for advice, and a repeat test within a year"},
        "surveillance_1y": {"meaning": "Your cervical screening test found HPV, a very common virus, but your cells looked "
                                       "normal. Most HPV infections clear on their own.",
                            "next_step": "a repeat HPV test in 12 months (we will remind you)"},
        "colposcopy_urgent": {"meaning": "Your cervical screening test found cell changes that need a closer look soon. "
                                         "This does NOT mean you have cancer.",
                              "next_step": "a closer look at the cervix (colposcopy) at a hospital clinic, within 2 weeks"},
    },
    # DRAFT translations (Iteration 7): NOT reviewed by a professional medical translator. They stay unused until a
    # translator and a clinician have both signed off the exact wording.
    "ar": {
        "repeat_fit": {"meaning": "لم نتمكن من فحص عينة اختبار القولون (مثلاً لأن العينة تلفت أو لم تكن واضحة). هذه ليست نتيجة عن صحتك. نحتاج إلى عينة جديدة.",
                       "next_step": "مجموعة اختبار منزلية جديدة سنرسلها إليك"},
        "repeat_sample": {"meaning": "لم يتمكن المختبر من قراءة عينة فحص عنق الرحم. هذه ليست نتيجة عن صحتك. نحتاج إلى عينة جديدة.",
                          "next_step": "أخذ عينة جديدة لفحص عنق الرحم في عيادتك"},
        "prediabetes_gp": {"meaning": "كانت نتيجة فحص السكر في الدم أعلى قليلاً من الطبيعي (تُسمى مرحلة ما قبل السكري). هذا ليس مرض السكري، لكنه يعني احتمالاً أكبر للإصابة به. تغييرات بسيطة في نمط الحياة يمكن أن تقلل هذا الاحتمال.",
                           "next_step": "زيارة طبيب الأسرة للحصول على النصيحة، وإعادة الفحص خلال سنة"},
        "surveillance_1y": {"meaning": "وجد فحص عنق الرحم فيروس الورم الحليمي البشري (HPV)، وهو فيروس شائع جداً، لكن خلاياك بدت طبيعية. معظم هذه العدوى تزول من تلقاء نفسها.",
                            "next_step": "إعادة فحص فيروس الورم الحليمي (HPV) بعد 12 شهراً (سنذكّرك بذلك)"},
        "colposcopy_urgent": {"meaning": "وجد فحص عنق الرحم تغيرات في الخلايا تحتاج إلى فحص أدق قريباً. هذا لا يعني أنك مصابة بالسرطان.",
                              "next_step": "فحص عنق الرحم بالمنظار المكبّر في عيادة المستشفى، خلال أسبوعين"},
    },
    "hi": {
        "repeat_fit": {"meaning": "आपकी आंत की जांच की किट की जांच नहीं हो सकी (जैसे नमूना ख़राब हो गया या साफ़ नहीं था)। यह आपकी सेहत के बारे में कोई नतीजा नहीं है। हमें एक नया नमूना चाहिए।",
                       "next_step": "घर पर जांच की एक नई किट, जो हम आपको भेजेंगे"},
        "repeat_sample": {"meaning": "लैब आपकी गर्भाशय ग्रीवा की जांच का नमूना नहीं पढ़ पाई। यह आपकी सेहत के बारे में कोई नतीजा नहीं है। हमें एक नया नमूना चाहिए।",
                          "next_step": "आपके क्लिनिक में गर्भाशय ग्रीवा की जांच का नया नमूना"},
        "prediabetes_gp": {"meaning": "आपकी ब्लड शुगर जांच सामान्य से थोड़ी ज़्यादा आई है (इसे प्रीडायबिटीज़ कहते हैं)। यह मधुमेह नहीं है, लेकिन इससे मधुमेह होने की संभावना बढ़ जाती है। रोज़मर्रा की आदतों में छोटे बदलाव इस संभावना को कम कर सकते हैं।",
                           "next_step": "सलाह के लिए अपने फ़ैमिली डॉक्टर से मुलाक़ात, और एक साल के अंदर दोबारा जांच"},
        "surveillance_1y": {"meaning": "आपकी जांच में HPV मिला है, जो एक बहुत आम वायरस है, लेकिन आपकी कोशिकाएं सामान्य दिखीं। ज़्यादातर HPV संक्रमण अपने आप ठीक हो जाते हैं।",
                            "next_step": "12 महीने बाद दोबारा HPV जांच (हम आपको याद दिलाएंगे)"},
        "colposcopy_urgent": {"meaning": "आपकी जांच में कोशिकाओं में कुछ बदलाव मिले हैं, जिनकी जल्द नज़दीकी जांच ज़रूरी है। इसका मतलब यह नहीं कि आपको कैंसर है।",
                              "next_step": "अस्पताल क्लिनिक में गर्भाशय ग्रीवा की नज़दीकी जांच (कोल्पोस्कोपी), 2 हफ़्तों के अंदर"},
    },
    "ur": {
        "repeat_fit": {"meaning": "آپ کی آنت کی اسکریننگ کٹ کا ٹیسٹ نہیں ہو سکا (مثلاً نمونہ خراب ہو گیا یا واضح نہیں تھا)۔ یہ آپ کی صحت کے بارے میں کوئی نتیجہ نہیں ہے۔ ہمیں ایک نیا نمونہ چاہیے۔",
                       "next_step": "گھر پر ٹیسٹ کی ایک نئی کٹ، جو ہم آپ کو بھیجیں گے"},
        "repeat_sample": {"meaning": "لیب آپ کی سروائیکل اسکریننگ کا نمونہ نہیں پڑھ سکی۔ یہ آپ کی صحت کے بارے میں کوئی نتیجہ نہیں ہے۔ ہمیں ایک نیا نمونہ چاہیے۔",
                          "next_step": "آپ کے کلینک میں سروائیکل اسکریننگ کا نیا نمونہ"},
        "prediabetes_gp": {"meaning": "آپ کے خون میں شوگر کا ٹیسٹ معمول سے تھوڑا زیادہ آیا ہے (اسے پری ذیابیطس کہتے ہیں)۔ یہ ذیابیطس نہیں ہے، لیکن اس سے ذیابیطس ہونے کا امکان بڑھ جاتا ہے۔ روزمرہ کی عادتوں میں چھوٹی تبدیلیاں اس امکان کو کم کر سکتی ہیں۔",
                           "next_step": "مشورے کے لیے اپنے فیملی ڈاکٹر سے ملاقات، اور ایک سال کے اندر دوبارہ ٹیسٹ"},
        "surveillance_1y": {"meaning": "آپ کے ٹیسٹ میں HPV پایا گیا ہے، جو ایک بہت عام وائرس ہے، لیکن آپ کے خلیے نارمل نظر آئے۔ زیادہ تر HPV انفیکشن خود ہی ختم ہو جاتے ہیں۔",
                            "next_step": "12 مہینے بعد دوبارہ HPV ٹیسٹ (ہم آپ کو یاد دلائیں گے)"},
        "colposcopy_urgent": {"meaning": "آپ کے ٹیسٹ میں خلیوں میں کچھ تبدیلیاں ملی ہیں، جن کا جلد قریبی معائنہ ضروری ہے۔ اس کا مطلب یہ نہیں کہ آپ کو کینسر ہے۔",
                              "next_step": "ہسپتال کلینک میں سروکس کا قریبی معائنہ (کولپوسکوپی)، 2 ہفتوں کے اندر"},
    },
}
PATHWAY_TEXT_EN = PATHWAY_TEXT["en"]
TRANSLATION_STATUS = {lang: "draft - needs professional medical translator review" for lang in ("ar", "hi", "ur")}

# Used when the pathway wording itself has no current clinical sign-off: never guess, ask the team to call.
NEUTRAL_PATHWAY_TEXT = {"meaning": "Your screening result is ready. Your care team will call you to explain what it "
                                   "means for you and to answer your questions.",
                        "next_step": "a call from your care team to explain your result and the next step"}


def _direct_applies(case) -> bool:
    rid = getattr(case, "rule_id", None)
    if rid is None:  # legacy cases created before the rules engine
        return True
    return {"cervical": rid == "CX-HPV1618", "breast": rid.startswith(("BR-4", "BR-5")),
            "diabetes_hba1c": rid.endswith("-CRIT")}.get(case.programme, False)


def programme_text(lang: str, case, live: set[str] | None = None) -> dict[str, str]:
    """Patient-facing wording for this case: programme text, overridden for result-specific direct pathways and
    for governed pathway wording (only versions with the required human sign-offs are used; see governance.py)."""
    lang = lang if lang in PROGRAMME_TEXT else "en"
    base = dict(PROGRAMME_TEXT[lang][case.programme])
    if case.needs_confirmation is False and _direct_applies(case):
        base.update(DIRECT_PATHWAY_TEXT[lang].get(case.programme, {}))
    pw = getattr(case, "pathway", None)
    if pw in PATHWAY_TEXT_EN:
        if live is None:
            from .governance import live_content  # noqa: PLC0415 - avoid an import cycle

            live = live_content(case)
        if lang != "en" and f"pathway:{pw}:{lang}" in live:
            base.update(PATHWAY_TEXT[lang][pw])
        elif f"pathway:{pw}:en" in live:
            base.update(PATHWAY_TEXT_EN[pw])
            if lang != "en":
                base["translation_pending"] = "true"
        else:
            base.update(NEUTRAL_PATHWAY_TEXT)
            base["content_pending_approval"] = "true"
            if lang != "en":
                base["translation_pending"] = "true"
    return base
