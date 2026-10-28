"""Sections 1-2: purpose, scope, audience, definitions; system overview and access."""


def part_a(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    FIG, GREY = g["FIG"], g["GREY"]

    h1("1. Purpose, scope and audience")
    h2("1.1 Purpose")
    para("This document is the Standard Operating Procedure (SOP) and user operations manual for **KareShafaa**. KareShafaa turns every actionable screening result into a tracked case and carries it to care: one accountable owner at every step, a deadline for every step, escalation when a deadline is missed, and closure only with evidence or an authorised human decision.")
    para("It explains the end-to-end flow from result to closure, what each user role does at each step, and how the service is kept safe, governed and supported.")
    h2("1.2 Scope")
    table(["In scope", "Out of scope"], [[
        "Four screening programmes: bowel (FIT), cervical (HPV and cytology), breast (mammography) and diabetes (HbA1c and fasting glucose).\nThe case lifecycle from result to closure, including side states (unreachable, no-show, declined).\nEight user roles: seven staff roles and the patient.\nSafety, governance, exceptions, escalation, administration and support.",
        "The clinical guidelines themselves (they are summarised in the rules and in the code documentation).\nReal patient data: the service runs on fictional sample records (SYN- MRNs).\nDelivery of SMS and WhatsApp messages: these are simulated until a messaging provider is connected.\nHosting, contracts and certification needed for a real pilot (Section 6.3).",
    ]], [8.5, 8.5], size=9.5, zebra=False, valign="top", title="Scope of this document")
    h2("1.3 Audience")
    table(["Reader", "Role in KareShafaa", "Read first"], [
        ["Care coordinator (navigator)", "coordinator", "Sections 3 and 4.4"],
        ["Family doctor (GP)", "gp", "Sections 3 and 4.5"],
        ["Specialist doctor", "specialist", "Sections 3 and 4.6"],
        ["Clinical lead (also medical director for L4)", "clinical_lead", "Sections 3, 4.7 and 5"],
        ["Programme analyst", "analyst", "Sections 4.8 and 5.1"],
        ["System administrator", "admin", "Sections 4.9 and 6.3"],
        ["Medical translator", "translator", "Sections 4.10 and 5.8"],
        ["Patient (through staff and the patient page)", "patient", "Section 4.11"],
    ], [6.2, 3.6, 7.2], title="Who should read what", first_col_bold=True)
    h2("1.4 Definitions and abbreviations")
    table(["Term", "Meaning"], [
        ["Case", "One actionable screening result followed from issue to closure. It always has exactly one accountable owner."],
        ["Accountable owner", "The named person (or the programme team pool) responsible for the current step."],
        ["State / step", "Where the case is on the care pathway, for example *Patient notified* (Section 3.2)."],
        ["Tier (T0–T3)", "Urgency: T0 Critical, T1 Urgent, T2 Soon, T3 Routine (Appendix A)."],
        ["SLA / step deadline", "Time allowed for the current step, by state and tier (Appendix B)."],
        ["Pathway target", "End-to-end deadline from the result to reaching care, set by the clinical rule (Appendix C)."],
        ["Escalation (L1–L4)", "Alerts raised as a step deadline passes 50 %, 100 %, 150 % and 200 % (Section 3.4.14)."],
        ["Teach-back", "Three questions that check the patient understood the result, the next step and the timeframe."],
        ["Channel ladder", "Order of patient contact: app, then SMS, then WhatsApp, then a navigator phone call."],
        ["Closure guard", "Code rule that allows closure only with evidence or an authorised decision with a reason."],
        ["Clinical review (flag)", "A result the rules cannot handle safely; a clinician must review it before anything else happens."],
        ["e-consult", "A clinician-to-clinician question (*Ask a specialist*) with a response deadline."],
        ["Team pool", "Programme-level owner used when no named person is available; listed as *No named owner*."],
        ["Working day (wd)", "UAE working day, excluding weekends and public holidays; used for T2 and T3 clocks."],
        ["HL7 v2 ORU^R01 / FHIR R4", "Laboratory result message standards accepted by the Results inbox and the API."],
        ["ACK AA / AE", "HL7 acknowledgement: accepted, or error (message not processed)."],
        ["MRN", "Medical record number. Every sample record starts with SYN-."],
        ["PWA", "Progressive web app: the site can be installed on a phone or computer like an app."],
        ["RACI", "Responsible, Accountable, Consulted, Informed."],
        ["RBAC", "Role-based access control: permissions are fixed per role and checked on the server."],
    ], [4.4, 12.6], title="Definitions and abbreviations", first_col_bold=True)

    h1("2. System overview and access")
    h2("2.1 What KareShafaa does")
    bullets([
        "**Receives results** as HL7 v2 ORU^R01 or FHIR R4 messages, or as results entered in the Results inbox, and matches them to a patient.",
        "**Applies deterministic clinical rules** (no machine learning) to set the urgency tier, the pathway, the pathway target and the reason. Anything uncertain goes to clinical review.",
        "**Creates a case** with exactly one accountable owner and a deadline for every step, then follows it to closure.",
        "**Contacts the patient** in English, Arabic, Hindi or Urdu, checks understanding with a teach-back, and falls back to a navigator phone call.",
        "**Escalates** missed deadlines to named people, and **records everything** in an append-only, tamper-evident audit log.",
        "Uses an ML model **only to order outreach** within the same urgency. It never moves, triages or closes a case.",
    ])
    table(["Programme", "Screening test", "Confirmatory step", "Specialist service"], [
        ["Bowel (colorectal) – FIT", "Faecal immunochemical test (µg/g)", "None (direct to colonoscopy)", "Gastroenterology (colonoscopy)"],
        ["Cervical – HPV", "High-risk HPV DNA, reflex cytology", "Cervical cytology (reflex)", "Gynaecology (colposcopy)"],
        ["Breast – mammography", "Screening mammogram (BI-RADS)", "Diagnostic imaging / biopsy", "Breast clinic (breast surgery)"],
        ["Diabetes – HbA1c / fasting glucose", "HbA1c (% or mmol/mol), FPG", "Repeat HbA1c / fasting glucose", "Diabetes clinic (endocrinology)"],
    ], [4.4, 4.4, 4.0, 4.2], title="Screening programmes covered", first_col_bold=True)
    h2("2.2 Accessing the service")
    table(["Item", "Detail"], [
        ["Web address", "https://kareshafaa.onrender.com"],
        ["Mode", "Production mode: full workflows, password sign-in only, approval gates, a limit of 300 writes per minute per user, data never reset."],
        ["Devices", "Any modern browser on a laptop, tablet or phone. Screens adapt to phone width; buttons have 44-pixel tap targets."],
        ["Accounts", "Issued by the system administrator. Staff in clinical roles are linked to a staff directory record. Patient accounts are created from the patient record."],
        ["First start", "On the free hosting tier the service sleeps after 15 minutes without use; the first page after that takes about a minute to load."],
    ], [3.4, 13.6], title="Access details", first_col_bold=True)
    h2("2.3 Installing the app (PWA)")
    table(["Device", "How to install"], [
        ["Android (Chrome)", "Open the web address → menu (⋮) → *Install app* or *Add to Home screen*."],
        ["iPhone / iPad (Safari)", "Open the web address → Share → *Add to Home Screen*."],
        ["Windows / macOS (Chrome or Edge)", "Open the web address → install icon in the address bar → *Install*."],
    ], [5.2, 11.8], title="Installing KareShafaa as an app", first_col_bold=True)
    callout("What the installed app stores", [
        "The installed app caches only static files (styles, scripts, icons) and the offline page. It **never stores patient data, API responses or signed-in pages** on the device. Without a connection it shows an offline page; nothing is saved until the connection returns.",
    ])
    h2("2.4 Signing in, passwords and sessions")
    table(["Rule", "Detail"], [
        ["Sign-in", "Username and password. Error messages are generic and do not reveal whether the username exists."],
        ["Passwords", "At least 12 characters; not a common password; no repeated patterns or keyboard sequences; must not contain your name or username. Stored only as argon2id hashes."],
        ["Temporary passwords", "A new account or an administrator reset gives a temporary password; you must change it at the next sign-in."],
        ["Sessions", "End after 30 minutes without activity and after 8 hours in total. Signing out ends the session at once."],
        ["Lockout", "5 failed attempts in 15 minutes lock the account for 15 minutes; 20 failures from one network address in 15 minutes pause that address."],
        ["Protection", "Every change needs a per-session security (CSRF) token. Each user can make at most 300 changes per minute."],
        ["Audit", "Sign-in, failed sign-in, sign-out and every account change are recorded in the audit log."],
    ], [3.4, 13.6], title="Sign-in rules", first_col_bold=True)
    para("Never share a password or write it in a case note, message or this document. If you think a password is known to someone else, change it at once (account menu → *Change password*) and tell the system administrator.", size=9.5, color=GREY)
    figure(FIG / "login_pair.png", "Sign-in page on a laptop (left) and on a phone (right).", width_cm=15.5)
    h2("2.5 Languages")
    bullets([
        "**Staff screens** are in English.",
        "**The patient page and patient messages** are available in English, Arabic, Hindi and Urdu. Arabic and Urdu are shown right to left. The patient can switch the page language at the top of the page; the choice is saved for the page and does not change the language recorded for clinical messages.",
        "**Other languages:** Tagalog and Malayalam speakers receive English text plus a notice in their own language, and the system creates an interpreter-supported navigator phone call.",
        "The brand name **KareShafaa** is never translated.",
        "Translated patient text goes live only after a clinical sign-off and a translation sign-off by different people (Section 5.8).",
    ])
    h2("2.6 Screen layout and navigation")
    para("After sign-in each role lands on its own main task. The menu is on the left on a laptop and at the bottom on a phone. Your name and role are shown at the top right; the account menu has *Change password* and *Sign out*.")
    table(["Role", "Lands on", "Menu items"], [
        ["Care coordinator", "Today's queue", "Today's queue · Results inbox"],
        ["Family doctor (GP)", "Needs review", "Needs review · All my cases · Ask a specialist"],
        ["Specialist doctor", "Needs review", "Needs review · My cases · Consult requests"],
        ["Clinical lead", "Overview", "Overview · Work queue · Approvals · Outcomes · Audit trail"],
        ["Programme analyst", "Outcomes", "Outcomes · Models & rules"],
        ["System administrator", "Users", "Users · System · Outcomes · Advanced"],
        ["Medical translator", "Content sign-off", "Content sign-off"],
        ["Patient", "My results", "My results"],
    ], [4.0, 3.6, 9.4], title="Landing page and menu by role", first_col_bold=True)
    para("Opening another role's page is refused by the server, even if the address is typed directly. The footer of every page carries the prototype notice.", size=9.5, color=GREY)
