"""Title page, document control and the contents placeholder."""
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from lib import add_rich, set_font, para_border, NAVY, GREY, DARK, MINT_HEX

APP_URL = "https://kareshafaa.onrender.com"


def build(g, version, date):
    doc, table, para, callout = g["doc"], g["table"], g["para"], g["callout"]
    LOGO = g["FIG"].parent.parent / "assets" / "logo"
    # ---- title page (logo clear space = 1/4 badge height: badge ~2.3 cm -> >= 0.6 cm around it)
    p = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    p.paragraph_format.space_before = Pt(36); p.paragraph_format.space_after = Pt(40)
    p.add_run().add_picture(str(LOGO / "logo-horizontal.png"), width=Cm(8.2))
    t = doc.add_paragraph(); r = t.add_run("Operating Document"); set_font(r, size=30, bold=True, color=NAVY)
    t.paragraph_format.space_after = Pt(2)
    t = doc.add_paragraph(); r = t.add_run("Standard Operating Procedure and User Operations Manual"); set_font(r, size=15, color=DARK)
    para_border(t, "bottom", MINT_HEX, 18, 8); t.paragraph_format.space_after = Pt(18)
    t = doc.add_paragraph(); r = t.add_run("Closing the Screening-to-Care Loop"); set_font(r, size=13, bold=True, color=NAVY)
    t.paragraph_format.space_after = Pt(2)
    t = doc.add_paragraph(); r = t.add_run("Hack2skill Future Health Digital Innovation Challenge (FHDIC) · Challenge 05"); set_font(r, size=10.5, color=GREY)
    t.paragraph_format.space_after = Pt(22)
    table([], [["Version", version], ["Date", date], ["Prepared by", "Priyam Singh"],
               ["Applies to", "KareShafaa v0.1 (code at master aac2f3d), production mode"],
               ["Service", f"{APP_URL} (web and installable app)"],
               ["Status", "Issued for the FHDIC Challenge 05 submission"]],
          [3.6, 13.4], header=False, zebra=False, first_col_bold=True, size=10)
    callout("Important notices", [
        "**Prototype software, not a certified medical device.** Clinical decisions remain with the treating clinician.",
        "**Patient messages are simulated.** SMS and WhatsApp messages are recorded in the case but not delivered to phones until a messaging provider is connected.",
        "**Sample records only.** All patients are fictional and carry SYN- medical record numbers. No real person's data is used.",
        "**Clinical targets marked CONFIRM** in the rules engine are awaiting clinical-lead sign-off before any real use.",
    ])
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- document control
    t = doc.add_paragraph(); r = t.add_run("Document control"); set_font(r, size=18, bold=True, color=NAVY)
    para_border(t, "bottom", MINT_HEX, 12, 4); t.paragraph_format.space_after = Pt(8)
    table(["Item", "Detail"], [
        ["Document title", "KareShafaa Operating Document (SOP and User Operations Manual)"],
        ["Document owner", "Priyam Singh (KareShafaa)"],
        ["Version / date", f"{version} / {date}"],
        ["Software covered", "KareShafaa v0.1, state machine v1.1.0, repository master at commit aac2f3d"],
        ["Sources", "Application code and documentation (README, DEPLOY, docs/), and the Challenge 05 solution design. Where they differ, this document follows the code."],
        ["Audience", "Care coordinators, family doctors, specialists, clinical leads, programme analysts, system administrators, medical translators and the patients they serve (Section 1.3)"],
        ["Distribution", "FHDIC Challenge 05 submission; programme staff"],
        ["Review cycle", "Review on every release that changes roles, states, deadlines or rules, and at least every 6 months"],
    ], [4.2, 12.8], title="Document details", first_col_bold=True)
    table(["Version", "Date", "Author", "Change"], [
        ["1.0", "24 Oct 2026", "Priyam Singh", "First issue."],
        ["1.1", "28 Oct 2026", "Priyam Singh", "Separated clinical lead and system administrator duties to match the code (aac2f3d). Updated the permissions matrix, RACI and role sections. Applied the official logo and navy brand colours. Replaced every screenshot and redrew the diagrams."],
    ], [1.6, 2.4, 2.8, 10.2], title="Revision history")
    table(["Role", "Name", "Status"], [
        ["Author", "Priyam Singh", "Complete"],
        ["Clinical review (clinical lead)", "To be named by the programme", "Pending"],
        ["Technical review (system administrator)", "To be named by the programme", "Pending"],
        ["Approval for pilot use", "Programme sponsor", "Pending"],
    ], [6.0, 6.5, 4.5], title="Review and approval")
    para("How to read this document: Sections 1–3 explain the service and the end-to-end flow for everyone. Section 4 has one part per role; read your own role first. Sections 5–6 cover safety, governance, exceptions and support. Section 7 holds reference tables.", size=9.5, color=GREY)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- contents
    t = doc.add_paragraph(); r = t.add_run("Contents"); set_font(r, size=18, bold=True, color=NAVY)
    para_border(t, "bottom", MINT_HEX, 12, 4); t.paragraph_format.space_after = Pt(8)
    g["TOC_ANCHOR"] = doc.add_paragraph()
