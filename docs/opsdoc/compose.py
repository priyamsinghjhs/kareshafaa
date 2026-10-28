"""Compose document figures from build/shots into build/fig (laptop + phone pairs, crops)."""
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
S, F = HERE / "build" / "shots", HERE / "assets" / "fig"
F.mkdir(parents=True, exist_ok=True)
LINE, BG = (201, 211, 224), (255, 255, 255)


def load(name, max_h=None):
    im = Image.open(S / f"{name}.png").convert("RGB")
    if max_h and im.height > max_h:
        im = im.crop((0, 0, im.width, max_h))
    return im


def frame(im, radius, pad, bezel=(23, 32, 48)):
    """Device-like frame: dark bezel with rounded corners."""
    w, h = im.width + 2 * pad, im.height + 2 * pad
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    out.paste(Image.new("RGB", (w, h), bezel), (0, 0), m)
    inner = Image.new("L", im.size, 0); ImageDraw.Draw(inner).rounded_rectangle((0, 0, im.width - 1, im.height - 1), max(4, radius - pad), fill=255)
    out.paste(im, (pad, pad), inner)
    return out


def pair(desk, phone, out, desk_h=1200, phone_h=1688, gap=70):
    d = frame(load(desk, desk_h), 26, 18)
    p = load(phone, phone_h)
    p = p.resize((int(p.width * d.height / (p.height + 44)), int(p.height * d.height / (p.height + 44))))
    p = frame(p, 46, 22)
    W, H = d.width + gap + p.width, max(d.height, p.height)
    c = Image.new("RGB", (W + 40, H + 40), BG)
    c.paste(d, (20, 20 + (H - d.height) // 2), d); c.paste(p, (20 + d.width + gap, 20 + (H - p.height) // 2), p)
    c.save(F / f"{out}.png", optimize=True)


def phones(names, out, phone_h=1688, gap=60):
    ims = [frame(load(n, phone_h), 46, 22) for n in names]
    W = sum(i.width for i in ims) + gap * (len(ims) - 1); H = max(i.height for i in ims)
    c = Image.new("RGB", (W + 40, H + 40), BG); x = 20
    for i in ims:
        c.paste(i, (x, 20), i); x += i.width + gap
    c.save(F / f"{out}.png", optimize=True)


def single(name, out, max_h=None, border=True, scale=None):
    im = load(name, max_h)
    if scale: im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    if border:
        c = Image.new("RGB", (im.width + 4, im.height + 4), LINE); c.paste(im, (2, 2)); im = c
    im.save(F / f"{out}.png", optimize=True)


pair("desktop_login", "mobile_login", "login_pair")
pair("desktop_coord_queue", "mobile_coord_queue", "coord_queue_pair")
pair("desktop_gp_case", "mobile_spec_case", "clinician_case_pair", desk_h=1650)
pair("desktop_lead_overview", "mobile_lead_overview", "lead_overview_pair")
pair("desktop_admin_users", "mobile_admin_users", "admin_users_pair")
pair("desktop_analyst_dashboard", "mobile_analyst_dashboard", "analyst_pair", desk_h=1500)
phones(["mobile_patient_en", "mobile_patient_ar", "mobile_patient_hi"], "patient_phones")
phones(["mobile_lead_audit", "mobile_admin_audit"], "audit_phones")
phones(["mobile_gp_review", "mobile_spec_consults"], "clinician_lists")
single("desktop_coord_inbox", "coord_inbox", max_h=1000)
single("desktop_coord_case_step", "coord_case_step")
single("desktop_gp_case_step", "gp_case_step")
single("desktop_lead_models", "lead_models", max_h=1200)
single("desktop_lead_rules", "lead_rules", max_h=1200)
single("desktop_admin_advanced", "admin_advanced", max_h=1350)
single("desktop_translator_content", "translator_content", max_h=1200)
single("mobile_patient_locked_card", "patient_locked", border=True, scale=0.8)
# keep the document small: cap the width and reduce to a 256-colour palette (screenshots stay sharp)
for f in F.glob("*.png"):
    im = Image.open(f).convert("RGB")
    if im.width > 2200:
        im = im.resize((2200, im.height * 2200 // im.width), Image.LANCZOS)
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(f, optimize=True)
print(sorted(p.name for p in F.glob("*.png")))
